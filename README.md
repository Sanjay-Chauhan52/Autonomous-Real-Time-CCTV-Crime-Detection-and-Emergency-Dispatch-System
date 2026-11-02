# CrimeWatch: Autonomous AI Surveillance & CCTV Crime Detection System

CrimeWatch is an end-to-end, real-time AI surveillance pipeline engineered to detect violent and property crimes in CCTV video feeds and live webcam streams. When an incident is identified, CrimeWatch dispatches automated emergency alerts via SMTP email (with visual snapshot attachments) and Twilio SMS to designated security personnel and police authorities.

---

## 1. System Capabilities & Target Classes

CrimeWatch classifies 16-frame spatiotemporal video segments into **7 distinct categories**:

| Category | Severity Level | Alert Channels |
| :--- | :---: | :---: |
| **Shooting** | `HIGH` | Email + Twilio SMS |
| **Robbery** | `HIGH` | Email + Twilio SMS |
| **Assault** | `HIGH` | Email + Twilio SMS |
| **Fighting** | `HIGH` | Email + Twilio SMS |
| **Burglary** | `MEDIUM` | Email |
| **Shoplifting** | `MEDIUM` | Email |
| **Normal** | `NONE` | No Alert (Logged as background activity) |

---

## 2. Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │  CCTV Video Upload / Live WebCam Stream │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │    Decord / OpenCV Video Sampler       │
                      │  (16 frames sampled at 182x182 RGB)    │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │    PyTorchVideo X3D-M 3D-CNN Model     │
                      │  - Pretrained on Kinetics-400          │
                      │  - AdaptiveAvgPool3d((1,1,1)) Pooling  │
                      │  - CUDA FP16 Autocast Inference        │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │       StreamAnalyzer / Buffer Engine   │
                      │  - Rolling 16-frame sliding window     │
                      │  - Confidence Threshold Filter (>=0.70)│
                      │  - Temporal Persistence (3 consecutive)│
                      │  - Per-class Cooldown (60s default)    │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │           FastAPI Async Backend        │
                      │  - POST /api/analyze-video             │
                      │  - WebSocket /ws/live                  │
                      │  - Incident Persistence & SQLite ORM   │
                      └─────────────┬──────────────────────────┘
                                    │
            ┌───────────────────────┴───────────────────────┐
            ▼                                               ▼
┌───────────────────────────────┐               ┌───────────────────────────────┐
│     Multi-Channel Alerting    │               │    Tailwind / JS Dashboard    │
│ - SMTP Email with Snapshot    │               │ - Real-Time Live Monitor (WS) │
│ - Twilio SMS (HIGH severity)  │               │ - Video Upload & Timeline Jump│
│ - Non-blocking async dispatch │               │ - Incident Review & Feedback  │
└───────────────────────────────┘               │ - Contact & Threshold Config  │
                                                │ - Analytics & Confusion Matrix│
                                                └───────────────────────────────┘
```

### Key Technical Components
- **Deep Learning Backbone**: Pretrained `x3d_m` (PyTorchVideo) fine-tuned on CCTV footage with spatial adaptive pooling and mixed-precision fp16 execution.
- **Inference Engine (`backend/inference_engine.py`)**: Singleton `CrimeDetector` for batch/video evaluation and `StreamAnalyzer` with rolling FIFO frame buffers, 3-window consecutive trigger persistence, and configurable cooldown timers.
- **Alert Dispatcher (`backend/alerts.py`)**: Multi-channel non-blocking notification engine supporting HTML formatted emails with embedded JPEG snapshots and Twilio SMS.
- **Persistence Layer (`backend/db.py`)**: SQLAlchemy with SQLite (`outputs/crimewatch.db`) storing alert history, incident snapshots, and alert recipient contact registries.
- **Frontend (`frontend/index.html`)**: Responsive single-page dashboard built with Tailwind CSS, Chart.js, HTML5 Canvas, and WebSockets.

---

## 3. Windows 11 Setup Guide

### Prerequisites
- **Operating System**: Windows 10/11 64-bit
- **Python**: Version 3.10 to 3.12 (Python 3.12 recommended)
- **NVIDIA GPU**: NVIDIA GeForce RTX 2050 (4GB VRAM) or higher recommended, with CUDA 12.1+ installed. (CPU fallback is supported).
- **FFmpeg**: Required by `decord` and `moviepy` for video decoding.

### Step-by-Step Installation

1. **Clone or Navigate to the Workspace**:
   ```powershell
   cd c:\Users\thesa\Desktop\Sem-7\NLP\Crime_detection
   ```

2. **Create and Activate Python Virtual Environment**:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
   *(If PowerShell gives an execution policy error, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` first).*

3. **Install PyTorch with CUDA Support**:
   ```powershell
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
   ```

4. **Install System Dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```

5. **Initialize Environment Variables**:
   ```powershell
   copy .env.example .env
   ```

---

## 4. Credential Acquisition Guide

Before running alerts or downloading data, populate your `.env` configuration file:

### A. Kaggle API Credentials
Required for downloading the CCTV anomaly video dataset.

1. Navigate to [Kaggle](https://www.kaggle.com) and sign in to your account.
2. Click your profile picture in the top-right corner -> **Settings**.
3. Scroll down to the **API** section and click **Create New Token**.
4. A file named `kaggle.json` will download. It contains your `username` and `key`.
5. Place this file in `C:\Users\<YourUsername>\.kaggle\kaggle.json` or configure in `.env`:
   ```env
   KAGGLE_USERNAME=your_kaggle_username
   KAGGLE_KEY=your_kaggle_api_key
   ```

### B. Google Gmail App Password (SMTP Email)
Required for sending HTML email incident notifications with snapshot attachments.

1. Log in to your [Google Account](https://myaccount.google.com/).
2. Select **Security** from the left navigation panel.
3. Under *How you sign in to Google*, ensure **2-Step Verification** is turned ON.
4. Search for **App passwords** in the top search bar (or visit `https://myaccount.google.com/apppasswords`).
5. In the App name field, enter `CrimeWatch` and click **Create**.
6. Google will generate a 16-character passcode (e.g., `abcd efgh ijkl mnop`).
7. Copy this code into `.env`:
   ```env
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your_email@gmail.com
   SMTP_PASSWORD=abcdefghijklmnop
   ALERT_EMAIL_FROM=your_email@gmail.com
   ```

### C. Twilio SMS Credentials
Required for dispatching SMS notifications for `HIGH` severity crimes (`Shooting`, `Robbery`, `Assault`, `Fighting`).

1. Register or log in to the [Twilio Console](https://console.twilio.com/).
2. On the main Dashboard, locate **Account Info**:
   - **Account SID**: (Starts with `AC...`)
   - **Auth Token**: Click to reveal your secret token.
3. Acquire a Twilio phone number under **Phone Numbers** -> **Manage** -> **Active numbers** (or use your trial number).
4. Configure `.env`:
   ```env
   TWILIO_ACCOUNT_SID=ACXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   TWILIO_FROM_NUMBER=+1234567890
   ```

---

## 5. Running Each Pipeline Stage

### Stage 1: Selective Video Download
Downloads 20 videos per class from the Kaggle CCTV dataset, unzips them, and structures them into `data_prep/raw_videos/<ClassName>/`:
```powershell
python data_prep/download_selective.py --per_class 20
```

### Stage 2: Clip Preparation & Video-Level Splitting
Splits the 140 videos strictly by video into Train (70% - 14 videos), Val (15% - 3 videos), and Test (15% - 3 videos) to prevent data leakage. Samples 16-frame clips at 182x182 resolution:
```powershell
python data_prep/prepare_clips.py --samples_per_video 12 --num_frames 16 --frame_size 182
```
*Outputs: `clips_index.csv`, `train_clips.csv`, `val_clips.csv`, `test_clips.csv`, and `class_mapping.json`.*

### Stage 3: Train the X3D-M 3D-CNN Model
Fine-tunes PyTorchVideo X3D-M with backbone freezing for the first 3 epochs, class-weighted CrossEntropyLoss, AdamW optimizer, and fp16 mixed precision:
```powershell
python training/train_x3d.py --epochs 8 --batch_size 2 --lr 1e-4
```
*Outputs best checkpoint to: `models/x3d_crime.pt`.*

### Stage 4: Held-Out Test Split Evaluation
Evaluates `models/x3d_crime.pt` strictly on the held-out test split (21 completely unseen videos):
```powershell
python training/evaluate.py
```
*Outputs: `outputs/report.md`, `outputs/confusion_matrix.png`, and `outputs/training_curves.png`.*

### Stage 5: Start the Backend & Web Dashboard
Launches the FastAPI application via Uvicorn:
```powershell
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Open your browser and navigate to:
**`http://localhost:8000`**

### Stage 6: Automated End-to-End Test Suite
Runs validation across all endpoints, video analysis, websocket live streaming, simulated alerts, and recipient dispatching:
```powershell
python tests/test_e2e.py
```
*Outputs test verification report to `TESTING.md`.*

---

## 6. Model Evaluation Results

Evaluation was executed strictly on held-out test videos without test-set tuning.

### Overall Performance
- **Test Accuracy**: **34.92%**
- **Macro F1-Score**: **30.36%**
- **Weighted F1-Score**: **30.36%**
- **Test Set Size**: 252 clips across 21 unseen videos (3 per class, 36 clips each)

### Per-Class Performance Breakdown

| Class | Severity | Precision (%) | Recall (%) | F1-Score (%) | Test Clips |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Normal** | None | 75.0% | 100.0% | **85.7%** | 36 |
| **Burglary** | Medium | 35.3% | 50.0% | **41.4%** | 36 |
| **Shooting** | High | 20.3% | 41.7% | **27.3%** | 36 |
| **Robbery** | High | 20.7% | 33.3% | **25.5%** | 36 |
| **Fighting** | High | 60.0% | 8.3% | **14.6%** | 36 |
| **Shoplifting** | Medium | 33.3% | 8.3% | **13.3%** | 36 |
| **Assault** | High | 14.3% | 2.8% | **4.7%** | 36 |
| **Macro Average** | — | **37.0%** | **34.9%** | **30.4%** | **252** |
| **Weighted Average**| — | **37.0%** | **34.9%** | **30.4%** | **252** |

### Evaluation Artifacts
- **Confusion Matrix**: Saved in `outputs/confusion_matrix.png`
- **Loss & Accuracy Curves**: Saved in `outputs/training_curves.png`
- **Detailed Evaluation Report**: Saved in `outputs/report.md`

---

## 7. Limitations & Engineering Discussion

1. **Untrimmed Video Noise**:
   CCTV training videos are long and untrimmed. Because video-level labels are applied across uniformly sampled 16-frame clips, many clips capture quiet background periods before or after an incident occurs. This introduces label noise during supervised cross-entropy training.

2. **Spatial Resolution Constraints (182x182)**:
   To operate within the 4GB VRAM ceiling of an RTX 2050 GPU, input clips are downsampled to 182x182 pixels. At this resolution, subtle micro-actions such as drawing a concealed weapon (*Shooting*), pocketing an item (*Shoplifting*), or breaking a lock (*Burglary*) lose fine visual detail and become ambiguous.

3. **Data Scarcity & Domain Shift**:
   Training was conducted on 14 videos per class (98 total). This yields only 14 unique camera viewpoints, lighting conditions, and architectural settings per category. Evaluating on unseen test videos introduces significant environmental domain shift.

4. **False Positive Suppression in Production**:
   To prevent false alarms in operational settings, CrimeWatch incorporates three algorithmic safeguards:
   - **Rolling Window Persistence**: An alert is only triggered if 3 consecutive windows agree on an anomalous crime class.
   - **Configurable Confidence Thresholding**: Default threshold of `0.70` blocks marginal predictions.
   - **Per-Class Cooldown Period**: A 60-second cooldown prevents repeated notifications for the same ongoing incident.
   - **Human-in-the-Loop Feedback**: Security operators can confirm or dismiss alerts directly from the dashboard, saving verification records in SQLite for future active-learning retraining.
