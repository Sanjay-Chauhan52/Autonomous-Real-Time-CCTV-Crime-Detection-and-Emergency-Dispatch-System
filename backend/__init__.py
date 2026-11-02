# Backend package (FastAPI)
# Stage 3: main.py          — app factory, mounts routers, serves frontend
# Stage 3: routers/infer.py — /api/analyze endpoint (upload video → prediction)
# Stage 3: routers/alert.py — /api/alerts endpoint (log + history)
# Stage 3: alerting.py      — send_email() and send_sms() helpers
# Stage 3: inference.py     — load model, preprocess, run prediction
