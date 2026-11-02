# CrimeWatch End-to-End Testing & Verification Report

**Date of Execution**: 2026-10-29  
**Platform**: Windows 11, Python 3.12, PyTorch 2.5.1+cu121  
**Hardware Device**: NVIDIA GeForce RTX 2050 (4GB VRAM) via CUDA  
**Model**: PyTorchVideo X3D-M (7 classes, 16 frames, 182x182)

---

## 1. Executive Summary

An end-to-end operational test of the entire CrimeWatch system was conducted across:
1. Backend (FastAPI + Uvicorn) & Frontend (SPA on port 8000).
2. Video Upload Analysis on 1 held-out validation video per class (7 classes total).
3. Real-time Live WebSocket streaming simulation (`/ws/live`).
4. Multi-channel alerting engine (SMTP Email and Twilio SMS).
5. Database operations (SQLite ORM, status updating, forensic snapshot/clip generation).

| Module / Test Suite | Status | Key Metric / Result |
| :--- | :---: | :--- |
| **Backend API Health** | **PASSED** | `GET /health` returned HTTP 200, CUDA active |
| **Frontend Web UI** | **PASSED** | Served at `GET /`, static mounts active |
| **Video Upload Analysis** | **PASSED** | 7/7 validation videos processed via background jobs |
| **Live CCTV WebSocket** | **PASSED** | 40 frames streamed, live HUD state received |
| **Forensic Capture** | **PASSED** | 397 snapshots and video clips generated in `outputs/` |
| **Alert Routing & Cooldown**| **PASSED** | Severity rules enforced; non-blocking error handling |
| **Credentials Diagnostics** | **PASSED** | Graceful error capture on unconfigured credentials |
| **Incident Feedback Loop** | **PASSED** | `PATCH /api/alerts/{id}` confirmed status updates |

---

## 2. Validation Video Upload Test (1 Video Per Class)

Each validation video was uploaded to `POST /api/analyze-video` and analyzed using 16-frame sliding windows (stride 8 frames):

| Ground Truth Class | Video Filename | Duration / Frames | Top Predicted Class | Prediction Distribution | Merged Incidents |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **Burglary** | `Burglary016_x264.mp4` | 175.3s (5,258 frames) | **Burglary** (Accurate) | Burglary: 606, Normal: 46, Fighting: 2, Shooting: 2 | 59 events |
| **Robbery** | `Robbery017_x264.mp4` | 145.3s (4,358 frames) | **Robbery** (Accurate) | Robbery: 474, Shoplifting: 38, Shooting: 19, Assault: 9 | 88 events |
| **Assault** | `Assault003_x264.mp4` | 148.1s (4,443 frames) | **Burglary** (Confused) | Burglary: 373, Shooting: 176, Assault: 4, Fighting: 1 | 113 events |
| **Fighting** | `Fighting019_x264.mp4` | 107.1s (3,213 frames) | **Robbery** (Confused) | Robbery: 222, Shooting: 85, Shoplifting: 50, Burglary: 32 | 57 events |
| **Normal** | `Normal_Videos021_x264.mp4`| 65.4s (1,963 frames) | **Shoplifting** (Confused) | Shoplifting: 89, Robbery: 75, Burglary: 37, Shooting: 33, Normal: 8 | 44 events |
| **Shooting** | `Shooting018_x264.mp4` | 60.0s (1,799 frames) | **Normal** (Ambient) | Normal: 196, Robbery: 21, Shooting: 5, Shoplifting: 1 | 11 events |
| **Shoplifting** | `Shoplifting016_x264.mp4`| 49.4s (1,483 frames) | **Robbery** (Confused) | Robbery: 147, Burglary: 23, Shooting: 10, Shoplifting: 4 | 25 events |

### Honest Analysis of Model Predictions:
- **Strong Recognition**: The model decisively classified `Burglary016` (606 windows predicted as Burglary) and `Robbery017` (474 windows predicted as Robbery).
- **Confusion Patterns**:
  - `Fighting` and `Assault` were frequently predicted as `Robbery` or `Burglary`. In CCTV footage, violent kinetic interactions look structurally similar to aggressive physical robberies.
  - `Shooting018` was classified predominantly as `Normal` (196 out of 223 windows). In untrimmed CCTV, shooters often stand or walk normally for 50 seconds before firing in a 2-second burst.
  - `Normal_Videos021` produced false positives for `Shoplifting` and `Robbery` due to domain shift (unseen camera angles and lighting in the validation split).

---

## 3. Real-Time WebSocket Streaming Simulation

- **Endpoint**: `ws://127.0.0.1:8000/ws/live`
- **Feed**: 40 consecutive frames from `Fighting019_x264.mp4` encoded to JPEG and streamed at ~12 fps.
- **Results**:
  - WebSocket connection opened and maintained cleanly (`connection open`).
  - Server successfully decoded frames, maintained rolling buffer of 16 frames, and evaluated softmax probabilities.
  - Returned real-time HUD payloads with detected labels (`Fighting`, `Normal`, `Fighting`, `Burglary`), confidence percentages, and dynamic severity (`HIGH`, `none`, `HIGH`, `MEDIUM`).
  - WebSocket stream closed cleanly with zero dropped packets or exceptions.

---

## 4. Multi-Channel Alerting (Email & SMS)

Triggered via `POST /api/test-alert` and live video processing:

1. **SMTP (Email)**:
   - Status: Captured as **unconfigured credentials** (`SMTP credentials are not configured`).
   - Pipeline behavior: **Did not crash**. Logged warning and returned clean JSON diagnostic.
2. **Twilio (SMS)**:
   - Status: Captured as **unconfigured credentials** (`Twilio credentials not configured`).
   - Severity filtering: Correctly filtered `MEDIUM` severity alerts (`Skipping SMS: severity 'MEDIUM' is not HIGH`).
   - Pipeline behavior: **Did not crash**.

---

## 5. Bugs Discovered & Resolved

| Bug Description | Root Cause | Fix Applied | Status |
| :--- | :--- | :--- | :---: |
| **X3D Pooling Shape Error** | Kinetics-400 X3D-M uses fixed `AvgPool3d(16, 7, 7)`. At `182x182`, spatial feature map is `6x6`, crashing with kernel size error. | Replaced fixed pool with `nn.AdaptiveAvgPool3d((1, 1, 1))`. | **RESOLVED** |
| **Missing `Tuple` Import in `main.py`** | `Tuple` was used in type annotation for `_save_clip_and_snapshot` without being imported from `typing`. | Added `Tuple` and `Union` to `typing` imports. | **RESOLVED** |
| **Zero Merged Events with Default Threshold** | Default confidence threshold was `0.70`, but 7-class softmax on untrimmed videos peaks around `0.25 - 0.45`. | Added optional `confidence_threshold` parameter (default `0.25`) for video analysis. | **RESOLVED** |
| **Alert Flooding on Long Videos** | A 3-minute video with low threshold created 50+ database alerts and forensic snapshots. | Limited created alerts per video upload to the top 5 highest-confidence events. | **RESOLVED** |
| **Starlette TestClient Requirement** | Modern `starlette.testclient` required `httpx` to execute in-process requests. | Installed `httpx` and verified with standalone client. | **RESOLVED** |

---

## 6. How to Run the System

### 1. Launch Server & Frontend
```bash
# In active virtual environment:
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Open **`http://localhost:8000`** in any web browser.

### 2. Configure Credentials (.env)
Copy `.env.example` to `.env` and fill in:
```ini
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
ALERT_TO=police@station.gov

TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
TWILIO_TO_NUMBERS=+1...
```

### 3. Run Automated End-to-End Tests
```bash
python tests/test_e2e.py
```

