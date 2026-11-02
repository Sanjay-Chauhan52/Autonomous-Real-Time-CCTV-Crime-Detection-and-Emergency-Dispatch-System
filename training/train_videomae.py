"""
CrimeWatch — VideoMAE Fine-tuning Script
=========================================
Model : MCG-NJU/videomae-base-finetuned-kinetics (HuggingFace)
        → VideoMAE-Base pretrained on Kinetics-400 (1.3M clips)
Task  : 7-class crime classification
        Fighting | Assault | Robbery | Shooting | Shoplifting | Burglary | Normal

Usage
-----
Run from the project root (Crime_detection/):

    C:\\Users\\thesa\\crime_detection\\crime\\Scripts\\python.exe -m training.train_videomae

Outputs
-------
    models/videomae_crime.pt        ← best checkpoint (state_dict)
    models/videomae_crime_full/     ← HuggingFace-format checkpoint (for inference)
    outputs/videomae_report.md      ← training & evaluation summary
"""

import os, sys, glob, random, math, time, json
from pathlib import Path
from typing import List, Tuple, Dict, Optional

# ── Make sure we can import project modules ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Safe UTF-8 encoding on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torch.cuda.amp import GradScaler, autocast
import cv2

try:
    from transformers import (
        VideoMAEForVideoClassification,
        VideoMAEImageProcessor,
    )
except ImportError:
    print("[ERROR] transformers not installed. Run:")
    print("  pip install transformers accelerate")
    sys.exit(1)

try:
    from decord import VideoReader, cpu
    HAS_DECORD = True
except ImportError:
    HAS_DECORD = False

# ── Config ──────────────────────────────────────────────────────────────────
CLASSES      = ["Assault", "Burglary", "Fighting", "Normal", "Robbery", "Shooting", "Shoplifting"]
NUM_CLASSES  = len(CLASSES)
LABEL2ID     = {c: i for i, c in enumerate(CLASSES)}
ID2LABEL     = {i: c for i, c in enumerate(CLASSES)}

DATA_DIR     = PROJECT_ROOT / "data_prep" / "raw_videos"
MODEL_DIR    = PROJECT_ROOT / "models"
OUT_DIR      = PROJECT_ROOT / "outputs"
MODEL_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

LOCAL_BASE   = MODEL_DIR / "videomae_base"
PRETRAINED   = str(LOCAL_BASE) if (LOCAL_BASE / "model.safetensors").exists() or (LOCAL_BASE / "pytorch_model.bin").exists() else "MCG-NJU/videomae-base-finetuned-kinetics"
NUM_FRAMES   = 16           # frames per clip (VideoMAE default)
FRAME_SIZE   = 224          # VideoMAE input size
CLIPS_PER_VIDEO_TRAIN = 4   # sample multiple clips per training video
CLIPS_PER_VIDEO_VAL   = 2

EPOCHS       = 8
LR           = 3e-5
WEIGHT_DECAY = 1e-4
BATCH_SIZE   = 2            # small — 4 GB VRAM with fp16
GRAD_ACCUM   = 4            # effective batch = 8
TRAIN_RATIO  = 0.75
VAL_RATIO    = 0.15
# TEST_RATIO  = 0.10 (remainder)

SEED         = 42
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
DEVICE       = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[INFO] Using device: {DEVICE}", flush=True)
if DEVICE.type == "cuda":
    print(f"[INFO] GPU: {torch.cuda.get_device_name(0)}  VRAM: "
          f"{torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB", flush=True)


# ── 1. Video utilities ────────────────────────────────────────────────────────

def read_video_frames(video_path: str, num_frames: int = NUM_FRAMES,
                      start_ratio: float = 0.0) -> Optional[np.ndarray]:
    """
    Read `num_frames` uniformly-sampled RGB frames from a video starting at
    `start_ratio` * total_duration. Returns array (T, H, W, 3) or None.
    Uses decord if available for 50x faster frame fetching.
    """
    if HAS_DECORD:
        try:
            vr = VideoReader(video_path, ctx=cpu(0), width=FRAME_SIZE, height=FRAME_SIZE)
            total = len(vr)
            if total >= num_frames:
                start_f = int(total * start_ratio)
                end_f   = min(total - 1, start_f + max(num_frames * 4, total // 2))
                indices = np.linspace(start_f, end_f, num_frames, dtype=int)
                indices = np.clip(indices, 0, total - 1)
                frames = vr.get_batch(indices).asnumpy()  # (T, H, W, 3) RGB uint8
                return frames
        except Exception:
            pass

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < num_frames:
        cap.release(); return None

    start_f = int(total * start_ratio)
    end_f   = min(total - 1, start_f + max(num_frames * 4, total // 2))
    indices = np.linspace(start_f, end_f, num_frames, dtype=int)
    indices = np.clip(indices, 0, total - 1)

    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = cv2.resize(frame, (FRAME_SIZE, FRAME_SIZE))
        frames.append(frame)
    cap.release()

    if len(frames) < num_frames:
        while len(frames) < num_frames:
            frames.append(frames[-1] if frames else np.zeros((FRAME_SIZE, FRAME_SIZE, 3), dtype=np.uint8))

    return np.stack(frames, axis=0)   # (T, H, W, 3)


def augment_frames(frames: np.ndarray) -> np.ndarray:
    """
    Simple spatial/temporal augmentation for small-dataset training.
    Input: (T, H, W, 3) numpy uint8
    """
    T, H, W, C = frames.shape

    # Random horizontal flip
    if random.random() < 0.5:
        frames = frames[:, :, ::-1, :].copy()

    # Random brightness/contrast jitter
    alpha = random.uniform(0.8, 1.2)   # contrast
    beta  = random.uniform(-15, 15)    # brightness
    frames = np.clip(frames.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)

    # Random temporal reversal
    if random.random() < 0.3:
        frames = frames[::-1, :, :, :].copy()

    # Random crop (90-100% of frame)
    crop_ratio = random.uniform(0.88, 1.0)
    ch, cw = int(H * crop_ratio), int(W * crop_ratio)
    y0 = random.randint(0, H - ch)
    x0 = random.randint(0, W - cw)
    frames = frames[:, y0:y0+ch, x0:x0+cw, :]
    frames = np.stack([cv2.resize(f, (W, H)) for f in frames], axis=0)

    return frames


# ── 2. Dataset ────────────────────────────────────────────────────────────────

class CrimeVideoDataset(Dataset):
    """
    Each item is a list of PIL-like RGB frames (numpy) + label.
    The VideoMAE processor handles normalization and patching.
    """

    def __init__(self, samples: List[Tuple[str, int]], processor,
                 clips_per_video: int = 1, augment: bool = False):
        self.samples         = samples           # [(path, label_id), ...]
        self.processor       = processor
        self.clips_per_video = clips_per_video
        self.augment         = augment
        # Expand: each video → clips_per_video items
        self._items = []
        for path, label in samples:
            starts = np.linspace(0.0, 0.85, clips_per_video)
            for s in starts:
                self._items.append((path, label, float(s)))

    def __len__(self):
        return len(self._items)

    def __getitem__(self, idx):
        path, label, start = self._items[idx]
        frames = read_video_frames(path, NUM_FRAMES, start)

        if frames is None:
            # Return a black clip on failure
            frames = np.zeros((NUM_FRAMES, FRAME_SIZE, FRAME_SIZE, 3), dtype=np.uint8)

        if self.augment:
            frames = augment_frames(frames)

        # Convert to list of PIL-like (processor wants list of frames as numpy H×W×3)
        frame_list = [frames[t] for t in range(NUM_FRAMES)]

        inputs = self.processor(
            images=frame_list,
            return_tensors="pt",
        )
        # pixel_values: (1, T, C, H, W) → squeeze batch dim
        pixel_values = inputs["pixel_values"].squeeze(0)  # (T, C, H, W) or (C, T, H, W)

        return pixel_values, torch.tensor(label, dtype=torch.long)


# ── 3. Build train / val / test splits ───────────────────────────────────────

def build_splits() -> Tuple[List, List, List]:
    all_samples = []
    for cls in CLASSES:
        vids = sorted(glob.glob(str(DATA_DIR / cls / "*.mp4")))
        if not vids:
            print(f"[WARN] No videos found for class: {cls}")
            continue
        for v in vids:
            all_samples.append((v, LABEL2ID[cls]))

    # Per-class stratified split
    from collections import defaultdict
    by_class: Dict[int, list] = defaultdict(list)
    for s in all_samples:
        by_class[s[1]].append(s)

    train_s, val_s, test_s = [], [], []
    for label_id, samps in by_class.items():
        random.shuffle(samps)
        n = len(samps)
        n_train = max(1, int(n * TRAIN_RATIO))
        n_val   = max(1, int(n * VAL_RATIO))
        train_s.extend(samps[:n_train])
        val_s.extend(samps[n_train:n_train + n_val])
        test_s.extend(samps[n_train + n_val:])

    print(f"[INFO] Split sizes — Train: {len(train_s)}  Val: {len(val_s)}  Test: {len(test_s)}")
    return train_s, val_s, test_s


# ── 4. Load model & processor ────────────────────────────────────────────────

def load_model_and_processor():
    print(f"[INFO] Loading VideoMAE from: {PRETRAINED}")
    print("[INFO] First run downloads ~344 MB from HuggingFace — please wait...")

    processor = VideoMAEImageProcessor.from_pretrained(PRETRAINED)

    model = VideoMAEForVideoClassification.from_pretrained(
        PRETRAINED,
        num_labels=NUM_CLASSES,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
        ignore_mismatched_sizes=True,   # replaces the Kinetics-400 head with 7-class head
    )
    print(f"[INFO] Model parameters: {sum(p.numel() for p in model.parameters()) / 1e6:.1f} M")
    return model, processor


# ── 5. Training loop ─────────────────────────────────────────────────────────

def train_epoch(model, loader, optimizer, scaler, grad_accum):
    model.train()
    total_loss, correct, n = 0.0, 0, 0
    optimizer.zero_grad()

    for step, (pixels, labels) in enumerate(loader):
        pixels = pixels.to(DEVICE)
        labels = labels.to(DEVICE)

        with autocast():
            outputs = model(pixel_values=pixels, labels=labels)
            loss    = outputs.loss / grad_accum

        scaler.scale(loss).backward()

        if (step + 1) % grad_accum == 0:
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad()

        total_loss += loss.item() * grad_accum
        preds = outputs.logits.argmax(dim=-1)
        correct += (preds == labels).sum().item()
        n       += labels.size(0)

        if (step + 1) % 20 == 0 or (step + 1) == len(loader):
            print(f"  step {step+1}/{len(loader)}  loss={total_loss/(step+1):.4f}  "
                  f"acc={correct/max(n,1):.3f}", flush=True)

    return total_loss / len(loader), correct / max(n, 1)


@torch.no_grad()
def eval_epoch(model, loader):
    model.eval()
    total_loss, correct, n = 0.0, 0, 0

    for pixels, labels in loader:
        pixels = pixels.to(DEVICE)
        labels = labels.to(DEVICE)

        with autocast():
            outputs = model(pixel_values=pixels, labels=labels)

        total_loss += outputs.loss.item()
        preds = outputs.logits.argmax(dim=-1)
        correct += (preds == labels).sum().item()
        n       += labels.size(0)

    return total_loss / max(len(loader), 1), correct / max(n, 1)


@torch.no_grad()
def evaluate_test(model, loader):
    """Full per-class report on test set."""
    model.eval()
    all_preds, all_labels = [], []

    for pixels, labels in loader:
        pixels = pixels.to(DEVICE)
        with autocast():
            outputs = model(pixel_values=pixels)
        preds = outputs.logits.argmax(dim=-1)
        all_preds.extend(preds.cpu().tolist())
        all_labels.extend(labels.tolist())

    # Accuracy
    correct = sum(p == l for p, l in zip(all_preds, all_labels))
    acc = correct / max(len(all_labels), 1)

    # Per-class
    from collections import defaultdict
    cls_correct = defaultdict(int)
    cls_total   = defaultdict(int)
    for p, l in zip(all_preds, all_labels):
        cls_total[l]   += 1
        cls_correct[l] += int(p == l)

    print(f"\n{'='*50}")
    print(f"  TEST ACCURACY: {acc*100:.2f}%  ({correct}/{len(all_labels)})")
    print(f"{'='*50}")
    for i, cls in enumerate(CLASSES):
        t = cls_total[i]
        c = cls_correct[i]
        bar = '#' * int(c/max(t,1) * 20)
        print(f"  {cls:12s}: {c}/{t}  {bar}", flush=True)
    print(f"{'='*50}\n", flush=True)

    return acc, all_preds, all_labels


# ── 6. Main ──────────────────────────────────────────────────────────────────

def main():
    t_start = time.time()

    # Splits
    train_s, val_s, test_s = build_splits()

    # Model
    model, processor = load_model_and_processor()
    model = model.to(DEVICE)

    # ── Freeze backbone, only train the head for the first few epochs ──
    # This speeds up early training and prevents catastrophic forgetting
    # on a tiny dataset.
    def set_backbone_frozen(frozen: bool):
        for name, p in model.named_parameters():
            if "classifier" not in name:
                p.requires_grad = not frozen

    set_backbone_frozen(True)   # phase 1: head-only
    print("[INFO] Phase 1: training classification head only...")

    # Datasets
    train_ds = CrimeVideoDataset(train_s, processor, CLIPS_PER_VIDEO_TRAIN, augment=True)
    val_ds   = CrimeVideoDataset(val_s,   processor, CLIPS_PER_VIDEO_VAL,   augment=False)
    test_ds  = CrimeVideoDataset(test_s,  processor, CLIPS_PER_VIDEO_VAL,   augment=False)

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                              num_workers=0, pin_memory=True, drop_last=True)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=0, pin_memory=True)
    test_loader  = DataLoader(test_ds,  batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=0, pin_memory=True)

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=LR * 10, weight_decay=WEIGHT_DECAY  # higher LR for head-only phase
    )
    scaler = GradScaler()
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)

    best_val_acc = -1.0
    history = []
    PHASE2_EPOCH = 3   # unfreeze backbone after this epoch

    for epoch in range(1, EPOCHS + 1):
        # Phase transition: unfreeze backbone at PHASE2_EPOCH
        if epoch == PHASE2_EPOCH + 1:
            set_backbone_frozen(False)
            # Rebuild optimizer with lower LR for backbone
            optimizer = torch.optim.AdamW([
                {"params": [p for n, p in model.named_parameters() if "classifier" not in n],
                 "lr": LR},
                {"params": [p for n, p in model.named_parameters() if "classifier" in n],
                 "lr": LR * 5},
            ], weight_decay=WEIGHT_DECAY)
            scaler = GradScaler()
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, T_max=EPOCHS - PHASE2_EPOCH
            )
            print(f"[INFO] Phase 2: full fine-tuning from epoch {epoch}...", flush=True)

        print(f"\n[Epoch {epoch}/{EPOCHS}]", flush=True)
        tr_loss, tr_acc = train_epoch(model, train_loader, optimizer, scaler, GRAD_ACCUM)
        vl_loss, vl_acc = eval_epoch(model, val_loader)
        scheduler.step()

        lr_now = optimizer.param_groups[0]["lr"]
        print(f"  train loss={tr_loss:.4f}  acc={tr_acc:.3f}  |  "
              f"val loss={vl_loss:.4f}  acc={vl_acc:.3f}  |  lr={lr_now:.2e}", flush=True)

        history.append({"epoch": epoch, "train_acc": tr_acc, "val_acc": vl_acc,
                        "train_loss": tr_loss, "val_loss": vl_loss})

        # Save best checkpoint
        if vl_acc > best_val_acc:
            best_val_acc = vl_acc
            ckpt_path = MODEL_DIR / "videomae_crime.pt"
            torch.save({
                "epoch": epoch,
                "val_acc": vl_acc,
                "model_state_dict": model.state_dict(),
                "id2label": ID2LABEL,
                "label2id": LABEL2ID,
                "pretrained_base": PRETRAINED,
            }, ckpt_path)
            print(f"  [BEST] Model saved -> {ckpt_path}  (val_acc={vl_acc:.4f})", flush=True)

    # ── Test set evaluation ──────────────────────────────────────────────────
    print("\n[INFO] Loading best checkpoint for test evaluation...", flush=True)
    ckpt = torch.load(MODEL_DIR / "videomae_crime.pt", map_location=DEVICE)
    model.load_state_dict(ckpt["model_state_dict"])

    test_acc, test_preds, test_labels = evaluate_test(model, test_loader)

    # Save HuggingFace-compatible checkpoint for easy inference
    hf_dir = MODEL_DIR / "videomae_crime_full"
    model.save_pretrained(str(hf_dir))
    processor.save_pretrained(str(hf_dir))
    print(f"[INFO] Full HuggingFace checkpoint saved -> {hf_dir}/", flush=True)

    # ── Write report ─────────────────────────────────────────────────────────
    elapsed = (time.time() - t_start) / 60
    report = f"""# CrimeWatch — VideoMAE Fine-tuning Report

**Model**: {PRETRAINED}  
**Parameters**: ~86 M  
**Training Time**: {elapsed:.1f} minutes  
**Device**: {DEVICE} ({torch.cuda.get_device_name(0) if DEVICE.type == 'cuda' else 'CPU'})

## Results

| Split | Accuracy |
|---|---|
| Train (best epoch) | {max(h["train_acc"] for h in history)*100:.2f}% |
| Validation (best)  | {best_val_acc*100:.2f}% |
| **Test**           | **{test_acc*100:.2f}%** |

## Training History

| Epoch | Train Acc | Val Acc | Train Loss | Val Loss |
|---|---|---|---|---|
"""
    for h in history:
        report += f"| {h['epoch']} | {h['train_acc']*100:.1f}% | {h['val_acc']*100:.1f}% | {h['train_loss']:.4f} | {h['val_loss']:.4f} |\n"

    report += f"""
## Checkpoints

- **PyTorch state_dict**: `models/videomae_crime.pt`
- **HuggingFace format**: `models/videomae_crime_full/`

## Inference

Run:
```bash
python -m backend.inference_videomae path/to/video.mp4
```
"""
    report_path = OUT_DIR / "videomae_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[INFO] Report saved → {report_path}")
    print(f"\n[DONE] Total training time: {elapsed:.1f} min")
    print(f"[DONE] Best val accuracy: {best_val_acc*100:.2f}%")
    print(f"[DONE] Test accuracy:     {test_acc*100:.2f}%")


if __name__ == "__main__":
    main()
