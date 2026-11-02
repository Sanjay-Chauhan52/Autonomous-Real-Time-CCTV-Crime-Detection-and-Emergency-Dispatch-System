"""
train_fast_head.py
------------------
Extracts frozen Kinetics-400 pretrained 2048-dim X3D features for all clips
and trains an optimal classification head with regularization to prevent overfitting.
"""

import csv
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
import pytorchvideo.models.hub as ptv_hub

from training.train_x3d import CrimeClipDataset

def extract_features(model, dataloader, device):
    features = []
    labels = []
    model.eval()
    with torch.no_grad():
        for batch_idx, (inputs, targets) in enumerate(dataloader):
            inputs = inputs.to(device, non_blocking=True)
            with torch.amp.autocast("cuda", dtype=torch.float16):
                # Run through backbone up to block 4
                x = inputs
                for block in model.blocks[:-1]:
                    x = block(x)
                # Head spatial-temporal pooling
                head = model.blocks[-1]
                x = head.pool(x)  # shape (B, 2048, 1, 1, 1)
                feat = x.flatten(start_dim=1)  # shape (B, 2048)
            features.append(feat.cpu().float().numpy())
            labels.append(targets.numpy())
            if (batch_idx + 1) % 15 == 0 or (batch_idx + 1) == len(dataloader):
                print(f"  Extracted batch {batch_idx + 1}/{len(dataloader)}")
    return np.vstack(features), np.concatenate(labels)

def main():
    repo_root = Path(__file__).resolve().parent.parent
    processed_dir = repo_root / "data_prep" / "processed"
    models_dir = repo_root / "models"
    outputs_dir = repo_root / "outputs"

    train_csv = processed_dir / "train_clips.csv"
    val_csv = processed_dir / "val_clips.csv"
    test_csv = processed_dir / "test_clips.csv"
    mapping_json = processed_dir / "class_mapping.json"

    with open(mapping_json, "r") as f:
        meta = json.load(f)
        classes = [meta["idx_to_class"][str(i)] for i in range(len(meta["idx_to_class"]))]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # Load clean Kinetics-400 X3D-M
    print("Loading pretrained Kinetics-400 X3D-M...")
    model = ptv_hub.x3d_m(pretrained=True)
    model.blocks[-1].pool.pool = nn.AdaptiveAvgPool3d((1, 1, 1))
    model.to(device)

    # Dataloaders
    train_ds = CrimeClipDataset(train_csv, repo_root, is_train=False)
    val_ds = CrimeClipDataset(val_csv, repo_root, is_train=False)
    test_ds = CrimeClipDataset(test_csv, repo_root, is_train=False)

    batch_size = 16
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    print("\n--- Extracting Training Features ---")
    X_train, y_train = extract_features(model, train_loader, device)
    print("\n--- Extracting Validation Features ---")
    X_val, y_val = extract_features(model, val_loader, device)
    print("\n--- Extracting Test Features ---")
    X_test, y_test = extract_features(model, test_loader, device)

    print(f"\nFeatures shape: Train {X_train.shape}, Val {X_val.shape}, Test {X_test.shape}")

    # Train a calibrated Logistic Regression classifier with L2 regularization
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, classification_report

    best_clf = None
    best_val_score = 0.0
    best_c = 1.0

    print("\nTuning regularization parameter C...")
    for C in [0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0]:
        clf = LogisticRegression(C=C, max_iter=1000, random_state=42, class_weight="balanced")
        clf.fit(X_train, y_train)
        val_score = clf.score(X_val, y_val)
        print(f"  C={C:<6} -> Val Acc: {val_score * 100:.2f}%")
        if val_score > best_val_score:
            best_val_score = val_score
            best_clf = clf
            best_c = C

    print(f"\nBest C: {best_c} with Val Acc: {best_val_score * 100:.2f}%")

    # Evaluate on held-out test split
    test_preds = best_clf.predict(X_test)
    test_acc = accuracy_score(y_test, test_preds) * 100.0
    print(f"\n>>> TEST ACCURACY: {test_acc:.2f}% <<<")
    print(classification_report(y_test, test_preds, target_names=classes, digits=3))

    # Save weights back into PyTorch model
    weights = torch.tensor(best_clf.coef_, dtype=torch.float32)
    bias = torch.tensor(best_clf.intercept_, dtype=torch.float32)

    # Construct the PyTorch model with trained weights
    model.blocks[-1].proj = nn.Linear(2048, len(classes))
    model.blocks[-1].proj.weight.data.copy_(weights)
    model.blocks[-1].proj.bias.data.copy_(bias)
    model.blocks[-1].activation = None

    save_path = models_dir / "x3d_crime.pt"
    # Backup old checkpoint
    if save_path.exists():
        backup_path = models_dir / "x3d_crime_backup.pt"
        if not backup_path.exists():
            import shutil
            shutil.copy2(save_path, backup_path)
            print(f"Backed up old checkpoint to {backup_path.name}")

    torch.save({
        "epoch": 10,
        "best_val_acc": float(best_val_score * 100),
        "test_acc": float(test_acc),
        "model_state_dict": model.state_dict(),
        "classes": classes,
        "class_to_idx": {c: i for i, c in enumerate(classes)},
        "preprocessing": {"num_frames": 16, "frame_size": 182, "mean": [0.45, 0.45, 0.45], "std": [0.225, 0.225, 0.225]},
    }, save_path)
    print(f"[SUCCESS] Saved optimized model to {save_path}!")

if __name__ == "__main__":
    main()

