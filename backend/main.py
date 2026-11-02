"""
main.py
-------
FastAPI application for CrimeWatch: Real-time CCTV Crime Detection & Multi-Channel Alerting.

Endpoints:
- POST /api/analyze-video: Upload video and run background analysis.
- GET  /api/jobs/{id}: Query analysis job status, progress, timeline, and detected events.
- WS   /ws/live: Live webcam/CCTV WebSocket feed (~5-8 fps JPEG) with real-time detection & alerting.
- GET  /api/alerts: Query stored incident alerts.
- PATCH /api/alerts/{id}: Confirm or dismiss an alert.
- CRUD /api/recipients: Manage alert notification contacts.
- POST /api/test-alert: Test SMTP and Twilio credentials from UI.
- GET  /api/stats: Dashboard summary analytics.
- GET  /health: System diagnostic check.
"""

from contextlib import asynccontextmanager
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from dotenv import load_dotenv
load_dotenv()

import cv2
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from backend.alerts import dispatch_alert, test_alert
from backend.db import Alert, Recipient, get_db, init_db
from backend.inference_engine import CrimeDetector, StreamAnalyzer

try:
    from backend.inference_videomae import VideoMAEDetector
except Exception:
    VideoMAEDetector = None

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [CrimeWatch-Server] %(message)s",
)
logger = logging.getLogger("crimewatch.server")

# Project directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
SNAPSHOTS_DIR = OUTPUTS_DIR / "snapshots"
CLIPS_DIR = OUTPUTS_DIR / "clips"
UPLOADS_DIR = OUTPUTS_DIR / "uploads"

for d in (SNAPSHOTS_DIR, CLIPS_DIR, UPLOADS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# In-memory background jobs registry
jobs_db: Dict[str, Dict[str, Any]] = {}


# -------------------------------------------------------------------------------
# Lifespan: Model initialization
# -------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    init_db()

    use_videomae = os.getenv("USE_VIDEOMAE", "0").lower() in ("1", "true")
    if use_videomae and VideoMAEDetector is not None:
        logger.info("Loading VideoMAE Crime Detector model...")
        try:
            app.state.detector = VideoMAEDetector()
            logger.info(f"VideoMAE model successfully loaded on device: {app.state.detector.device}")
        except Exception as e:
            logger.warning(f"VideoMAE failed to load ({e}), falling back to CrimeDetector X3D-M.")
            app.state.detector = CrimeDetector()
    else:
        logger.info("Loading CrimeDetector X3D-M model...")
        try:
            app.state.detector = CrimeDetector()
            logger.info(f"Model successfully loaded on device: {app.state.detector.device}")
        except Exception as e:
            logger.error(f"Failed to load model on startup: {e}")
            # Initialize with fallback on CPU
            app.state.detector = CrimeDetector(checkpoint_path="models/x3d_crime.pt")

    yield

    logger.info("Shutting down CrimeWatch application...")


app = FastAPI(
    title="CrimeWatch API",
    description="Intelligent CCTV Crime Detection and Automated Alert System",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static file serving for forensic snapshots, video clips, and outputs
app.mount("/static/snapshots", StaticFiles(directory=str(SNAPSHOTS_DIR)), name="snapshots")
app.mount("/static/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")
app.mount("/static/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


@app.get("/", response_class=FileResponse)
def serve_frontend_index():
    index_path = PROJECT_ROOT / "frontend" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found")
    return FileResponse(index_path)


# -------------------------------------------------------------------------------
# Pydantic Schemas
# -------------------------------------------------------------------------------

class AlertUpdate(BaseModel):
    status: str = Field(..., description="new, confirmed, or dismissed")


class RecipientCreate(BaseModel):
    name: str
    role: str = Field("police", description="police, invigilator, or manager")
    email: Optional[str] = None
    phone: Optional[str] = None
    min_severity: str = Field("MEDIUM", description="HIGH, MEDIUM, or none")
    active: bool = True


class RecipientUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    min_severity: Optional[str] = None
    active: Optional[bool] = None


class TestAlertRequest(BaseModel):
    channel: str = Field("all", description="all, email, or sms")
    email: Optional[str] = None
    phone: Optional[str] = None


# -------------------------------------------------------------------------------
# Diagnostic & Health Route
# -------------------------------------------------------------------------------

@app.get("/health")
def health_check():
    detector = getattr(app.state, "detector", None)
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "model_loaded": detector is not None,
        "model_name": type(detector).__name__ if detector else "None",
        "device": str(detector.device) if detector else "uninitialized",
        "classes": detector.classes if detector else [],
    }


# -------------------------------------------------------------------------------
# Video Analysis Endpoints
# -------------------------------------------------------------------------------

def ensure_h264(input_path: Path) -> Path:
    """Ensures video is encoded in H.264 MP4 format with +faststart for universal browser playback."""
    try:
        import subprocess
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        output_path = input_path.parent / f"web_{input_path.stem}.mp4"
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path

        cmd = [
            ffmpeg_exe, "-y",
            "-i", str(input_path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-crf", "26",
            "-an",
            "-movflags", "+faststart",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return output_path
    except Exception as e:
        logger.warning(f"Video transcoding notice for {input_path.name}: {e}")
    return input_path


def _save_clip_and_snapshot(
    video_path: Path, start_sec: float, end_sec: float, event_id: str
) -> Tuple[Optional[str], Optional[str]]:
    """Extracts a snapshot image and a forensic clip from the source video."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None, None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    start_frame = int(start_sec * fps)
    end_frame = max(start_frame + 16, int(end_sec * fps))

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    snapshot_rel = None
    clip_rel = None

    frames = []
    for _ in range(end_frame - start_frame):
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)

    cap.release()

    if frames:
        # 1. Save mid-point frame as snapshot
        snap_idx = len(frames) // 2
        snap_path = SNAPSHOTS_DIR / f"snap_{event_id}.jpg"
        cv2.imwrite(str(snap_path), frames[snap_idx])
        snapshot_rel = f"/static/snapshots/{snap_path.name}"

        # 2. Save video clip with web-friendly encoding
        temp_clip = CLIPS_DIR / f"temp_{event_id}.mp4"
        clip_path = CLIPS_DIR / f"clip_{event_id}.mp4"
        h, w, _ = frames[0].shape
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(str(temp_clip), fourcc, min(fps, 30.0), (w, h))
        for f in frames[:64]:  # limit to ~2-3 seconds
            out.write(f)
        out.release()

        # Convert to browser-friendly H.264
        try:
            import imageio_ffmpeg, subprocess
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [
                ffmpeg_exe, "-y", "-i", str(temp_clip),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "ultrafast", "-crf", "28", "-an",
                "-movflags", "+faststart",
                str(clip_path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if res.returncode == 0 and clip_path.exists():
                temp_clip.unlink(missing_ok=True)
            else:
                temp_clip.replace(clip_path)
        except Exception:
            temp_clip.replace(clip_path)

        clip_rel = f"/static/clips/{clip_path.name}"

    return snapshot_rel, clip_rel


def _process_video_job(
    job_id: str, video_path: Path, confidence_threshold: Optional[float] = 0.25
):
    """Background task to run video analysis and dispatch alerts."""
    detector: CrimeDetector = app.state.detector
    jobs_db[job_id]["status"] = "processing"
    jobs_db[job_id]["progress"] = 15

    try:
        res = detector.analyze_video(video_path, confidence_threshold=confidence_threshold)
        jobs_db[job_id]["progress"] = 70

        # Transcode uploaded video to browser-native H.264 MP4 with +faststart
        web_video = ensure_h264(video_path)
        res["web_video_filename"] = web_video.name
        jobs_db[job_id]["progress"] = 85

        # Generate database alerts for top merged anomaly events (limit to top 2 to avoid spam)
        from backend.db import SessionLocal
        db = SessionLocal()
        created_alerts = []

        try:
            top_events = sorted(
                res.get("merged_events", []), key=lambda x: x["max_confidence"], reverse=True
            )[:2]
            for event in top_events:
                event_uid = uuid.uuid4().hex[:8]
                snap_path, clip_path = _save_clip_and_snapshot(
                    video_path, event["start_sec"], event["end_sec"], event_uid
                )

                db_alert = Alert(
                    source=video_path.name,
                    label=event["class"],
                    confidence=event["max_confidence"],
                    severity=event["severity"],
                    snapshot_path=snap_path,
                    clip_path=clip_path,
                    status="new",
                )
                db.add(db_alert)
                db.commit()
                db.refresh(db_alert)

                # Dispatch notifications (Email & SMS)
                dispatch_alert(db_alert, db_session=db)
                created_alerts.append(db_alert.to_dict())
        finally:
            db.close()

        res["created_alerts"] = created_alerts
        jobs_db[job_id]["status"] = "completed"
        jobs_db[job_id]["progress"] = 100
        jobs_db[job_id]["result"] = res
        logger.info(f"Job {job_id} completed successfully. Found {len(created_alerts)} anomaly events.")

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        jobs_db[job_id]["status"] = "failed"
        jobs_db[job_id]["error"] = str(e)


@app.post("/api/analyze-video")
async def analyze_video_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    confidence_threshold: Optional[float] = Form(0.25),
):
    """Accepts video file upload, queues background analysis, and returns job id."""
    job_id = uuid.uuid4().hex
    safe_filename = f"{job_id}_{file.filename}"
    saved_path = UPLOADS_DIR / safe_filename

    # Save uploaded bytes to disk
    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    jobs_db[job_id] = {
        "job_id": job_id,
        "filename": file.filename,
        "status": "queued",
        "progress": 0,
        "created_at": datetime.utcnow().isoformat(),
        "result": None,
        "error": None,
    }

    background_tasks.add_task(_process_video_job, job_id, saved_path, confidence_threshold)
    return {"job_id": job_id, "status": "queued", "filename": file.filename}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Returns analysis progress, status, and detected events for a job."""
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs_db[job_id]


# -------------------------------------------------------------------------------
# Live CCTV WebSocket Stream Endpoint
# -------------------------------------------------------------------------------

@app.websocket("/ws/live")
async def live_stream_websocket(
    websocket: WebSocket,
    confidence_threshold: Optional[float] = 0.22,
    persistence_windows: Optional[int] = 2,
    db: Session = Depends(get_db),
):
    """
    Receives JPEG camera frames (~5-8 fps) from client.
    Evaluates StreamAnalyzer rolling buffer; responds with live detection state
    and triggers forensic snapshots + alerts when persistence threshold is met.
    """
    await websocket.accept()
    detector: CrimeDetector = app.state.detector
    analyzer = StreamAnalyzer(
        detector=detector,
        confidence_threshold=confidence_threshold,
        persistence_windows=persistence_windows,
    )
    logger.info("Client connected to live CCTV WebSocket stream.")

    frame_history: List[np.ndarray] = []

    try:
        while True:
            # Receive frame data (either raw bytes or base64)
            message = await websocket.receive()
            if "bytes" in message and message["bytes"]:
                data = message["bytes"]
            elif "text" in message and message["text"]:
                import base64
                text_data = message["text"]
                if "," in text_data:
                    text_data = text_data.split(",", 1)[1]
                data = base64.b64decode(text_data)
            else:
                continue

            # Decode JPEG
            nparr = np.frombuffer(data, np.uint8)
            bgr_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if bgr_frame is None:
                continue

            rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            frame_history.append(bgr_frame)
            if len(frame_history) > 32:
                frame_history.pop(0)

            # Analyze frame in rolling buffer
            alert_event = analyzer.add_frame(rgb_frame)

            response = {
                "alert_triggered": False,
                "label": "Analyzing...",
                "confidence": 0.0,
                "severity": "none",
                "all_probs": {},
            }

            # Retrieve most recent prediction
            if analyzer.window_history:
                last_lbl, last_cnf, _ = analyzer.window_history[-1]
                response["label"] = last_lbl
                response["confidence"] = round(last_cnf, 4)
                response["severity"] = detector.get_severity(last_lbl)

            if alert_event:
                response["alert_triggered"] = True
                response["alert_info"] = alert_event

                # Save forensic snapshot
                alert_uid = uuid.uuid4().hex[:8]
                snap_path = SNAPSHOTS_DIR / f"live_{alert_uid}.jpg"
                cv2.imwrite(str(snap_path), bgr_frame)
                snap_rel = f"/static/snapshots/{snap_path.name}"

                # Save short forensic clip
                clip_rel = None
                if len(frame_history) >= 16:
                    clip_path = CLIPS_DIR / f"live_{alert_uid}.mp4"
                    h, w, _ = frame_history[0].shape
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    out = cv2.VideoWriter(str(clip_path), fourcc, 15.0, (w, h))
                    for f in frame_history[-24:]:
                        out.write(f)
                    out.release()
                    clip_rel = f"/static/clips/{clip_path.name}"

                # Commit to DB
                db_alert = Alert(
                    source="Live CCTV Stream 01",
                    label=alert_event["class"],
                    confidence=alert_event["confidence"],
                    severity=alert_event["severity"],
                    snapshot_path=snap_rel,
                    clip_path=clip_rel,
                    status="new",
                )
                db.add(db_alert)
                db.commit()
                db.refresh(db_alert)

                # Dispatch notifications asynchronously
                dispatch_alert(db_alert, db_session=db)
                logger.info(f"Live Alert Triggered: {alert_event['class']} ({alert_event['confidence']})")

            await websocket.send_json(response)

    except WebSocketDisconnect:
        logger.info("Live WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket session error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


# -------------------------------------------------------------------------------
# Alerts Management Endpoints
# -------------------------------------------------------------------------------

@app.get("/api/alerts")
def list_alerts(
    status_filter: Optional[str] = None,
    severity_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Returns stored anomaly alerts ordered by most recent first."""
    query = db.query(Alert)
    if status_filter:
        query = query.filter(Alert.status == status_filter)
    if severity_filter:
        query = query.filter(Alert.severity == severity_filter)

    total = query.count()
    alerts = query.order_by(Alert.timestamp.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "alerts": [a.to_dict() for a in alerts],
    }


@app.patch("/api/alerts/{alert_id}")
def update_alert_status(alert_id: int, payload: AlertUpdate, db: Session = Depends(get_db)):
    """Updates an alert status to 'confirmed' or 'dismissed'."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    valid_statuses = {"new", "confirmed", "dismissed"}
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")

    alert.status = payload.status
    db.commit()
    db.refresh(alert)
    return {"message": "Alert status updated", "alert": alert.to_dict()}


@app.delete("/api/alerts")
def clear_all_alerts(db: Session = Depends(get_db)):
    """Clears all stored alerts from the database."""
    deleted_count = db.query(Alert).delete()
    db.commit()
    return {"message": "All alerts cleared successfully", "deleted_count": deleted_count}


# -------------------------------------------------------------------------------
# Recipients Management Endpoints (CRUD)
# -------------------------------------------------------------------------------

@app.get("/api/recipients")
def get_recipients(db: Session = Depends(get_db)):
    """Lists all configured notification recipients."""
    recipients = db.query(Recipient).all()
    return [r.to_dict() for r in recipients]


@app.post("/api/recipients", status_code=status.HTTP_201_CREATED)
def create_recipient(payload: RecipientCreate, db: Session = Depends(get_db)):
    """Registers a new alert recipient."""
    recipient = Recipient(
        name=payload.name,
        role=payload.role,
        email=payload.email,
        phone=payload.phone,
        min_severity=payload.min_severity,
        active=payload.active,
    )
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return recipient.to_dict()


@app.patch("/api/recipients/{recipient_id}")
def update_recipient(recipient_id: int, payload: RecipientUpdate, db: Session = Depends(get_db)):
    """Modifies an existing notification recipient."""
    recipient = db.query(Recipient).filter(Recipient.id == recipient_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(recipient, key, value)

    db.commit()
    db.refresh(recipient)
    return recipient.to_dict()


@app.delete("/api/recipients/{recipient_id}")
def delete_recipient(recipient_id: int, db: Session = Depends(get_db)):
    """Removes a notification recipient."""
    recipient = db.query(Recipient).filter(Recipient.id == recipient_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    db.delete(recipient)
    db.commit()
    return {"message": "Recipient deleted successfully"}


# -------------------------------------------------------------------------------
# Diagnostic & Verification Endpoint
# -------------------------------------------------------------------------------

@app.post("/api/test-alert")
def trigger_test_alert(payload: TestAlertRequest):
    """Triggers a simulated test alert to verify email and SMS connectivity."""
    results = test_alert(
        channel=payload.channel,
        test_email=payload.email,
        test_phone=payload.phone,
    )
    return {
        "status": "completed",
        "results": results,
    }


# -------------------------------------------------------------------------------
# Dashboard Analytics Endpoint
# -------------------------------------------------------------------------------

@app.get("/api/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Returns aggregated analytics for the CrimeWatch security dashboard."""
    total_alerts = db.query(Alert).count()
    new_alerts = db.query(Alert).filter(Alert.status == "new").count()
    confirmed_alerts = db.query(Alert).filter(Alert.status == "confirmed").count()
    dismissed_alerts = db.query(Alert).filter(Alert.status == "dismissed").count()

    high_sev = db.query(Alert).filter(Alert.severity == "HIGH").count()
    med_sev = db.query(Alert).filter(Alert.severity == "MEDIUM").count()

    # Class distribution
    from sqlalchemy import func
    class_counts = (
        db.query(Alert.label, func.count(Alert.id))
        .group_by(Alert.label)
        .all()
    )
    class_breakdown = {lbl: count for lbl, count in class_counts}

    total_recipients = db.query(Recipient).filter(Recipient.active == True).count()

    detector: Optional[CrimeDetector] = getattr(app.state, "detector", None)

    return {
        "total_alerts": total_alerts,
        "new_alerts": new_alerts,
        "confirmed_alerts": confirmed_alerts,
        "dismissed_alerts": dismissed_alerts,
        "severity_breakdown": {
            "HIGH": high_sev,
            "MEDIUM": med_sev,
        },
        "class_breakdown": class_breakdown,
        "active_recipients": total_recipients,
        "system": {
            "device": str(detector.device) if detector else "CPU",
            "model": "PyTorchVideo X3D-M",
            "classes_monitored": len(detector.classes) if detector else 7,
            "status": "operational",
        },
    }
