"""
download_selective.py
---------------------
Selectively downloads N videos per class from the Kaggle dataset:
  webadvisor/real-time-anomaly-detection-in-cctv-surveillance

Usage:
  python data_prep/download_selective.py
  python data_prep/download_selective.py --per_class 15
  python data_prep/download_selective.py --per_class 20 --out_dir custom/path

Auth (Kaggle CLI v2 -- pick ONE):
  Option A (recommended):  python -m kaggle auth login
  Option B (token file):   place your token in ~/.kaggle/access_token
  Option C (env var):      set KAGGLE_API_TOKEN=<your_token>

You must also accept the dataset's terms on Kaggle before downloading.
"""

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

# -- Constants ------------------------------------------------------------------
SLUG = "webadvisor/real-time-anomaly-detection-in-cctv-surveillance"

# Dataset folder names (lowercase) -> local ClassName (Title-case)
CLASS_MAP = {
    "fighting":    "Fighting",
    "assault":     "Assault",
    "robbery":     "Robbery",
    "shooting":    "Shooting",
    "shoplifting": "Shoplifting",
    "burglary":    "Burglary",
    "normal":      "Normal",
}

TARGET_CLASSES = set(CLASS_MAP.keys())


# -- Helpers --------------------------------------------------------------------

def kaggle_cmd(*args) -> list[str]:
    """Build a `python -m kaggle <args>` command list (no PATH dependency)."""
    return [sys.executable, "-m", "kaggle"] + list(args)


def run(cmd: list[str], capture: bool = True) -> str:
    """Run a shell command and return stdout. Raises on non-zero exit."""
    result = subprocess.run(
        cmd, capture_output=capture, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] Command failed: {' '.join(cmd)}", file=sys.stderr, flush=True)
        if result.stderr:
            print(result.stderr, file=sys.stderr, flush=True)
        sys.exit(1)
    return result.stdout


def check_auth() -> None:
    """Verify kaggle auth works; print clear instructions if not."""
    result = subprocess.run(
        kaggle_cmd("datasets", "list", "--max-size", "1"),
        capture_output=True, text=True
    )
    output_combined = result.stdout + result.stderr
    if "Authentication required" in output_combined or "401" in output_combined:
        print(
            "\n[ERROR] Kaggle authentication required.\n"
            "  Authenticate with ONE of these methods:\n"
            f"    A) Run:  {sys.executable} -m kaggle auth login\n"
            "    B) Place your API token in: ~/.kaggle/access_token\n"
            "    C) Set env var: KAGGLE_API_TOKEN=<token>\n"
            "\n  Get your token at: https://www.kaggle.com/settings/api\n"
            "  Also ensure you have accepted the dataset terms at:\n"
            f"  https://www.kaggle.com/datasets/{SLUG}\n",
            flush=True
        )
        sys.exit(1)
    print("[INFO] Kaggle authentication OK.", flush=True)


def parse_csv_rows(raw: str) -> list[dict]:
    """Parse CSV text returned by `kaggle datasets files --csv`."""
    reader = csv.DictReader(io.StringIO(raw.strip()))
    return list(reader)


def collect_file_list(per_class: int, cache_file: Path | None = None) -> dict[str, list[str]]:
    """
    Page through `kaggle datasets files --csv` and collect up to `per_class`
    dataset file paths per class.  Stops early once all classes are satisfied.
    Caches results to disk so subsequent runs don't need to re-page.
    """
    if cache_file and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            counts = {c: len(cached.get(c, [])) for c in TARGET_CLASSES}
            if all(counts.get(c, 0) >= per_class for c in TARGET_CLASSES):
                print(f"[INFO] Loaded file list from cache: {cache_file}", flush=True)
                return {c: cached[c][:per_class] for c in TARGET_CLASSES}
        except Exception as e:
            print(f"[WARN] Failed to read cache file ({e}), scanning from Kaggle...", flush=True)

    collected: dict[str, list[str]] = defaultdict(list)
    page_token: str | None = None
    page_num = 0

    print(f"[INFO] Scanning dataset file listing (need {per_class} per class)...", flush=True)

    while True:
        page_num += 1
        cmd = kaggle_cmd("datasets", "files", SLUG, "--csv")
        if page_token:
            cmd += ["--page-token", page_token]

        raw = run(cmd)

        # Extract next-page token
        next_token: str | None = None
        clean_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("Next Page Token"):
                next_token = line.split("=", 1)[-1].strip()
            else:
                clean_lines.append(line)
        clean_csv = "\n".join(clean_lines)

        rows = parse_csv_rows(clean_csv)
        if not rows:
            print(f"[WARN] Page {page_num}: no rows parsed -- stopping.", flush=True)
            break

        for row in rows:
            path = (row.get("name") or row.get("Name") or "").strip()
            if not path.lower().endswith(".mp4"):
                continue

            parts = Path(path).parts
            if len(parts) < 2:
                continue

            folder = parts[-2].lower()
            if folder not in TARGET_CLASSES:
                continue

            if len(collected[folder]) < per_class:
                collected[folder].append(path)

        # Status
        counts = {c: len(v) for c, v in collected.items()}
        satisfied = sum(1 for c in TARGET_CLASSES if counts.get(c, 0) >= per_class)
        print(
            f"  Page {page_num:>3} | rows={len(rows):>4} | "
            f"classes satisfied={satisfied}/{len(TARGET_CLASSES)} | "
            + " ".join(f"{CLASS_MAP[c]}={counts.get(c,0)}" for c in sorted(TARGET_CLASSES)),
            flush=True
        )

        if satisfied == len(TARGET_CLASSES):
            print("[INFO] All classes satisfied -- stopping early.", flush=True)
            break
        if not next_token:
            print("[INFO] No more pages.", flush=True)
            break
        page_token = next_token

    result = dict(collected)
    if cache_file:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            print(f"[INFO] Saved file list to cache: {cache_file}", flush=True)
        except Exception as e:
            print(f"[WARN] Failed to write cache: {e}", flush=True)

    return result


def extract_and_flatten(dest_dir: Path, filename: str) -> bool:
    """
    1. Unzip any .zip or .mp4.zip files in dest_dir (recursively).
    2. Flatten files: if `filename` is inside a subdirectory (e.g. data/assault/),
       move it directly into dest_dir.
    3. Remove leftover empty subdirectories and zip files.
    """
    # 1. Unzip any zip files found
    for zf in list(dest_dir.rglob("*.zip")):
        print(f"  [unzip] {zf.name}", flush=True)
        try:
            with zipfile.ZipFile(zf) as z:
                z.extractall(zf.parent)
            zf.unlink(missing_ok=True)
        except Exception as e:
            print(f"  [WARN] Bad zip {zf.name}: {e}", flush=True)
            zf.unlink(missing_ok=True)

    target_file = dest_dir / filename
    # 2. Check if the target file landed in a subfolder
    if not target_file.exists():
        found = list(dest_dir.rglob(filename))
        if found:
            shutil.move(str(found[0]), str(target_file))

    # 3. Clean up empty subdirectories
    for root, dirs, files in os.walk(dest_dir, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
            except Exception:
                pass

    return target_file.exists() and target_file.stat().st_size > 0


def download_videos(
    file_list: dict[str, list[str]],
    out_root: Path,
    per_class: int,
) -> dict[str, int]:
    """
    Download videos using `kaggle datasets download -f <path> -p <dir> --unzip`.
    Skips files that already exist in out_root / ClassName.

    Returns: { ClassName: count_downloaded }
    """
    summary: dict[str, int] = {}

    for folder_lower, paths in file_list.items():
        class_name = CLASS_MAP[folder_lower]
        dest_dir = out_root / class_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Pre-check and flatten any existing files
        for p in paths:
            extract_and_flatten(dest_dir, Path(p).name)

        downloaded = 0
        skipped = 0

        print(f"\n{'-'*60}", flush=True)
        print(f"[{class_name}]  target={per_class}  queued={len(paths)}", flush=True)
        print(f"{'-'*60}", flush=True)

        for path in paths:
            filename = Path(path).name
            target_path = dest_dir / filename

            if target_path.exists() and target_path.stat().st_size > 0:
                downloaded += 1
                skipped += 1
                print(f"  [skip]  {filename} ({downloaded}/{per_class})", flush=True)
                continue

            print(f"  [dl]    {path}", flush=True)
            cmd = kaggle_cmd(
                "datasets", "download",
                SLUG,
                "-f", path,
                "-p", str(dest_dir),
                "--unzip",
            )
            subprocess.run(cmd, capture_output=False, text=True)

            # Flatten and verify
            if extract_and_flatten(dest_dir, filename):
                downloaded += 1
                print(f"  [ok]    {filename}  ({downloaded}/{per_class})", flush=True)
            else:
                print(f"  [WARN]  Expected file not found after download: {filename}", flush=True)

        summary[class_name] = downloaded
        print(f"  -> {class_name}: {downloaded} video(s) ready", flush=True)

    return summary


def print_summary(summary: dict[str, int], per_class: int) -> None:
    """Pretty-print a table of results."""
    print("\n" + "=" * 42, flush=True)
    print(f"{'CLASS':<15} {'DOWNLOADED':>10}  {'STATUS'}", flush=True)
    print("-" * 42, flush=True)
    all_ok = True
    for class_name in sorted(summary.keys()):
        count = summary[class_name]
        ok = count >= per_class
        if not ok:
            all_ok = False
        status = "OK" if ok else f"INCOMPLETE (need {per_class - count} more)"
        print(f"  {class_name:<13} {count:>10}  {status}", flush=True)
    print("=" * 42, flush=True)
    if all_ok:
        print("[DONE] All classes fully downloaded!", flush=True)
    else:
        print("[WARN] Some classes are incomplete -- re-run or check the dataset.", flush=True)
    print(flush=True)


# -- Main -----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Selective Kaggle dataset downloader for CrimeWatch")
    parser.add_argument(
        "--per_class", type=int, default=20,
        help="Number of videos to download per class (default: 20)"
    )
    parser.add_argument(
        "--out_dir", type=str,
        default=str(Path(__file__).parent / "raw_videos"),
        help="Root output directory (default: data_prep/raw_videos)"
    )
    args = parser.parse_args()

    out_root = Path(args.out_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    cache_file = Path(__file__).parent / "dataset_files_cache.json"

    print("=" * 60, flush=True)
    print("  CrimeWatch -- Selective Dataset Downloader", flush=True)
    print(f"  Dataset : {SLUG}", flush=True)
    print(f"  Classes : {', '.join(sorted(CLASS_MAP.values()))}", flush=True)
    print(f"  Per class: {args.per_class}", flush=True)
    print(f"  Output  : {out_root}", flush=True)
    print("=" * 60 + "\n", flush=True)

    # Pre-flight: verify auth
    check_auth()

    # Stage 1: collect file paths from the dataset listing (or cache)
    file_list = collect_file_list(args.per_class, cache_file=cache_file)

    # Warn about any class that couldn't find enough files
    for cls in TARGET_CLASSES:
        found = len(file_list.get(cls, []))
        if found < args.per_class:
            print(
                f"[WARN] {CLASS_MAP[cls]}: only {found}/{args.per_class} "
                f"files found in dataset listing.",
                flush=True
            )

    # Stage 2: download
    summary = download_videos(file_list, out_root, args.per_class)

    # Stage 3: print summary
    print_summary(summary, args.per_class)


if __name__ == "__main__":
    main()
