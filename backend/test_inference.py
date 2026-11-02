"""
test_inference.py
-----------------
Unit tests for CrimeDetector and StreamAnalyzer using a dummy model.
Runs on CPU without requiring a GPU or actual model checkpoint.
"""

import unittest
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn

from backend.inference_engine import CrimeDetector, StreamAnalyzer, DEFAULT_CLASSES


class DummyModel(nn.Module):
    """
    Mock video model returning controlled logits for testing.
    """
    def __init__(self, target_class_idx: int = 2, target_confidence: float = 0.95):
        super().__init__()
        self.num_classes = len(DEFAULT_CLASSES)
        self.target_class_idx = target_class_idx
        self.target_confidence = target_confidence

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (B, C, T, H, W)
        batch_size = x.shape[0]
        logits = torch.ones((batch_size, self.num_classes), dtype=torch.float32) * -2.0
        logits[:, self.target_class_idx] = 10.0  # High confidence on target class
        return logits


class TestInferenceEngine(unittest.TestCase):
    def setUp(self):
        self.classes = DEFAULT_CLASSES
        # Target class: Fighting (index 2)
        self.dummy_model = DummyModel(target_class_idx=2)
        self.detector = CrimeDetector(
            model=self.dummy_model,
            classes=self.classes,
            device=torch.device("cpu"),
        )

    def test_predict_clip_format_and_probabilities(self):
        """Verify predict_clip outputs valid keys, probabilities, and severity."""
        frames = [np.random.randint(0, 255, (182, 182, 3), dtype=np.uint8) for _ in range(16)]
        res = self.detector.predict_clip(frames)

        self.assertIn("label", res)
        self.assertIn("confidence", res)
        self.assertIn("all_probs", res)
        self.assertIn("severity", res)

        # High confidence for Fighting
        self.assertEqual(res["label"], "Fighting")
        self.assertEqual(res["severity"], "HIGH")
        self.assertGreater(res["confidence"], 0.90)

        # Probabilities must sum close to 1.0
        prob_sum = sum(res["all_probs"].values())
        self.assertAlmostEqual(prob_sum, 1.0, places=2)

    def test_predict_clip_dynamic_resizing(self):
        """Verify predict_clip correctly resizes non-182x182 frames without error."""
        frames = [np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8) for _ in range(16)]
        res = self.detector.predict_clip(frames)
        self.assertEqual(res["label"], "Fighting")

    def test_severity_mapping(self):
        """Verify severity levels adhere to the user's specification."""
        self.assertEqual(self.detector.get_severity("Shooting"), "HIGH")
        self.assertEqual(self.detector.get_severity("Robbery"), "HIGH")
        self.assertEqual(self.detector.get_severity("Assault"), "HIGH")
        self.assertEqual(self.detector.get_severity("Fighting"), "HIGH")
        self.assertEqual(self.detector.get_severity("Shoplifting"), "MEDIUM")
        self.assertEqual(self.detector.get_severity("Burglary"), "MEDIUM")
        self.assertEqual(self.detector.get_severity("Normal"), "none")

    def test_analyze_video_timeline_and_merged_events(self):
        """Verify sliding window analysis and merging of contiguous anomaly events."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            temp_path = tf.name

        try:
            # Mock _read_all_frames to return 32 frames at 16 FPS (duration = 2.0s)
            test_frames = [np.zeros((182, 182, 3), dtype=np.uint8) for _ in range(32)]
            self.detector._read_all_frames = lambda path: (test_frames, 16.0)

            res = self.detector.analyze_video(temp_path, stride=8, confidence_threshold=0.70)

            self.assertEqual(res["total_frames"], 32)
            self.assertEqual(res["fps"], 16.0)
            self.assertEqual(res["duration_sec"], 2.0)

            # Windows: 0..16, 8..24, 16..32 -> 3 windows
            self.assertEqual(len(res["timeline"]), 3)

            # All 3 windows detect Fighting -> merged into 1 continuous event
            merged = res["merged_events"]
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["class"], "Fighting")
            self.assertEqual(merged[0]["severity"], "HIGH")
            self.assertEqual(merged[0]["start_sec"], 0.0)
            self.assertEqual(merged[0]["end_sec"], 2.0)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_stream_analyzer_persistence_and_cooldown(self):
        """Verify confidence threshold, 3-window persistence, and 60s cooldown logic."""
        analyzer = StreamAnalyzer(
            detector=self.detector,
            confidence_threshold=0.70,
            persistence_windows=3,
            cooldown_seconds=60.0,
            stride=8,
        )

        dummy_frame = np.zeros((182, 182, 3), dtype=np.uint8)
        current_time = 100.0

        # Step 1: Add 16 frames -> Window 1 evaluated (history length = 1)
        alert = None
        for _ in range(16):
            alert = analyzer.add_frame(dummy_frame, timestamp=current_time)
        self.assertIsNone(alert, "Window 1 should not trigger an alert (persistence < 3)")

        # Step 2: Add 8 frames -> Window 2 evaluated (history length = 2)
        current_time += 1.0
        for _ in range(8):
            alert = analyzer.add_frame(dummy_frame, timestamp=current_time)
        self.assertIsNone(alert, "Window 2 should not trigger an alert (persistence < 3)")

        # Step 3: Add 8 frames -> Window 3 evaluated (history length = 3) -> TRIGGER!
        current_time += 1.0
        for _ in range(8):
            alert = analyzer.add_frame(dummy_frame, timestamp=current_time)
        self.assertIsNotNone(alert, "Window 3 must trigger an alert (persistence = 3 met)")
        self.assertTrue(alert["alert"])
        self.assertEqual(alert["class"], "Fighting")
        self.assertEqual(alert["severity"], "HIGH")
        self.assertGreaterEqual(alert["confidence"], 0.70)

        # Step 4: Window 4 at time 110s (within 60s cooldown) -> SUPPRESSED
        current_time = 110.0
        for _ in range(8):
            alert = analyzer.add_frame(dummy_frame, timestamp=current_time)
        self.assertIsNone(alert, "Alert should be suppressed within 60s cooldown window")

        # Step 5: Window 5 at time 175s (> 60s cooldown) -> TRIGGER AGAIN!
        current_time = 175.0
        for _ in range(8):
            alert = analyzer.add_frame(dummy_frame, timestamp=current_time)
        self.assertIsNotNone(alert, "Alert should fire again after cooldown period expires")
        self.assertEqual(alert["class"], "Fighting")

    def test_stream_analyzer_normal_no_alert(self):
        """Verify Normal class never triggers an alert regardless of persistence."""
        # Index 3 is 'Normal'
        normal_model = DummyModel(target_class_idx=3)
        normal_detector = CrimeDetector(
            model=normal_model,
            classes=self.classes,
            device=torch.device("cpu"),
        )
        analyzer = StreamAnalyzer(
            detector=normal_detector,
            confidence_threshold=0.70,
            persistence_windows=3,
        )

        dummy_frame = np.zeros((182, 182, 3), dtype=np.uint8)
        # Feed 40 frames (more than 3 windows)
        alerts = []
        for i in range(40):
            res = analyzer.add_frame(dummy_frame, timestamp=float(i))
            if res is not None:
                alerts.append(res)

        self.assertEqual(len(alerts), 0, "Normal predictions should never generate alerts")


if __name__ == "__main__":
    unittest.main()
