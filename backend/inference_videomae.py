"""
CrimeWatch — VideoMAE Inference Engine
=======================================
Standalone inference module using the fine-tuned VideoMAE-Base model.
Can be used:
  1. As a CLI tool:     python -m backend.inference_videomae path/to/video.mp4
  2. As a Python import: from backend.inference_videomae import VideoMAEDetector
  3. Via the REST API:  set USE_VIDEOMAE=1 in .env to swap the backend model

This is a DROP-IN replacement for inference_engine.CrimeDetector —
it exposes the same .analyze_video() interface.
"""

import sys, glob, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

import numpy as np
import torch
import torch.nn.functional as F
import cv2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from transformers import (
        VideoMAEForVideoClassification,
        VideoMAEImageProcessor,
    )
    HAS_VIDEOMAE = True
except ImportError:
    HAS_VIDEOMAE = False

# ── Constants ────────────────────────────────────────────────────────────────

CLASSES    = ["Assault", "Burglary", "Fighting", "Normal", "Robbery", "Shooting", "Shoplifting"]
LABEL2ID   = {c: i for i, c in enumerate(CLASSES)}
ID2LABEL   = {i: c for i, c in enumerate(CLASSES)}

SEVERITY_MAP = {
    "Shooting":   "HIGH",
    "Assault":    "HIGH",
    "Robbery":    "HIGH",
    "Fighting":   "HIGH",
    "Burglary":   "MEDIUM",
    "Shoplifting":"MEDIUM",
    "Normal":     "none",
}

# Sensitivity calibration weights (same data-driven ones as X3D engine)
SENSITIVITY = {
    "Assault":    1.00,
    "Burglary":   0.90,
    "Fighting":   1.20,
    "Normal":     1.00,
    "Robbery":    0.90,
    "Shooting":   0.85,
    "Shoplifting":1.15,
}

HF_CHECKPOINT = PROJECT_ROOT / "models" / "videomae_crime_full"   # after training
PT_CHECKPOINT  = PROJECT_ROOT / "models" / "videomae_crime.pt"    # state_dict fallback
LOCAL_BASE     = PROJECT_ROOT / "models" / "videomae_base"
PRETRAINED_BASE = str(LOCAL_BASE) if (LOCAL_BASE / "model.safetensors").exists() or (LOCAL_BASE / "pytorch_model.bin").exists() else "MCG-NJU/videomae-base-finetuned-kinetics"

NUM_FRAMES  = 16
FRAME_SIZE  = 224
STRIDE_SEC  = 1.0   # analyze one window every N seconds
WINDOW_SEC  = 0.8   # fraction of video covered per window


# ── Detector class ────────────────────────────────────────────────────────────

class VideoMAEDetector:
    """
    Drop-in replacement for CrimeDetector — same .analyze_video() API.
    """

    def __init__(self,
                 checkpoint_path: Optional[str] = None,
                 conf_threshold: float = 0.25):

        if not HAS_VIDEOMAE:
            raise RuntimeError(
                "transformers not installed. Run: pip install transformers accelerate"
            )

        self.classes       = CLASSES
        self.label2id      = LABEL2ID
        self.id2label      = ID2LABEL
        self.conf_threshold= conf_threshold
        self.device        = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        print(f"[VideoMAEDetector] Loading model on {self.device}...")
        t0 = time.time()

        # ── Try HF-format checkpoint first (saved by train_videomae.py) ──────
        hf_dir = Path(checkpoint_path) if checkpoint_path else HF_CHECKPOINT
        if hf_dir.exists():
            print(f"[VideoMAEDetector] Loading from HF checkpoint: {hf_dir}")
            self.processor = VideoMAEImageProcessor.from_pretrained(str(hf_dir))
            self.model     = VideoMAEForVideoClassification.from_pretrained(
                str(hf_dir),
                num_labels=len(CLASSES),
                id2label=ID2LABEL,
                label2id=LABEL2ID,
                ignore_mismatched_sizes=True,
            )
        elif PT_CHECKPOINT.exists():
            # Load from state_dict + pretrained base processor
            print(f"[VideoMAEDetector] Loading state_dict from {PT_CHECKPOINT}")
            self.processor = VideoMAEImageProcessor.from_pretrained(PRETRAINED_BASE)
            self.model = VideoMAEForVideoClassification.from_pretrained(
                PRETRAINED_BASE,
                num_labels=len(CLASSES),
                id2label=ID2LABEL,
                label2id=LABEL2ID,
                ignore_mismatched_sizes=True,
            )
            ckpt = torch.load(str(PT_CHECKPOINT), map_location="cpu")
            self.model.load_state_dict(ckpt["model_state_dict"])
        else:
            # No fine-tuned checkpoint — run pretrained Kinetics-400 model
            # (predictions won't map to crime classes, but shows the architecture)
            print("[VideoMAEDetector] WARNING: No fine-tuned checkpoint found.")
            print(f"  Train first: python -m training.train_videomae")
            print("  Using pretrained base (Kinetics-400 head) as placeholder.")
            self.processor = VideoMAEImageProcessor.from_pretrained(PRETRAINED_BASE)
            self.model = VideoMAEForVideoClassification.from_pretrained(
                PRETRAINED_BASE,
                num_labels=len(CLASSES),
                id2label=ID2LABEL,
                label2id=LABEL2ID,
                ignore_mismatched_sizes=True,
            )

        self.model = self.model.to(self.device)
        self.model.eval()
        print(f"[VideoMAEDetector] Ready in {time.time()-t0:.1f}s")

    # ── Frame reading ─────────────────────────────────────────────────────────

    def _read_frames(self, video_path: str,
                     start_frame: int, end_frame: int) -> Optional[np.ndarray]:
        """Read NUM_FRAMES uniformly-sampled RGB frames between [start_frame, end_frame]."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        end_frame = min(end_frame, total - 1)
        if end_frame <= start_frame + NUM_FRAMES:
            end_frame = min(start_frame + NUM_FRAMES * 2, total - 1)
        indices = np.linspace(start_frame, end_frame, NUM_FRAMES, dtype=int)
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
        if len(frames) < NUM_FRAMES:
            while len(frames) < NUM_FRAMES:
                frames.append(frames[-1] if frames else
                              np.zeros((FRAME_SIZE, FRAME_SIZE, 3), np.uint8))
        return np.stack(frames[:NUM_FRAMES], axis=0)   # (T, H, W, 3)

    # ── Single-window prediction ───────────────────────────────────────────────

    @torch.no_grad()
    def predict_clip(self, frames: np.ndarray) -> Dict[str, Any]:
        """
        frames: (T, H, W, 3) numpy uint8
        Returns: {"label", "confidence", "all_probs", "severity"}
        """
        frame_list = [frames[t] for t in range(NUM_FRAMES)]
        inputs = self.processor(images=frame_list, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(self.device)

        with torch.cuda.amp.autocast(enabled=(self.device.type == "cuda")):
            outputs = self.model(pixel_values=pixel_values)

        logits = outputs.logits[0]                    # (num_classes,)
        probs  = F.softmax(logits, dim=0)             # (num_classes,)
        probs_np = probs.float().cpu().numpy()

        pred_id   = int(probs_np.argmax())
        pred_cls  = self.id2label[pred_id]
        pred_conf = float(probs_np[pred_id])

        all_probs = {self.id2label[i]: float(probs_np[i]) for i in range(len(CLASSES))}

        return {
            "label":     pred_cls,
            "confidence":pred_conf,
            "all_probs": all_probs,
            "severity":  SEVERITY_MAP.get(pred_cls, "none"),
        }

    # ── Full video analysis (same interface as CrimeDetector) ─────────────────

    def analyze_video(
        self,
        video_path: Union[str, Path],
        stride: Optional[int] = None,
        confidence_threshold: Optional[float] = None,
        conf_thresh: Optional[float] = None,
        stride_override: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Analyze an entire video file.
        Accepts both confidence_threshold and conf_thresh for drop-in compatibility
        with CrimeDetector and FastAPI endpoints.
        Returns the same dict shape as CrimeDetector.analyze_video().
        """
        effective_conf = confidence_threshold if confidence_threshold is not None else (
            conf_thresh if conf_thresh is not None else self.conf_threshold
        )

        video_path = Path(video_path)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames= int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        # Cap very long videos
        max_frames = 3600
        effective_total = min(total_frames, max_frames)
        duration_sec    = effective_total / fps

        stride_sec  = stride_override or STRIDE_SEC
        if stride is not None:
            stride_f = max(1, int(stride))
        else:
            stride_f = max(1, int(stride_sec * fps))
        window_f    = max(NUM_FRAMES * 2, int(stride_f * 1.5))

        timeline = []
        window_idx = 0
        start_frame = 0

        while start_frame < effective_total - NUM_FRAMES:
            end_frame = min(start_frame + window_f, effective_total - 1)
            frames = self._read_frames(str(video_path), start_frame, end_frame)
            if frames is None:
                break

            pred = self.predict_clip(frames)

            start_sec = round(start_frame / fps, 2)
            end_sec   = round(end_frame   / fps, 2)

            timeline.append({
                "window_idx": window_idx,
                "start_frame": start_frame,
                "end_frame":   end_frame,
                "start_sec":   start_sec,
                "end_sec":     end_sec,
                "label":       pred["label"],
                "confidence":  pred["confidence"],
                "all_probs":   pred["all_probs"],
                "severity":    pred["severity"],
            })
            window_idx  += 1
            start_frame += stride_f

        # ── Merge contiguous events ───────────────────────────────────────────
        merged_events = self._merge_events(timeline, effective_conf)

        # ── Calibrated average-probability voting (video-level verdict) ────────
        n_windows = max(len(timeline), 1)
        cumulative = {c: 0.0 for c in CLASSES}
        class_peak: Dict[str, Tuple[float, float]] = {}   # cls → (max_prob, sec)

        for w in timeline:
            for cls_name in CLASSES:
                p   = float(w["all_probs"].get(cls_name, 0.0))
                sec = float(w["start_sec"])
                cumulative[cls_name] += p
                if cls_name not in class_peak or p > class_peak[cls_name][0]:
                    class_peak[cls_name] = (p, sec)

        calibrated_avg = {
            c: (cumulative[c] / n_windows) * SENSITIVITY.get(c, 1.0)
            for c in CLASSES
        }

        overall_class             = max(CLASSES, key=lambda c: calibrated_avg[c])
        overall_conf, peak_sec    = class_peak.get(overall_class, (0.3, 0.0))
        overall_severity          = SEVERITY_MAP.get(overall_class, "none")
        peak_m, peak_s            = int(peak_sec // 60), int(peak_sec % 60)
        peak_timestamp_str        = f"{peak_m:02d}:{peak_s:02d}"

        overall_verdict = {
            "class":             overall_class,
            "confidence":        round(float(overall_conf), 4),
            "severity":          overall_severity,
            "peak_sec":          round(float(peak_sec), 2),
            "peak_timestamp_str":peak_timestamp_str,
            "is_crime":          (overall_class != "Normal"),
            "model":             "VideoMAE-Base",
        }

        return {
            "video_path":    str(video_path),
            "fps":           round(fps, 2),
            "total_frames":  total_frames,
            "duration_sec":  round(duration_sec, 2),
            "timeline":      timeline,
            "merged_events": merged_events,
            "overall_verdict": overall_verdict,
        }

    def _merge_events(self, timeline: List[Dict], conf_thresh: float) -> List[Dict]:
        """Merge adjacent crime windows above threshold (same logic as X3D engine)."""
        crime_windows = [
            w for w in timeline
            if w["label"] != "Normal" and w["confidence"] >= conf_thresh
        ]
        if not crime_windows:
            return []

        merged, group = [], [crime_windows[0]]
        for w in crime_windows[1:]:
            if (w["label"] == group[-1]["label"] and
                    w["start_sec"] - group[-1]["end_sec"] <= 2.0):
                group.append(w)
            else:
                merged.append(self._summarise_group(group))
                group = [w]
        merged.append(self._summarise_group(group))
        return merged

    def _summarise_group(self, group: List[Dict]) -> Dict:
        best  = max(group, key=lambda x: x["confidence"])
        return {
            "class":          best["label"],
            "severity":       best["severity"],
            "start_sec":      group[0]["start_sec"],
            "end_sec":        group[-1]["end_sec"],
            "max_confidence": best["confidence"],
            "window_count":   len(group),
        }

    def get_severity(self, cls: str) -> str:
        return SEVERITY_MAP.get(cls, "none")


# ── CLI entry-point ───────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="CrimeWatch VideoMAE Inference")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--threshold", type=float, default=0.25,
                        help="Confidence threshold for merged events")
    args = parser.parse_args()

    detector = VideoMAEDetector(conf_threshold=args.threshold)
    print(f"\n[INFO] Analyzing: {args.video}")
    result = detector.analyze_video(args.video)

    v = result["overall_verdict"]
    print(f"\n{'='*50}")
    print(f"  VERDICT: {v['class'].upper()}  ({v['confidence']*100:.1f}% confidence)")
    print(f"  SEVERITY: {v['severity']}")
    print(f"  PEAK AT: {v['peak_timestamp_str']}  ({v['peak_sec']}s)")
    print(f"  IS_CRIME: {v['is_crime']}")
    print(f"  MODEL: {v.get('model','VideoMAE-Base')}")
    print(f"{'='*50}")
    print(f"\nTimeline: {len(result['timeline'])} windows analysed")
    print(f"Merged crime events: {len(result['merged_events'])}")


if __name__ == "__main__":
    main()
