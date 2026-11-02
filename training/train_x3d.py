"""
train_x3d.py
------------
Trains a PyTorchVideo X3D-M model pretrained on Kinetics-400 for 7-class CCTV crime detection.

Features:
- Loads X3D-M from torch.hub (or pytorchvideo) and replaces the classification head for 7 classes.
- Decord-based VideoDataset loading 16-frame clips directly from train/val CSV indices at 182x182.
- Light augmentations for training: random horizontal flip and brightness jitter.
- Mixed precision training: fp16 autocast + GradScaler.
- Backbone freezing: freeze blocks 0-4 for first 3 epochs, then unfreeze all layers.
- Class-weighted CrossEntropyLoss to address class imbalance.
- Early stopping based on validation accuracy; saves best checkpoint to models/x3d_crime.pt.
- Logs epoch metrics (train_loss, train_acc, val_loss, val_acc) to outputs/training_log.csv.
- Catches CUDA OOM errors with actionable guidance.

Usage:
  # Smoke test (1 epoch on a small subset):
  python training/train_x3d.py --smoke_test

  # Full training (15 epochs, batch size 2):
  python training/train_x3d.py --epochs 15 --batch_size 2
"""

import argparse
import csv
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
import decord
from decord import VideoReader, cpu

# Suppress verbose decord bridge messages
decord.bridge.set_bridge("native")

# Normalization constants (Kinetics-400 standard)
MEAN = [0.45, 0.45, 0.45]
STD = [0.225, 0.225, 0.225]


# -------------------------------------------------------------------------------
# Dataset
# -------------------------------------------------------------------------------

class CrimeClipDataset(Dataset):
    """
    Dataset that loads 16-frame video clips using Decord on-the-fly.
    """
    def __init__(
        self,
        csv_file: Path,
        root_dir: Path,
        num_frames: int = 16,
        frame_size: int = 182,
        is_train: bool = False,
    ):
        self.root_dir = root_dir
        self.num_frames = num_frames
        self.frame_size = frame_size
        self.is_train = is_train

        self.records = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.records.append(row)

        self.mean = torch.tensor(MEAN, dtype=torch.float32).view(3, 1, 1, 1)
        self.std = torch.tensor(STD, dtype=torch.float32).view(3, 1, 1, 1)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        row = self.records[idx]
        rel_path = row["video_path"]
        start_frame = int(row["start_frame"])
        label_idx = int(row["label_idx"])

        video_path = self.root_dir / rel_path
        if not video_path.exists():
            # Fallback check directly in raw_videos
            video_path = self.root_dir / "data_prep" / rel_path

        try:
            vr = VideoReader(
                str(video_path),
                ctx=cpu(0),
                width=self.frame_size,
                height=self.frame_size,
            )
            total_frames = len(vr)
            indices = [min(start_frame + i, total_frames - 1) for i in range(self.num_frames)]
            batch = vr.get_batch(indices).asnumpy()  # Shape: (T, H, W, C), uint8
        except Exception as e:
            # Fallback zero tensor if video decoding fails
            print(f"[WARN] Failed to read clip {video_path} at frame {start_frame}: {e}", file=sys.stderr)
            tensor = torch.zeros((3, self.num_frames, self.frame_size, self.frame_size), dtype=torch.float32)
            return tensor, label_idx

        # Convert to FloatTensor in [0, 1]: (T, H, W, C) -> (C, T, H, W)
        tensor = torch.from_numpy(batch).float() / 255.0
        tensor = tensor.permute(3, 0, 1, 2)

        # Light training augmentations (temporally consistent)
        if self.is_train:
            # 1. Random horizontal flip
            if random.random() < 0.5:
                tensor = torch.flip(tensor, dims=[3])

            # 2. Random brightness jitter (+/- 20%)
            brightness_factor = random.uniform(0.8, 1.2)
            tensor = torch.clamp(tensor * brightness_factor, 0.0, 1.0)

        # Normalize with Kinetics mean and std
        tensor = (tensor - self.mean) / self.std
        return tensor, label_idx


# -------------------------------------------------------------------------------
# Model Builder
# -------------------------------------------------------------------------------

def build_x3d_model(num_classes: int = 7) -> nn.Module:
    """
    Builds X3D-M pretrained on Kinetics-400 and adapts the final head for num_classes.
    Replaces AvgPool3d with AdaptiveAvgPool3d((1,1,1)) so arbitrary spatial resolutions
    (e.g. 182x182) can be accepted without shape mismatch errors.
    """
    try:
        model = torch.hub.load("facebookresearch/pytorchvideo", "x3d_m", pretrained=True)
    except Exception as e:
        print(f"[INFO] torch.hub load fallback to pytorchvideo library ({e})")
        import pytorchvideo.models.hub as ptv_hub
        model = ptv_hub.x3d_m(pretrained=True)

    head = model.blocks[-1]
    in_features = head.proj.in_features

    # Replace fixed pool with adaptive pool to allow 182x182 resolution
    head.pool.pool = nn.AdaptiveAvgPool3d((1, 1, 1))

    # Replace linear projection for target classes
    head.proj = nn.Linear(in_features, num_classes)

    # Disable softmax activation so model outputs raw logits for CrossEntropyLoss
    head.activation = None

    return model


def set_backbone_frozen(model: nn.Module, frozen: bool) -> None:
    """
    Freeze or unfreeze the backbone (blocks 0 to 4).
    The classification head (block 5) always requires grad.
    """
    for idx, block in enumerate(model.blocks):
        is_head = (idx == len(model.blocks) - 1)
        requires_grad = True if is_head else not frozen
        for param in block.parameters():
            param.requires_grad = requires_grad


# -------------------------------------------------------------------------------
# Class Weights Computation
# -------------------------------------------------------------------------------

def compute_class_weights(csv_file: Path, num_classes: int = 7) -> torch.Tensor:
    """Computes balanced inverse-frequency class weights from training CSV."""
    counts = [0] * num_classes
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            counts[int(row["label_idx"])] += 1

    total = sum(counts)
    weights = []
    for c in counts:
        if c > 0:
            w = total / (num_classes * c)
        else:
            w = 1.0
        weights.append(w)

    w_tensor = torch.tensor(weights, dtype=torch.float32)
    # Normalize weights so sum equals num_classes
    w_tensor = w_tensor / w_tensor.sum() * num_classes
    return w_tensor


# -------------------------------------------------------------------------------
# Training & Validation Loops
# -------------------------------------------------------------------------------

def run_one_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    criterion: nn.Module,
    optimizer: Optional[torch.optim.Optimizer],
    scaler: Optional[torch.amp.GradScaler],
    device: torch.device,
    is_train: bool,
    use_amp: bool = True,
    limit_batches: Optional[int] = None,
) -> tuple[float, float]:
    """
    Runs one epoch of training or validation.
    Returns: (average_loss, accuracy_percentage)
    """
    if is_train:
        model.train()
    else:
        model.eval()

    total_loss = 0.0
    correct = 0
    total_samples = 0

    batch_count = 0
    total_batches = min(len(dataloader), limit_batches) if limit_batches else len(dataloader)

    for inputs, labels in dataloader:
        batch_count += 1
        inputs = inputs.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)

        if is_train:
            optimizer.zero_grad(set_to_none=True)

            if use_amp and device.type == "cuda":
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    outputs = model(inputs)
                    loss = criterion(outputs, labels)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                outputs = model(inputs)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()
        else:
            with torch.no_grad():
                if use_amp and device.type == "cuda":
                    with torch.amp.autocast("cuda", dtype=torch.float16):
                        outputs = model(inputs)
                        loss = criterion(outputs, labels)
                else:
                    outputs = model(inputs)
                    loss = criterion(outputs, labels)

        total_loss += loss.item() * inputs.size(0)
        preds = torch.argmax(outputs, dim=1)
        correct += (preds == labels).sum().item()
        total_samples += inputs.size(0)

        # Inline progress reporting
        if batch_count % 15 == 0 or batch_count == total_batches:
            running_acc = 100.0 * correct / max(1, total_samples)
            mode_str = "Train" if is_train else "Val"
            print(
                f"  [{mode_str}] Batch {batch_count:>3}/{total_batches} | "
                f"Loss: {loss.item():.4f} | Running Acc: {running_acc:.2f}%",
                end="\r",
                flush=True,
            )

        if limit_batches and batch_count >= limit_batches:
            break

    print()  # newline after inline progress
    avg_loss = total_loss / max(1, total_samples)
    accuracy = 100.0 * correct / max(1, total_samples)
    return avg_loss, accuracy


# -------------------------------------------------------------------------------
# Main Training Orchestrator
# -------------------------------------------------------------------------------

def train(args):
    # Setup directories
    repo_root = Path(__file__).resolve().parent.parent
    processed_dir = repo_root / "data_prep" / "processed"
    models_dir = repo_root / "models"
    outputs_dir = repo_root / "outputs"
    models_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    train_csv = processed_dir / "train_clips.csv"
    val_csv = processed_dir / "val_clips.csv"
    mapping_json = processed_dir / "class_mapping.json"

    if not train_csv.exists() or not val_csv.exists():
        print(f"[ERROR] Processed CSV files not found in {processed_dir}. Run prepare_clips.py first!", file=sys.stderr)
        sys.exit(1)

    # Load classes
    with open(mapping_json, "r", encoding="utf-8") as f:
        meta = json.load(f)
        classes = [meta["idx_to_class"][str(i)] for i in range(len(meta["idx_to_class"]))]
    num_classes = len(classes)

    # Device configuration
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("=" * 60)
    print("  CrimeWatch -- X3D-M Crime Detection Model Training")
    print(f"  Device        : {device.type.upper()}" + (f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else ""))
    print(f"  Classes ({num_classes})  : {', '.join(classes)}")
    print(f"  Batch size    : {args.batch_size}")
    print(f"  Target epochs : {args.epochs}")
    print(f"  Freeze epochs : {args.freeze_epochs}")
    print(f"  Learning rate : {args.lr}")
    print(f"  Smoke test    : {args.smoke_test}")
    print("=" * 60 + "\n")

    # Datasets & Loaders
    train_dataset = CrimeClipDataset(
        csv_file=train_csv,
        root_dir=repo_root,
        num_frames=args.num_frames,
        frame_size=args.frame_size,
        is_train=True,
    )
    val_dataset = CrimeClipDataset(
        csv_file=val_csv,
        root_dir=repo_root,
        num_frames=args.num_frames,
        frame_size=args.frame_size,
        is_train=False,
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
        drop_last=False,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
        drop_last=False,
    )

    print(f"[INFO] Loaded {len(train_dataset)} training clips, {len(val_dataset)} validation clips.")

    # Model & Head Replacement
    print("[INFO] Initializing X3D-M model...")
    model = build_x3d_model(num_classes=num_classes)
    model = model.to(device)

    # Class-weighted loss
    class_weights = compute_class_weights(train_csv, num_classes=num_classes).to(device)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    print(f"[INFO] Balanced class weights: {[round(w, 2) for w in class_weights.tolist()]}")

    # Optimizer & Scaler
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-2)
    scaler = torch.amp.GradScaler("cuda", enabled=(device.type == "cuda"))

    # Logging CSV setup
    log_csv_path = outputs_dir / "training_log.csv"
    with open(log_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "learning_rate", "time_sec"])

    # Training state
    best_val_acc = 0.0
    patience_counter = 0
    save_path = models_dir / "x3d_crime.pt"
    total_start_time = time.time()

    # Limit batches if smoke test
    limit_train = 15 if args.smoke_test else None
    limit_val = 8 if args.smoke_test else None
    total_epochs = 1 if args.smoke_test else args.epochs

    try:
        for epoch in range(1, total_epochs + 1):
            epoch_start = time.time()

            # Backbone freezing schedule
            if epoch <= args.freeze_epochs and not args.smoke_test:
                set_backbone_frozen(model, frozen=True)
                stage_str = "Backbone FROZEN (Head only)"
            else:
                set_backbone_frozen(model, frozen=False)
                stage_str = "All layers UNFROZEN"

            print(f"\n--- Epoch {epoch}/{total_epochs} [{stage_str}] ---")

            # Train
            train_loss, train_acc = run_one_epoch(
                model=model,
                dataloader=train_loader,
                criterion=criterion,
                optimizer=optimizer,
                scaler=scaler,
                device=device,
                is_train=True,
                use_amp=(device.type == "cuda"),
                limit_batches=limit_train,
            )

            # Validation
            val_loss, val_acc = run_one_epoch(
                model=model,
                dataloader=val_loader,
                criterion=criterion,
                optimizer=None,
                scaler=scaler,
                device=device,
                is_train=False,
                use_amp=(device.type == "cuda"),
                limit_batches=limit_val,
            )

            epoch_time = time.time() - epoch_start
            current_lr = optimizer.param_groups[0]["lr"]

            # Log to CSV
            with open(log_csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([epoch, f"{train_loss:.4f}", f"{train_acc:.2f}", f"{val_loss:.4f}", f"{val_acc:.2f}", f"{current_lr:.6f}", f"{epoch_time:.1f}"])

            print(
                f"[Summary] Epoch {epoch:02d} | "
                f"Train Loss: {train_loss:.4f} | Train Acc: {train_acc:.2f}% | "
                f"Val Loss: {val_loss:.4f} | Val Acc: {val_acc:.2f}% | "
                f"Time: {epoch_time:.1f}s"
            )

            # Checkpoint best model
            if val_acc > best_val_acc or epoch == 1:
                improved = val_acc > best_val_acc
                best_val_acc = max(best_val_acc, val_acc)
                patience_counter = 0

                checkpoint = {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "best_val_acc": best_val_acc,
                    "classes": classes,
                    "class_to_idx": meta["class_to_idx"],
                    "preprocessing": {
                        "num_frames": args.num_frames,
                        "frame_size": args.frame_size,
                        "mean": MEAN,
                        "std": STD,
                    },
                }
                torch.save(checkpoint, save_path)
                status_msg = "Saved NEW BEST checkpoint" if improved else "Initial checkpoint saved"
                print(f"  -> {status_msg} -> {save_path.name} (Val Acc: {best_val_acc:.2f}%)")
            else:
                patience_counter += 1
                print(f"  -> No improvement in val acc for {patience_counter}/{args.patience} epochs.")
                if patience_counter >= args.patience:
                    print(f"\n[INFO] Early stopping triggered at epoch {epoch} (best val acc: {best_val_acc:.2f}%).")
                    break

    except torch.cuda.OutOfMemoryError as oom:
        print("\n" + "!" * 60, file=sys.stderr)
        print("[CUDA OOM ERROR] GPU ran out of memory!", file=sys.stderr)
        print("To fix this on your 4GB VRAM GPU:", file=sys.stderr)
        print("  1. Reduce batch size to 1:", file=sys.stderr)
        print("       python training/train_x3d.py --batch_size 1", file=sys.stderr)
        print("  2. If still high, reduce frame size or clip frames:", file=sys.stderr)
        print("       python training/train_x3d.py --batch_size 1 --frame_size 160", file=sys.stderr)
        print("  3. Ensure other GPU processes are closed.", file=sys.stderr)
        print("!" * 60 + "\n", file=sys.stderr)
        sys.exit(1)

    total_time = time.time() - total_start_time
    print("\n" + "=" * 60)
    print(f"  Training Completed in {total_time / 60:.2f} minutes.")
    print(f"  Best Validation Accuracy : {best_val_acc:.2f}%")
    print(f"  Checkpoint Location      : {save_path}")
    print(f"  Training Log             : {log_csv_path}")
    print("=" * 60 + "\n")


# -------------------------------------------------------------------------------
# CLI Entrypoint
# -------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Train X3D-M for CCTV Crime Detection")
    parser.add_argument("--epochs", type=int, default=15, help="Number of training epochs (default: 15)")
    parser.add_argument("--batch_size", type=int, default=2, help="Batch size (default: 2; fallback 1 for low VRAM)")
    parser.add_argument("--freeze_epochs", type=int, default=3, help="Epochs to freeze backbone (default: 3)")
    parser.add_argument("--lr", type=float, default=1e-4, help="Learning rate (default: 1e-4)")
    parser.add_argument("--patience", type=int, default=5, help="Early stopping patience (default: 5)")
    parser.add_argument("--num_frames", type=int, default=16, help="Frames per clip (default: 16)")
    parser.add_argument("--frame_size", type=int, default=182, help="Frame height/width (default: 182)")
    parser.add_argument("--num_workers", type=int, default=0, help="DataLoader worker processes (default: 0 on Windows)")
    parser.add_argument("--smoke_test", action="store_true", help="Run 1-epoch quick smoke test")

    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
