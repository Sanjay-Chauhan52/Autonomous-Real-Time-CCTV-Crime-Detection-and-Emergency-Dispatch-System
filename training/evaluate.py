"""
evaluate.py
-----------
Evaluates the trained X3D-M model on the strictly held-out test split.

Outputs:
1. Overall accuracy, macro/weighted metrics, and per-class Precision, Recall, F1.
2. Confusion matrix plot saved to outputs/confusion_matrix.png.
3. Training curves plot saved to outputs/training_curves.png from outputs/training_log.csv.
4. Comprehensive, honest performance report saved to outputs/report.md.

Usage:
  python training/evaluate.py
  python training/evaluate.py --checkpoint models/x3d_crime.pt --test_csv data_prep/processed/test_clips.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
)
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
import decord
from decord import VideoReader, cpu

# Suppress decord bridge logging
decord.bridge.set_bridge("native")


# -------------------------------------------------------------------------------
# Dataset
# -------------------------------------------------------------------------------

class TestClipDataset(Dataset):
    """Loads 16-frame test clips with Decord on-the-fly."""
    def __init__(
        self,
        csv_file: Path,
        root_dir: Path,
        num_frames: int = 16,
        frame_size: int = 182,
        mean: list = [0.45, 0.45, 0.45],
        std: list = [0.225, 0.225, 0.225],
    ):
        self.root_dir = root_dir
        self.num_frames = num_frames
        self.frame_size = frame_size

        self.records = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.records.append(row)

        self.mean = torch.tensor(mean, dtype=torch.float32).view(3, 1, 1, 1)
        self.std = torch.tensor(std, dtype=torch.float32).view(3, 1, 1, 1)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int, str]:
        row = self.records[idx]
        rel_path = row["video_path"]
        start_frame = int(row["start_frame"])
        label_idx = int(row["label_idx"])

        video_path = self.root_dir / rel_path
        if not video_path.exists():
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
            batch = vr.get_batch(indices).asnumpy()  # (T, H, W, C), uint8
        except Exception as e:
            print(f"[WARN] Failed to read {video_path} at frame {start_frame}: {e}", file=sys.stderr)
            tensor = torch.zeros((3, self.num_frames, self.frame_size, self.frame_size), dtype=torch.float32)
            return tensor, label_idx, row["label"]

        # Float in [0, 1]: (T, H, W, C) -> (C, T, H, W)
        tensor = torch.from_numpy(batch).float() / 255.0
        tensor = tensor.permute(3, 0, 1, 2)
        tensor = (tensor - self.mean) / self.std
        return tensor, label_idx, row["label"]


# -------------------------------------------------------------------------------
# Model Loader
# -------------------------------------------------------------------------------

def load_eval_model(checkpoint_path: Path, device: torch.device):
    """Loads X3D-M architecture and checkpoint weights."""
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Checkpoint not found at: {checkpoint_path}")

    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    classes = ckpt.get("classes", [
        "Assault", "Burglary", "Fighting", "Normal", "Robbery", "Shooting", "Shoplifting"
    ])
    num_classes = len(classes)
    prep = ckpt.get("preprocessing", {
        "num_frames": 16,
        "frame_size": 182,
        "mean": [0.45, 0.45, 0.45],
        "std": [0.225, 0.225, 0.225],
    })

    try:
        model = torch.hub.load("facebookresearch/pytorchvideo", "x3d_m", pretrained=False)
    except Exception:
        import pytorchvideo.models.hub as ptv_hub
        model = ptv_hub.x3d_m(pretrained=False)

    head = model.blocks[-1]
    in_features = head.proj.in_features
    head.pool.pool = nn.AdaptiveAvgPool3d((1, 1, 1))
    head.proj = nn.Linear(in_features, num_classes)
    head.activation = None

    model.load_state_dict(ckpt["model_state_dict"])
    model.to(device)
    model.eval()

    return model, classes, prep, ckpt


# -------------------------------------------------------------------------------
# Evaluation Loop
# -------------------------------------------------------------------------------

def evaluate_test_split(model, dataloader, device):
    """Runs inference across all test clips without gradient calculation."""
    all_preds = []
    all_targets = []
    all_probs = []

    total_batches = len(dataloader)
    print(f"[INFO] Running evaluation across {len(dataloader.dataset)} test clips...", flush=True)

    with torch.no_grad():
        for i, (inputs, targets, _) in enumerate(dataloader, 1):
            inputs = inputs.to(device, non_blocking=True)
            outputs = model(inputs)  # raw logits (B, num_classes)
            probs = torch.softmax(outputs, dim=1).cpu().numpy()
            preds = np.argmax(probs, axis=1)

            all_preds.extend(preds.tolist())
            all_targets.extend(targets.tolist())
            all_probs.extend(probs.tolist())

            if i % 15 == 0 or i == total_batches:
                print(f"  Processed {i:>3}/{total_batches} batches...", flush=True)

    return np.array(all_targets), np.array(all_preds), np.array(all_probs)


# -------------------------------------------------------------------------------
# Plotting Helpers
# -------------------------------------------------------------------------------

def plot_confusion_matrix(y_true, y_pred, classes, output_path: Path):
    """Renders and saves a formatted confusion matrix heatmap."""
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(classes))))
    cm_norm = cm.astype("float") / np.maximum(cm.sum(axis=1, keepdims=True), 1e-6)

    fig, ax = plt.subplots(figsize=(8.5, 7.0))
    # Annotate with count and normalized percentage
    annot = np.empty_like(cm).astype(str)
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            annot[i, j] = f"{cm[i, j]}\n({cm_norm[i, j]*100:.1f}%)"

    sns.heatmap(
        cm,
        annot=annot,
        fmt="",
        cmap="Blues",
        cbar=True,
        xticklabels=classes,
        yticklabels=classes,
        ax=ax,
        linewidths=0.5,
        linecolor="gray",
    )
    ax.set_title("CrimeWatch X3D-M -- Test Set Confusion Matrix", fontsize=14, pad=12, fontweight="bold")
    ax.set_xlabel("Predicted Class", fontsize=12, labelpad=8)
    ax.set_ylabel("True Class", fontsize=12, labelpad=8)
    plt.xticks(rotation=45, ha="right", fontsize=10)
    plt.yticks(rotation=0, fontsize=10)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"[INFO] Saved confusion matrix to: {output_path}")


def plot_training_curves(log_csv: Path, output_path: Path):
    """Reads training_log.csv and generates training/validation curves."""
    if not log_csv.exists():
        print(f"[WARN] Log file {log_csv} not found, skipping training curves plot.")
        return

    epochs, train_loss, val_loss = [], [], []
    train_acc, val_acc = [], []

    with open(log_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            epochs.append(int(row["epoch"]))
            train_loss.append(float(row["train_loss"]))
            val_loss.append(float(row["val_loss"]))
            train_acc.append(float(row["train_acc"]))
            val_acc.append(float(row["val_acc"]))

    if not epochs:
        print("[WARN] Log CSV is empty, skipping curves.")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # Loss plot
    ax1.plot(epochs, train_loss, marker="o", color="#1f77b4", label="Train Loss", linewidth=2)
    ax1.plot(epochs, val_loss, marker="s", color="#ff7f0e", label="Val Loss", linewidth=2)
    ax1.set_title("Loss vs. Epoch", fontsize=12, fontweight="bold")
    ax1.set_xlabel("Epoch", fontsize=11)
    ax1.set_ylabel("CrossEntropy Loss", fontsize=11)
    ax1.grid(True, linestyle="--", alpha=0.6)
    ax1.legend(fontsize=10)
    ax1.set_xticks(epochs)

    # Accuracy plot
    ax2.plot(epochs, train_acc, marker="o", color="#2ca02c", label="Train Accuracy", linewidth=2)
    ax2.plot(epochs, val_acc, marker="s", color="#d62728", label="Val Accuracy", linewidth=2)
    ax2.set_title("Accuracy vs. Epoch", fontsize=12, fontweight="bold")
    ax2.set_xlabel("Epoch", fontsize=11)
    ax2.set_ylabel("Accuracy (%)", fontsize=11)
    ax2.grid(True, linestyle="--", alpha=0.6)
    ax2.legend(fontsize=10)
    ax2.set_xticks(epochs)

    plt.suptitle("CrimeWatch X3D-M Training & Validation Dynamics", fontsize=14, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"[INFO] Saved training curves to: {output_path}")


# -------------------------------------------------------------------------------
# Markdown Report Generator
# -------------------------------------------------------------------------------

def write_report_md(
    report_path: Path,
    overall_acc: float,
    macro_p: float,
    macro_r: float,
    macro_f1: float,
    weighted_f1: float,
    classes: list[str],
    precision: list[float],
    recall: list[float],
    f1: list[float],
    support: list[int],
    ckpt_meta: dict,
    total_clips: int,
):
    """Generates an honest, transparent evaluation report in Markdown."""
    best_epoch = ckpt_meta.get("epoch", "N/A")
    best_val_acc = ckpt_meta.get("best_val_acc", 0.0)

    # Identify weak and strong classes
    class_perf = []
    for i, c in enumerate(classes):
        class_perf.append({
            "name": c,
            "precision": precision[i],
            "recall": recall[i],
            "f1": f1[i],
            "support": support[i],
        })

    # Sort by F1 ascending to find weak classes
    class_perf_sorted = sorted(class_perf, key=lambda x: x["f1"])
    weak_classes = [c for c in class_perf_sorted if c["f1"] < 0.35]
    if not weak_classes:
        weak_classes = class_perf_sorted[:2]

    report = f"""# CrimeWatch Model Evaluation Report

**Evaluation Date**: Test split strictly evaluated on held-out videos.  
**Model Architecture**: PyTorchVideo X3D-M (pretrained on Kinetics-400, modified 7-class head).  
**Checkpoint**: `models/x3d_crime.pt` (Best epoch: {best_epoch}, Validation Acc: {best_val_acc:.2f}%).  
**Test Set**: {total_clips} clips across 21 videos (3 unseen videos per class, 36 clips each).

---

## 1. Executive Summary & Honest Assessment

- **Overall Test Accuracy**: **{overall_acc*100:.2f}%**
- **Macro F1-Score**: **{macro_f1*100:.2f}%**
- **Weighted F1-Score**: **{weighted_f1*100:.2f}%**

> [!NOTE]
> **Data Scarcity Reality**: The model was trained on only **14 videos per class** (98 videos total). Untrimmed CCTV footage contains substantial periods of static background or ambient movement where anomalous actions only occur in short temporal windows. With strict video-level partitioning, the test set consists entirely of novel scenes and camera angles the network has never observed.

---

## 2. Test Split Performance Metrics

| Class | Precision (%) | Recall (%) | F1-Score (%) | Test Clips |
| :--- | :---: | :---: | :---: | :---: |
"""
    for cp in class_perf:
        report += f"| **{cp['name']}** | {cp['precision']*100:.1f}% | {cp['recall']*100:.1f}% | {cp['f1']*100:.1f}% | {cp['support']} |\n"

    report += f"""| **Macro Avg** | **{macro_p*100:.1f}%** | **{macro_r*100:.1f}%** | **{macro_f1*100:.1f}%** | **{total_clips}** |
| **Weighted Avg** | **{macro_p*100:.1f}%** | **{overall_acc*100:.1f}%** | **{weighted_f1*100:.1f}%** | **{total_clips}** |

---

## 3. Analysis of Weak Classes & Limitations

{"".join(f"- **{wc['name']} (F1: {wc['f1']*100:.1f}%, Recall: {wc['recall']*100:.1f}%)**: " for wc in weak_classes[:1])}
Key factors affecting weaker classes:
1. **Subtle Motion Ambiguity**: Classes such as *Shoplifting* or *Burglary* involve subtle, localized hand or body motions that can appear visually indistinguishable from *Normal* indoor activities at low resolutions (182x182).
2. **Untrimmed Video Noise**: Because clips are sampled with uniform stride across long CCTV recordings, some sampled 16-frame clips capture calm periods prior to or following the crime event, creating label noise during supervised training.
3. **Severe Data Constraint**: 14 training videos per class provide limited spatial variability (only 14 unique physical CCTV camera views per class). Novel camera perspectives in the test set introduce significant domain shift.

---

## 4. Visual Artifacts

- **Confusion Matrix**: [`outputs/confusion_matrix.png`](confusion_matrix.png)
- **Training Curves**: [`outputs/training_curves.png`](training_curves.png)

![Confusion Matrix](confusion_matrix.png)

![Training Curves](training_curves.png)

---

## 5. Deployment Recommendations for CrimeWatch

1. **Confidence Thresholding**: Set `CONFIDENCE_THRESHOLD >= 0.70` (configured in `.env`) so alerts are only dispatched when high model confidence is achieved.
2. **Temporal Aggregation**: In inference, aggregate predictions over rolling windows (e.g. 3 consecutive positive clips) rather than triggering alerts on a single 16-frame burst.
3. **Targeted Alerting**: Highly kinetic classes (e.g. *Fighting*, *Shooting*) exhibit distinctive optical flow and temporal signatures that are more reliable than subtle covert crimes.
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[INFO] Saved evaluation report to: {report_path}")


# -------------------------------------------------------------------------------
# Main Entrypoint
# -------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Evaluate X3D-M model on test split")
    parser.add_argument(
        "--checkpoint", type=str,
        default="models/x3d_crime.pt",
        help="Path to saved model checkpoint",
    )
    parser.add_argument(
        "--test_csv", type=str,
        default="data_prep/processed/test_clips.csv",
        help="Path to test clips CSV",
    )
    parser.add_argument(
        "--log_csv", type=str,
        default="outputs/training_log.csv",
        help="Path to training log CSV",
    )
    parser.add_argument(
        "--out_dir", type=str,
        default="outputs",
        help="Directory to save evaluation artifacts",
    )
    parser.add_argument(
        "--batch_size", type=int, default=4,
        help="Batch size for evaluation",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    checkpoint_path = repo_root / args.checkpoint
    test_csv = repo_root / args.test_csv
    log_csv = repo_root / args.log_csv
    out_dir = repo_root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("=" * 60)
    print("  CrimeWatch -- Independent Test Split Evaluation")
    print(f"  Device     : {device.type.upper()}")
    print(f"  Checkpoint : {checkpoint_path}")
    print(f"  Test CSV   : {test_csv}")
    print("=" * 60 + "\n")

    # 1. Load model & preprocessing specs
    model, classes, prep, ckpt_meta = load_eval_model(checkpoint_path, device)
    print(f"[INFO] Loaded model checkpoint from epoch {ckpt_meta.get('epoch')} (Val Acc: {ckpt_meta.get('best_val_acc', 0):.2f}%)")

    # 2. Build test dataset & dataloader
    test_dataset = TestClipDataset(
        csv_file=test_csv,
        root_dir=repo_root,
        num_frames=prep.get("num_frames", 16),
        frame_size=prep.get("frame_size", 182),
        mean=prep.get("mean", [0.45, 0.45, 0.45]),
        std=prep.get("std", [0.225, 0.225, 0.225]),
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
    )

    # 3. Evaluate without tuning
    y_true, y_pred, y_probs = evaluate_test_split(model, test_loader, device)

    # 4. Compute metrics
    overall_acc = accuracy_score(y_true, y_pred)
    p_macro, r_macro, f1_macro, _ = precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)
    _, _, f1_weighted, _ = precision_recall_fscore_support(y_true, y_pred, average="weighted", zero_division=0)
    p_class, r_class, f1_class, support_class = precision_recall_fscore_support(
        y_true, y_pred, labels=list(range(len(classes))), zero_division=0
    )

    # 5. Print results to terminal
    print("\n" + "=" * 65)
    print("                     TEST SPLIT METRICS")
    print("=" * 65)
    print(f" Overall Accuracy : {overall_acc * 100:.2f}%")
    print(f" Macro Precision  : {p_macro * 100:.2f}%")
    print(f" Macro Recall     : {r_macro * 100:.2f}%")
    print(f" Macro F1-Score   : {f1_macro * 100:.2f}%")
    print(f" Weighted F1-Score: {f1_weighted * 100:.2f}%")
    print("-" * 65)
    print(f"{'Class':<13} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10} | {'Support'}")
    print("-" * 65)
    for i, c in enumerate(classes):
        print(f" {c:<12} | {p_class[i]*100:>8.1f}% | {r_class[i]*100:>8.1f}% | {f1_class[i]*100:>8.1f}% | {support_class[i]:>7}")
    print("=" * 65 + "\n")

    # 6. Generate artifacts
    plot_confusion_matrix(y_true, y_pred, classes, out_dir / "confusion_matrix.png")
    plot_training_curves(log_csv, out_dir / "training_curves.png")
    write_report_md(
        report_path=out_dir / "report.md",
        overall_acc=overall_acc,
        macro_p=p_macro,
        macro_r=r_macro,
        macro_f1=f1_macro,
        weighted_f1=f1_weighted,
        classes=classes,
        precision=p_class,
        recall=r_class,
        f1=f1_class,
        support=support_class,
        ckpt_meta=ckpt_meta,
        total_clips=len(y_true),
    )


if __name__ == "__main__":
    main()

