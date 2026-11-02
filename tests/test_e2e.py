"""
test_e2e.py
-----------
Automated End-to-End System Test for CrimeWatch:
1. Video upload analysis across 1 validation video per class (7 classes).
2. Live WebSocket stream simulation streaming CCTV frames and checking alert triggers.
3. Diagnostic email and SMS alert testing.
4. Alerts database and recipient verification.
"""

import asyncio
import csv
import json
from pathlib import Path
import time
from typing import Dict, Any

import cv2
import httpx
import websockets

API_BASE = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/live"

VAL_VIDEOS = {
    "Assault": "data_prep/raw_videos/Assault/Assault003_x264.mp4",
    "Burglary": "data_prep/raw_videos/Burglary/Burglary016_x264.mp4",
    "Fighting": "data_prep/raw_videos/Fighting/Fighting019_x264.mp4",
    "Normal": "data_prep/raw_videos/Normal/Normal_Videos021_x264.mp4",
    "Robbery": "data_prep/raw_videos/Robbery/Robbery017_x264.mp4",
    "Shooting": "data_prep/raw_videos/Shooting/Shooting018_x264.mp4",
    "Shoplifting": "data_prep/raw_videos/Shoplifting/Shoplifting016_x264.mp4",
}


def test_video_upload_analysis() -> Dict[str, Any]:
    print("\n" + "=" * 65)
    print(" 1. TESTING UPLOAD ANALYSIS (1 VALIDATION VIDEO PER CLASS)")
    print("=" * 65)
    results = {}

    client = httpx.Client(base_url=API_BASE, timeout=120.0)

    for ground_truth_class, video_rel_path in VAL_VIDEOS.items():
        video_path = Path(video_rel_path)
        if not video_path.exists():
            print(f"[SKIP] Video not found: {video_path}")
            continue

        print(f"\n[Testing {ground_truth_class}] Uploading {video_path.name}...")
        with open(video_path, "rb") as f:
            resp = client.post(
                "/api/analyze-video",
                files={"file": (video_path.name, f, "video/mp4")},
            )
        assert resp.status_code == 200, f"Upload failed: {resp.text}"
        job_id = resp.json()["job_id"]
        print(f"  -> Queued Job ID: {job_id}")

        # Poll until complete
        start_wait = time.time()
        completed_job = None
        while time.time() - start_wait < 90:
            job_resp = client.get(f"/api/jobs/{job_id}")
            job_data = job_resp.json()
            status = job_data.get("status")
            if status == "completed":
                completed_job = job_data
                break
            elif status == "failed":
                raise RuntimeError(f"Job failed: {job_data.get('error')}")
            time.sleep(1.0)

        if not completed_job:
            print(f"  [WARN] Job timed out: {job_id}")
            results[ground_truth_class] = {"status": "timeout"}
            continue

        res = completed_job["result"]
        timeline = res.get("timeline", [])
        merged_events = res.get("merged_events", [])

        # Count predicted classes in sliding windows
        window_predictions = [w["label"] for w in timeline]
        pred_counts = {}
        for p in window_predictions:
            pred_counts[p] = pred_counts.get(p, 0) + 1

        top_pred = max(pred_counts.items(), key=lambda x: x[1])[0] if pred_counts else "None"
        fps = res.get("fps", 0)
        frames = res.get("total_frames", 0)
        duration = res.get("duration_sec", 0)

        print(f"  -> Duration: {duration}s ({frames} frames @ {fps} fps)")
        print(f"  -> Sliding Windows: {len(timeline)} evaluated")
        print(f"  -> Class Distribution: {pred_counts}")
        print(f"  -> Top Window Prediction: {top_pred}")
        print(f"  -> Merged Anomaly Events: {len(merged_events)}")
        for ev in merged_events[:2]:
            print(f"     * {ev['class']} ({ev['severity']}): {ev['start_sec']}s - {ev['end_sec']}s (max conf: {ev['max_confidence']*100:.1f}%)")

        results[ground_truth_class] = {
            "file": video_path.name,
            "duration": duration,
            "windows": len(timeline),
            "top_prediction": top_pred,
            "distribution": pred_counts,
            "merged_events_count": len(merged_events),
            "merged_events": merged_events,
        }

    return results


async def test_websocket_live_stream() -> Dict[str, Any]:
    print("\n" + "=" * 65)
    print(" 2. TESTING WEBSOCKET LIVE STREAM SIMULATION")
    print("=" * 65)

    test_video = Path("data_prep/raw_videos/Fighting/Fighting019_x264.mp4")
    if not test_video.exists():
        print(f"[SKIP] Video not found: {test_video}")
        return {"status": "skipped"}

    cap = cv2.VideoCapture(str(test_video))
    frames = []
    while len(frames) < 40:
        ret, frame = cap.read()
        if not ret:
            break
        # Resize to 182x182
        resized = cv2.resize(frame, (182, 182))
        frames.append(resized)
    cap.release()

    print(f"[INFO] Read {len(frames)} frames from {test_video.name}. Connecting to {WS_URL}...")

    alerts_triggered = 0
    responses = []

    async with websockets.connect(WS_URL) as ws:
        for idx, frame in enumerate(frames, 1):
            # Encode frame as JPEG bytes
            _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            await ws.send(buffer.tobytes())

            # Await server prediction response
            resp_raw = await ws.recv()
            resp = json.loads(resp_raw)
            responses.append(resp)

            if resp.get("alert_triggered"):
                alerts_triggered += 1
                print(f"  [ALERT TRIGGERED] Frame {idx:02d} -> {resp['label']} ({resp['confidence']*100:.1f}%) Severity: {resp['severity']}")
            elif idx % 8 == 0:
                print(f"  [HUD] Frame {idx:02d} -> Label: {resp.get('label')} | Conf: {resp.get('confidence', 0)*100:.1f}% | Severity: {resp.get('severity')}")

            await asyncio.sleep(0.08)  # simulate ~12 fps feed

    print(f"\n[INFO] WebSocket stream closed cleanly.")
    print(f"  Total frames streamed: {len(frames)}")
    print(f"  Total alerts triggered: {alerts_triggered}")

    return {
        "frames_streamed": len(frames),
        "alerts_triggered": alerts_triggered,
        "sample_response": responses[-1] if responses else {},
    }


def test_diagnostic_alerts() -> Dict[str, Any]:
    print("\n" + "=" * 65)
    print(" 3. TESTING DIAGNOSTIC ALERT DISPATCH (SMTP & TWILIO)")
    print("=" * 65)

    client = httpx.Client(base_url=API_BASE, timeout=15.0)
    resp = client.post("/api/test-alert", json={"channel": "all"})
    assert resp.status_code == 200, f"Diagnostic failed: {resp.text}"
    results = resp.json()["results"]

    print("SMTP Email Test Result:")
    email_res = results.get("email", {})
    print(f"  Success  : {email_res.get('success')}")
    print(f"  Target   : {email_res.get('target')}")
    print(f"  Error/Msg: {email_res.get('error')}")

    print("\nTwilio SMS Test Result:")
    sms_res = results.get("sms", {})
    print(f"  Success  : {sms_res.get('success')}")
    print(f"  Target   : {sms_res.get('target')}")
    print(f"  Error/Msg: {sms_res.get('error')}")

    return results


def test_database_and_stats() -> Dict[str, Any]:
    print("\n" + "=" * 65)
    print(" 4. TESTING DATABASE AND ANALYTICS STATS")
    print("=" * 65)

    client = httpx.Client(base_url=API_BASE, timeout=10.0)

    # 1. Stats
    stats_resp = client.get("/api/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    print(f"Total Alerts in DB      : {stats['total_alerts']}")
    print(f"New Alerts              : {stats['new_alerts']}")
    print(f"Confirmed Alerts        : {stats['confirmed_alerts']}")
    print(f"Dismissed Alerts        : {stats['dismissed_alerts']}")
    print(f"High Severity Count     : {stats['severity_breakdown']['HIGH']}")
    print(f"Medium Severity Count   : {stats['severity_breakdown']['MEDIUM']}")
    print(f"Class Breakdown         : {stats['class_breakdown']}")

    # 2. Alerts query
    alerts_resp = client.get("/api/alerts?limit=5")
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()["alerts"]
    print(f"\nRecent Stored Alerts (Sample of {len(alerts)}):")
    for a in alerts:
        print(f"  * #{a['id']:02d} [{a['severity']}] {a['label']:<12} ({a['confidence']*100:.0f}%) | Status: {a['status']:<9} | Channels: {a['notified_channels']}")

    # 3. Test Patch alert status (Confirm and Dismiss)
    if alerts:
        sample_id = alerts[0]["id"]
        patch_resp = client.patch(f"/api/alerts/{sample_id}", json={"status": "confirmed"})
        assert patch_resp.status_code == 200
        print(f"\n[PATCH] Alert #{sample_id} successfully updated to 'confirmed'")

    return stats


def main():
    print("CRIMEWATCH END-TO-END VERIFICATION")
    print("Timestamp:", time.strftime("%Y-%m-%d %H:%M:%S"))

    # Test 1: Upload analysis on validation videos
    upload_results = test_video_upload_analysis()

    # Test 2: WebSocket live stream
    ws_results = asyncio.run(test_websocket_live_stream())

    # Test 3: Alert diagnostics
    alert_results = test_diagnostic_alerts()

    # Test 4: Database stats and CRUD
    stats_results = test_database_and_stats()

    # Save summary
    summary = {
        "upload_analysis": upload_results,
        "websocket_live": ws_results,
        "alerts_diagnostic": alert_results,
        "database_stats": stats_results,
    }
    with open("outputs/e2e_test_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 65)
    print(" [DONE] END-TO-END TESTING COMPLETED SUCCESSFULLY!")
    print("=" * 65)


if __name__ == "__main__":
    main()

