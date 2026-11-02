"""
prepare_clips.py
----------------
Prepares video clip indices for training, validation, and testing.

Workflow:
1. Scans raw_videos/<Class>/*.mp4 for all 7 crime & normal classes.
2. Performs a VIDEO-LEVEL split (70% train / 15% val / 15% test) with a fixed seed,
   guaranteeing that clips from the same video never cross splits.
3. Samples 16-frame clips uniformly across the duration of each video using decord.
4. Saves clip metadata to CSV files (video_path, start_frame, label, split)
   in data_prep/processed/ (without exporting thousands of raw frame files).
5. Reports class balance per split and warns if any class has < 3 videos in val/test.

Usage:
  python data_prep/prepare_clips.py
  python data_prep/prepare_clips.py --clips_per_video 12 --num_frames 16 --seed 42
"""

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
import numpy as np
import decord
from decord import VideoReader, cpu

# Suppress noisy decord logging
decord.bridge.set_bridge("native")


def get_video_paths(raw_dir: Path) -> dict[str, list[Path]]:
    """Scan raw_dir and collect video paths grouped by class name."""
    classes = {}
    for d in sorted(raw_dir.iterdir()):
        if d.is_dir():
            videos = sorted(d.glob("*.mp4"))
            if videos:
                classes[d.name] = videos
    return classes


def split_videos(
    videos_by_class: dict[str, list[Path]],
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
) -> tuple[dict[str, dict[str, list[Path]]], list[str]]:
    """
    Split video files by class into train, val, and test splits.
    Returns:
      splits: { class_name: { 'train': [...], 'val': [...], 'test': [...] } }
      warnings: list of warning messages
    """
    rng = random.Random(seed)
    splits = {}
    warnings = []

    for class_name, videos in videos_by_class.items():
        vids = list(videos)
        rng.shuffle(vids)
        total = len(vids)

        n_train = int(round(total * train_ratio))
        n_val = int(round(total * val_ratio))
        # Ensure at least 1 if possible
        if total >= 3:
            n_val = max(1, n_val)
            n_test = total - n_train - n_val
            if n_test < 1:
                n_train -= 1
                n_test = 1
        else:
            n_test = total - n_train - n_val

        train_vids = vids[:n_train]
        val_vids = vids[n_train : n_train + n_val]
        test_vids = vids[n_train + n_val :]

        splits[class_name] = {
            "train": train_vids,
            "val": val_vids,
            "test": test_vids,
        }

        if len(val_vids) < 3 or len(test_vids) < 3:
            warnings.append(
                f"[WARN] Class '{class_name}' has fewer than 3 videos in val/test: "
                f"train={len(train_vids)}, val={len(val_vids)}, test={len(test_vids)}"
            )

    return splits, warnings


def sample_clip_start_frames(
    total_frames: int,
    num_frames: int = 16,
    clips_per_video: int = 12,
) -> list[int]:
    """
    Calculate start frame indices distributed with uniform stride across the video.
    """
    if total_frames < num_frames:
        return [0]

    max_start = total_frames - num_frames
    if max_start == 0:
        return [0]

    k = min(clips_per_video, max_start + 1)
    if k <= 1:
        return [0]

    starts = np.linspace(0, max_start, k, dtype=int)
    # Deduplicate and maintain order
    unique_starts = sorted(set(int(x) for x in starts))
    return unique_starts


def process_and_index_clips(
    splits: dict[str, dict[str, list[Path]]],
    num_frames: int = 16,
    clips_per_video: int = 12,
    frame_size: int = 182,
    base_dir: Path | None = None,
) -> tuple[list[dict], dict]:
    """
    Sample clips for all videos across all splits using decord.
    Returns:
      records: list of clip record dictionaries
      stats: nested dict of video and clip counts per split
    """
    records = []
    classes = sorted(splits.keys())
    class_to_idx = {c: i for i, c in enumerate(classes)}

    stats = {
        c: {
            "train": {"videos": 0, "clips": 0},
            "val": {"videos": 0, "clips": 0},
            "test": {"videos": 0, "clips": 0},
        }
        for c in classes
    }

    total_videos = sum(
        len(vids)
        for class_dict in splits.values()
        for vids in class_dict.values()
    )
    processed_count = 0

    print(f"[INFO] Indexing clips across {total_videos} videos (decord width={frame_size}, height={frame_size})...", flush=True)

    for class_name, split_dict in splits.items():
        for split_name, videos in split_dict.items():
            stats[class_name][split_name]["videos"] = len(videos)

            for video_path in videos:
                processed_count += 1
                try:
                    # Initialize VideoReader with target resolution to verify decoding
                    vr = VideoReader(str(video_path), ctx=cpu(0), width=frame_size, height=frame_size)
                    total_frames = len(vr)
                except Exception as e:
                    print(f"[WARN] Error reading {video_path}: {e}", flush=True)
                    continue

                start_frames = sample_clip_start_frames(
                    total_frames=total_frames,
                    num_frames=num_frames,
                    clips_per_video=clips_per_video,
                )

                # Store relative path with forward slashes for cross-platform portability
                if base_dir:
                    try:
                        rel_path = video_path.resolve().relative_to(base_dir.resolve()).as_posix()
                    except ValueError:
                        rel_path = video_path.as_posix()
                else:
                    rel_path = video_path.as_posix()

                for sf in start_frames:
                    records.append({
                        "video_path": rel_path,
                        "start_frame": sf,
                        "num_frames": num_frames,
                        "label": class_name,
                        "label_idx": class_to_idx[class_name],
                        "split": split_name,
                        "total_video_frames": total_frames,
                    })
                    stats[class_name][split_name]["clips"] += 1

                if processed_count % 35 == 0 or processed_count == total_videos:
                    print(f"  Processed {processed_count:>3}/{total_videos} videos...", flush=True)

    return records, stats


def save_csvs(records: list[dict], out_dir: Path, classes: list[str]) -> None:
    """Save full dataset and individual split CSVs."""
    out_dir.mkdir(parents=True, exist_ok=True)

    fieldnames = ["video_path", "start_frame", "label", "split", "label_idx", "num_frames", "total_video_frames"]

    # 1. Master index
    master_csv = out_dir / "clips_index.csv"
    with open(master_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    # 2. Individual split CSVs
    splits = ["train", "val", "test"]
    for s in splits:
        split_records = [r for r in records if r["split"] == s]
        split_csv = out_dir / f"{s}_clips.csv"
        with open(split_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(split_records)

    # 3. Class mapping JSON
    class_map = {c: i for i, c in enumerate(classes)}
    with open(out_dir / "class_mapping.json", "w", encoding="utf-8") as f:
        json.dump({"class_to_idx": class_map, "idx_to_class": {i: c for c, i in class_map.items()}}, f, indent=2)

    print(f"\n[INFO] CSV index files saved to: {out_dir}")
    print(f"  - Master index: {master_csv.name} ({len(records)} clips)")
    for s in splits:
        count = sum(1 for r in records if r['split'] == s)
        print(f"  - Split index : {s}_clips.csv ({count} clips)")


def print_summary_table(stats: dict, warnings: list[str]) -> None:
    """Print class balance per split in a clean tabular format."""
    print("\n" + "=" * 74)
    print("                    CRIMEWATCH DATASET SPLIT & CLIPS                      ")
    print("=" * 74)
    print(f"{'CLASS':<13} | {'TRAIN (Vid/Clip)':<17} | {'VAL (Vid/Clip)':<15} | {'TEST (Vid/Clip)':<15} | {'TOTAL'}")
    print("-" * 74)

    total_train_v, total_train_c = 0, 0
    total_val_v, total_val_c = 0, 0
    total_test_v, total_test_c = 0, 0

    for cls in sorted(stats.keys()):
        tr_v = stats[cls]["train"]["videos"]
        tr_c = stats[cls]["train"]["clips"]
        va_v = stats[cls]["val"]["videos"]
        va_c = stats[cls]["val"]["clips"]
        te_v = stats[cls]["test"]["videos"]
        te_c = stats[cls]["test"]["clips"]

        tot_v = tr_v + va_v + te_v
        tot_c = tr_c + va_c + te_c

        total_train_v += tr_v
        total_train_c += tr_c
        total_val_v += va_v
        total_val_c += va_c
        total_test_v += te_v
        total_test_c += te_c

        tr_str = f"{tr_v:>2} v / {tr_c:>3} c"
        va_str = f"{va_v:>2} v / {va_c:>3} c"
        te_str = f"{te_v:>2} v / {te_c:>3} c"
        tot_str = f"{tot_v:>2} v / {tot_c:>3} c"

        print(f" {cls:<12} | {tr_str:<17} | {va_str:<15} | {te_str:<15} | {tot_str}")

    print("-" * 74)
    tot_all_v = total_train_v + total_val_v + total_test_v
    tot_all_c = total_train_c + total_val_c + total_test_c
    all_tr_str = f"{total_train_v:>2} v / {total_train_c:>3} c"
    all_va_str = f"{total_val_v:>2} v / {total_val_c:>3} c"
    all_te_str = f"{total_test_v:>2} v / {total_test_c:>3} c"
    all_tot_str = f"{tot_all_v:>2} v / {tot_all_c:>3} c"
    print(f" {'TOTAL':<12} | {all_tr_str:<17} | {all_va_str:<15} | {all_te_str:<15} | {all_tot_str}")
    print("=" * 74)

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f"  {w}")
    else:
        print("\n[OK] All classes have >= 3 videos in validation and test splits.")
    print()


def main():
    parser = argparse.ArgumentParser(description="Prepare multi-clip dataset index for CrimeWatch")
    parser.add_argument(
        "--raw_dir", type=str,
        default=str(Path(__file__).parent / "raw_videos"),
        help="Directory containing raw videos grouped by class",
    )
    parser.add_argument(
        "--out_dir", type=str,
        default=str(Path(__file__).parent / "processed"),
        help="Directory to save clip index CSV files",
    )
    parser.add_argument(
        "--num_frames", type=int, default=16,
        help="Number of consecutive frames per clip (default: 16)",
    )
    parser.add_argument(
        "--clips_per_video", type=int, default=12,
        help="Maximum number of clips sampled per video (default: 12)",
    )
    parser.add_argument(
        "--frame_size", type=int, default=182,
        help="Target frame dimension (182 for 182x182) (default: 182)",
    )
    parser.add_argument(
        "--train_ratio", type=float, default=0.70,
        help="Fraction of videos assigned to training (default: 0.70)",
    )
    parser.add_argument(
        "--val_ratio", type=float, default=0.15,
        help="Fraction of videos assigned to validation (default: 0.15)",
    )
    parser.add_argument(
        "--test_ratio", type=float, default=0.15,
        help="Fraction of videos assigned to testing (default: 0.15)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for video-level split (default: 42)",
    )
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    base_dir = Path(__file__).resolve().parent.parent  # Crime_detection root

    if not raw_dir.exists():
        print(f"[ERROR] Raw videos directory not found: {raw_dir}")
        return

    # 1. Collect videos
    videos_by_class = get_video_paths(raw_dir)
    print(f"[INFO] Found {len(videos_by_class)} classes in {raw_dir}:")
    for cls, vids in videos_by_class.items():
        print(f"  - {cls:<12}: {len(vids)} videos")

    # 2. Split by video
    splits, warnings = split_videos(
        videos_by_class,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        seed=args.seed,
    )

    # 3. Sample clips and build index
    records, stats = process_and_index_clips(
        splits=splits,
        num_frames=args.num_frames,
        clips_per_video=args.clips_per_video,
        frame_size=args.frame_size,
        base_dir=base_dir,
    )

    # 4. Save CSV indices
    save_csvs(records, out_dir, sorted(videos_by_class.keys()))

    # 5. Print summary table
    print_summary_table(stats, warnings)


if __name__ == "__main__":
    main()
