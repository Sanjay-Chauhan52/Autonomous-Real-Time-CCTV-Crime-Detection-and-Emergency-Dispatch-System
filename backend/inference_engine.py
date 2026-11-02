"""
inference_engine.py
-------------------
Core inference engine for CrimeWatch CCTV anomaly detection.

Features:
- CrimeDetector:
    * Loads models/x3d_crime.pt once with CUDA + fp16 support if available.
    * predict_clip(frames): Takes 16 RGB frames and returns {label, confidence, all_probs, severity}.
    * analyze_video(path): Slides a 16-frame window (stride 8) over a file and returns
      a timeline of detections plus merged temporal events (class, start_sec, end_sec, max_confidence, severity).
- StreamAnalyzer:
    * Real-time rolling buffer for streaming video frames.
    * Evaluates confidence threshold (default 0.70), persistence (3 consecutive windows),
      and per-class cooldown (default 60s).
- Configurable severity mapping and settings via backend/config.yaml.
"""

import collections
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import torch
import torch.nn as nn
import yaml

try:
    import decord
    from decord import VideoReader, cpu
    decord.bridge.set_bridge("native")
    HAS_DECORD = True
except ImportError:
    HAS_DECORD = False

# Default Kinetics-400 normalization constants
DEFAULT_MEAN = [0.45, 0.45, 0.45]
DEFAULT_STD = [0.225, 0.225, 0.225]
DEFAULT_CLASSES = [
    "Assault", "Burglary", "Fighting", "Normal", "Robbery", "Shooting", "Shoplifting"
]
DEFAULT_SEVERITY_MAP = {
    "Shooting": "HIGH",
    "Robbery": "HIGH",
    "Assault": "HIGH",
    "Fighting": "HIGH",
    "Shoplifting": "MEDIUM",
    "Burglary": "MEDIUM",
    "Normal": "none",
}


def load_config(config_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Loads configuration YAML file or returns sensible defaults."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent / "config.yaml"
    else:
        config_path = Path(config_path)

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            return cfg

    # Fallback default dictionary
    return {
        "model": {
            "checkpoint_path": "models/x3d_crime.pt",
            "num_frames": 16,
            "frame_size": 182,
            "stride": 8,
            "mean": DEFAULT_MEAN,
            "std": DEFAULT_STD,
        },
        "detection": {
            "confidence_threshold": 0.70,
            "persistence_windows": 3,
            "cooldown_seconds": 60.0,
        },
        "severity_map": DEFAULT_SEVERITY_MAP,
    }


# -------------------------------------------------------------------------------
# CrimeDetector
# -------------------------------------------------------------------------------

class CrimeDetector:
    """
    Reusable detector for X3D-M video anomaly classification.
    Loads model checkpoint once and optimizes inference with CUDA & fp16 if available.
    """
    def __init__(
        self,
        checkpoint_path: Optional[Union[str, Path]] = None,
        config_path: Optional[Union[str, Path]] = None,
        model: Optional[nn.Module] = None,
        classes: Optional[List[str]] = None,
        device: Optional[torch.device] = None,
    ):
        self.config = load_config(config_path)
        self.model_cfg = self.config.get("model", {})
        self.severity_map = self.config.get("severity_map", DEFAULT_SEVERITY_MAP)

        self.num_frames = int(self.model_cfg.get("num_frames", 16))
        self.frame_size = int(self.model_cfg.get("frame_size", 182))
        self.stride = int(self.model_cfg.get("stride", 8))

        mean = self.model_cfg.get("mean", DEFAULT_MEAN)
        std = self.model_cfg.get("std", DEFAULT_STD)
        self.mean_tensor = torch.tensor(mean, dtype=torch.float32).view(1, 3, 1, 1, 1)
        self.std_tensor = torch.tensor(std, dtype=torch.float32).view(1, 3, 1, 1, 1)

        # Device selection
        if device is not None:
            self.device = device
        else:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.use_amp = (self.device.type == "cuda")

        self.mean_tensor = self.mean_tensor.to(self.device)
        self.std_tensor = self.std_tensor.to(self.device)

        if model is not None:
            # Custom or mock model supplied directly
            self.model = model
            self.classes = classes or DEFAULT_CLASSES
        else:
            # Resolve checkpoint path
            ckpt_p = checkpoint_path or self.model_cfg.get("checkpoint_path", "models/x3d_crime.pt")
            ckpt_path = Path(ckpt_p)
            if not ckpt_path.is_absolute():
                # Relative to project root
                project_root = Path(__file__).resolve().parent.parent
                ckpt_path = project_root / ckpt_path

            self.model, self.classes = self._load_checkpoint(ckpt_path)

        self.model.to(self.device)
        self.model.eval()

    def _load_checkpoint(self, checkpoint_path: Path) -> Tuple[nn.Module, List[str]]:
        """Constructs X3D-M architecture and loads weights from checkpoint."""
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Model checkpoint not found at: {checkpoint_path}")

        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        classes = ckpt.get("classes", DEFAULT_CLASSES)
        num_classes = len(classes)

        try:
            model = torch.hub.load("facebookresearch/pytorchvideo", "x3d_m", pretrained=False)
        except Exception:
            import pytorchvideo.models.hub as ptv_hub
            model = ptv_hub.x3d_m(pretrained=False)

        # Adapt head for target classes and arbitrary resolution
        head = model.blocks[-1]
        in_features = head.proj.in_features
        head.pool.pool = nn.AdaptiveAvgPool3d((1, 1, 1))
        head.proj = nn.Linear(in_features, num_classes)
        head.activation = None

        model.load_state_dict(ckpt["model_state_dict"])
        return model, classes

    def get_severity(self, label: str) -> str:
        """Returns the severity level for a given class name."""
        return self.severity_map.get(label, "none")

    def _preprocess_frames(self, frames: Union[List[np.ndarray], np.ndarray]) -> torch.Tensor:
        """
        Preprocesses a 16-frame sequence into a normalized tensor of shape (1, 3, 16, H, W).
        """
        if isinstance(frames, list):
            frames = np.stack(frames, axis=0)

        # frames shape: (T, H, W, C)
        T, H, W, C = frames.shape
        if T != self.num_frames:
            raise ValueError(f"Expected {self.num_frames} frames, received {T}")

        # Resize if spatial dimensions do not match
        if H != self.frame_size or W != self.frame_size:
            resized_frames = []
            for frame in frames:
                rf = cv2.resize(frame, (self.frame_size, self.frame_size), interpolation=cv2.INTER_LINEAR)
                resized_frames.append(rf)
            frames = np.stack(resized_frames, axis=0)

        # Convert to FloatTensor in [0, 1] with shape (1, 3, T, H, W)
        tensor = torch.from_numpy(frames).float() / 255.0
        tensor = tensor.permute(3, 0, 1, 2).unsqueeze(0)  # (1, 3, 16, 182, 182)
        tensor = tensor.to(self.device, non_blocking=True)
        tensor = (tensor - self.mean_tensor) / self.std_tensor
        return tensor

    def predict_clip(self, frames: Union[List[np.ndarray], np.ndarray]) -> Dict[str, Any]:
        """
        Runs inference on 16 consecutive RGB frames.
        Returns:
            {
                "label": str,
                "confidence": float,
                "all_probs": {class_name: float, ...},
                "severity": str
            }
        """
        tensor = self._preprocess_frames(frames)

        with torch.no_grad():
            if self.use_amp:
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    logits = self.model(tensor)
            else:
                logits = self.model(tensor)

            probs = torch.softmax(logits, dim=1).squeeze(0).cpu().numpy()

        top_idx = int(np.argmax(probs))
        top_label = self.classes[top_idx]
        top_conf = float(probs[top_idx])
        all_probs = {c: float(round(float(p), 4)) for c, p in zip(self.classes, probs)}

        return {
            "label": top_label,
            "confidence": round(top_conf, 4),
            "all_probs": all_probs,
            "severity": self.get_severity(top_label),
        }

    def analyze_video(
        self,
        video_path: Union[str, Path],
        stride: Optional[int] = None,
        confidence_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Slides a 16-frame window across a video file with uniform stride.
        Returns a timeline of detections and merged temporal anomaly events.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        stride = stride or self.stride
        conf_thresh = confidence_threshold or float(
            self.config.get("detection", {}).get("confidence_threshold", 0.70)
        )

        frames, fps = self._read_all_frames(video_path)
        total_frames = len(frames)
        duration_sec = total_frames / fps if fps > 0 else 0.0

        timeline = []
        if total_frames < self.num_frames:
            # Video too short: pad or evaluate once
            if total_frames > 0:
                pad_frames = [frames[-1]] * (self.num_frames - total_frames)
                clip = frames + pad_frames
                pred = self.predict_clip(clip)
                timeline.append({
                    "window_idx": 0,
                    "start_frame": 0,
                    "end_frame": total_frames,
                    "start_sec": 0.0,
                    "end_sec": round(duration_sec, 2),
                    "label": pred["label"],
                    "confidence": pred["confidence"],
                    "all_probs": pred["all_probs"],
                    "severity": pred["severity"],
                })
        else:
            window_idx = 0
            for start_frame in range(0, total_frames - self.num_frames + 1, stride):
                end_frame = start_frame + self.num_frames
                clip = frames[start_frame:end_frame]
                pred = self.predict_clip(clip)

                start_sec = round(start_frame / fps, 2)
                end_sec = round(end_frame / fps, 2)

                timeline.append({
                    "window_idx": window_idx,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "label": pred["label"],
                    "confidence": pred["confidence"],
                    "all_probs": pred["all_probs"],
                    "severity": pred["severity"],
                })
                window_idx += 1

        # Merge contiguous/overlapping anomaly windows
        merged_events = self._merge_timeline_events(timeline, conf_thresh)

        # ── Calibrated Average-Probability Voting ────────────────────────
        # Prior/sensitivity weights to counteract class bias from fine-tuning
        # on limited CCTV footage (e.g., Fighting and Shoplifting under-triggered,
        # Shooting and Burglary over-triggered by raw head logits).
        SENSITIVITY = {
            "Assault": 1.0,
            "Burglary": 0.90,
            "Fighting": 1.20,
            "Normal": 1.0,
            "Robbery": 0.90,
            "Shooting": 0.85,
            "Shoplifting": 1.15,
        }
        n_windows = max(len(timeline), 1)

        # 1. Accumulate per-class probability across every window
        cumulative: Dict[str, float] = {c: 0.0 for c in self.classes}
        class_peak_conf: Dict[str, Tuple[float, float]] = {}  # class -> (max_prob, sec)
        for w in timeline:
            for cls_name in self.classes:
                p = float(w["all_probs"].get(cls_name, 0.0))
                cumulative[cls_name] += p
                sec = float(w["start_sec"])
                if cls_name not in class_peak_conf or p > class_peak_conf[cls_name][0]:
                    class_peak_conf[cls_name] = (p, sec)

        # 2. Compute calibrated average probability per class
        calibrated_avg = {
            c: (cumulative[c] / n_windows) * SENSITIVITY.get(c, 1.0)
            for c in self.classes
        }

        # 3. Pick class with highest calibrated score
        overall_class = max(self.classes, key=lambda c: calibrated_avg[c])
        overall_conf, peak_sec = class_peak_conf.get(overall_class, (0.3, 0.0))

        overall_severity = self.get_severity(overall_class)
        peak_m = int(peak_sec // 60)
        peak_s = int(peak_sec % 60)
        peak_timestamp_str = f"{peak_m:02d}:{peak_s:02d}"

        overall_verdict = {
            "class": overall_class,
            "confidence": round(float(overall_conf), 4),
            "severity": overall_severity,
            "peak_sec": round(float(peak_sec), 2),
            "peak_timestamp_str": peak_timestamp_str,
            "is_crime": (overall_class != "Normal"),
        }

        return {
            "video_path": str(video_path),
            "fps": round(fps, 2),
            "total_frames": total_frames,
            "duration_sec": round(duration_sec, 2),
            "timeline": timeline,
            "merged_events": merged_events,
            "overall_verdict": overall_verdict,
        }

    def _read_all_frames(self, video_path: Path) -> Tuple[List[np.ndarray], float]:
        """Reads RGB frames and FPS from video with Decord or OpenCV fallback, capped to prevent OOM."""
        max_allowed_frames = 3600  # Cap to ~2 minutes @ 30fps for memory safety
        if HAS_DECORD:
            try:
                vr = VideoReader(str(video_path), ctx=cpu(0), width=self.frame_size, height=self.frame_size)
                fps = float(vr.get_avg_fps()) if vr.get_avg_fps() > 0 else 30.0
                total_frames = len(vr)
                num_to_read = min(total_frames, max_allowed_frames)
                frames = [vr[i].asnumpy() for i in range(num_to_read)]
                return frames, fps
            except Exception:
                pass

        # OpenCV fallback
        cap = cv2.VideoCapture(str(video_path))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if fps <= 0 or np.isnan(fps):
            fps = 30.0

        frames = []
        while cap.isOpened() and len(frames) < max_allowed_frames:
            ret, frame = cap.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if rgb.shape[0] != self.frame_size or rgb.shape[1] != self.frame_size:
                rgb = cv2.resize(rgb, (self.frame_size, self.frame_size), interpolation=cv2.INTER_LINEAR)
            frames.append(rgb)
        cap.release()
        return frames, fps

    def _merge_timeline_events(
        self, timeline: List[Dict[str, Any]], conf_thresh: float
    ) -> List[Dict[str, Any]]:
        """
        Merges consecutive windows with non-Normal anomalous classes exceeding confidence threshold.
        """
        events = []
        current_event: Optional[Dict[str, Any]] = None

        for item in timeline:
            label = item["label"]
            conf = item["confidence"]
            start_sec = item["start_sec"]
            end_sec = item["end_sec"]

            is_anomaly = (label != "Normal") and (conf >= conf_thresh)

            if is_anomaly:
                if current_event is None:
                    # Start new anomaly event
                    current_event = {
                        "class": label,
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "max_confidence": conf,
                        "severity": self.get_severity(label),
                    }
                elif current_event["class"] == label and start_sec <= current_event["end_sec"] + 1.0:
                    # Extend current event
                    current_event["end_sec"] = max(current_event["end_sec"], end_sec)
                    current_event["max_confidence"] = max(current_event["max_confidence"], conf)
                else:
                    # Class switch or time gap: commit previous and start new
                    events.append(current_event)
                    current_event = {
                        "class": label,
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "max_confidence": conf,
                        "severity": self.get_severity(label),
                    }
            else:
                if current_event is not None:
                    events.append(current_event)
                    current_event = None

        if current_event is not None:
            events.append(current_event)

        return events


# -------------------------------------------------------------------------------
# StreamAnalyzer
# -------------------------------------------------------------------------------

class StreamAnalyzer:
    """
    Rolling buffer analyzer for live CCTV video streams.
    Applies:
    1. Confidence threshold (default 0.70)
    2. Persistence filtering (3 consecutive windows of the same anomaly class)
    3. Per-class alert cooldown (default 60s)
    """
    def __init__(
        self,
        detector: CrimeDetector,
        confidence_threshold: Optional[float] = None,
        persistence_windows: Optional[int] = None,
        cooldown_seconds: Optional[float] = None,
        stride: Optional[int] = None,
    ):
        self.detector = detector
        detection_cfg = detector.config.get("detection", {})

        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else float(detection_cfg.get("confidence_threshold", 0.70))
        )
        self.persistence_windows = (
            persistence_windows
            if persistence_windows is not None
            else int(detection_cfg.get("persistence_windows", 3))
        )
        self.cooldown_seconds = (
            cooldown_seconds
            if cooldown_seconds is not None
            else float(detection_cfg.get("cooldown_seconds", 60.0))
        )
        self.stride = stride or detector.stride
        self.num_frames = detector.num_frames
        self.frame_size = detector.frame_size

        # State buffers
        self.frame_buffer = collections.deque(maxlen=self.num_frames)
        self.window_history = collections.deque(maxlen=self.persistence_windows)
        self.last_alert_times: Dict[str, float] = {}
        self.frame_count = 0

    def reset(self) -> None:
        """Resets all buffers and state."""
        self.frame_buffer.clear()
        self.window_history.clear()
        self.last_alert_times.clear()
        self.frame_count = 0

    def add_frame(
        self, frame: np.ndarray, timestamp: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Receives a single live frame (RGB, HxWx3).
        Every `stride` frames, evaluates the 16-frame window.
        Returns an Alert dictionary if confidence, persistence, and cooldown conditions are satisfied.
        Otherwise returns None.
        """
        now = time.time() if timestamp is None else float(timestamp)

        # Pre-resize frame to target resolution
        if frame.shape[0] != self.frame_size or frame.shape[1] != self.frame_size:
            frame = cv2.resize(frame, (self.frame_size, self.frame_size), interpolation=cv2.INTER_LINEAR)

        self.frame_buffer.append(frame)
        self.frame_count += 1

        # Check if a window is ready to evaluate
        if len(self.frame_buffer) < self.num_frames or (self.frame_count % self.stride != 0):
            return None

        # Predict current window
        pred = self.detector.predict_clip(list(self.frame_buffer))
        label = pred["label"]
        confidence = pred["confidence"]

        # Record window in history
        self.window_history.append((label, confidence, now))

        # Condition 1: Must be non-Normal and satisfy confidence threshold
        if label == "Normal" or confidence < self.confidence_threshold:
            return None

        # Condition 2: Persistence (must have persistence_windows consecutive windows of this class)
        if len(self.window_history) < self.persistence_windows:
            return None

        recent_labels = [item[0] for item in self.window_history]
        recent_confs = [item[1] for item in self.window_history]

        all_match = all(l == label for l in recent_labels)
        all_above_thresh = all(c >= self.confidence_threshold for c in recent_confs)

        if not (all_match and all_above_thresh):
            return None

        # Condition 3: Cooldown check for this specific class
        last_alert = self.last_alert_times.get(label, -1e9)
        if (now - last_alert) < self.cooldown_seconds:
            return None

        # Alert triggered!
        self.last_alert_times[label] = now
        avg_confidence = float(np.mean(recent_confs))

        return {
            "alert": True,
            "class": label,
            "confidence": round(avg_confidence, 4),
            "severity": self.detector.get_severity(label),
            "timestamp": now,
            "consecutive_windows": self.persistence_windows,
            "all_probs": pred["all_probs"],
        }

