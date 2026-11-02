"""
db.py
-----
SQLite database configuration and SQLAlchemy ORM models for CrimeWatch.

Tables:
- alerts: Stores all anomaly detection events with metadata, snapshots, and notification statuses.
- recipients: Registered personnel (police, invigilators, managers) receiving alerts.
"""

from datetime import datetime
import os
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Database path resolution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "outputs" / "crimewatch.db"))
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# -------------------------------------------------------------------------------
# ORM Models
# -------------------------------------------------------------------------------

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    source = Column(String(128), default="CCTV Feed", nullable=False)
    label = Column(String(64), nullable=False, index=True)
    confidence = Column(Float, nullable=False)
    severity = Column(String(32), nullable=False, index=True)  # HIGH, MEDIUM, none
    snapshot_path = Column(String(512), nullable=True)
    clip_path = Column(String(512), nullable=True)
    status = Column(String(32), default="new", nullable=False)  # new, confirmed, dismissed
    notified_channels = Column(String(128), default="", nullable=False)  # "email,sms", "email", etc.

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "source": self.source,
            "label": self.label,
            "confidence": round(self.confidence, 4) if self.confidence else 0.0,
            "severity": self.severity,
            "snapshot_path": self.snapshot_path,
            "clip_path": self.clip_path,
            "status": self.status,
            "notified_channels": self.notified_channels,
        }


class Recipient(Base):
    __tablename__ = "recipients"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    role = Column(String(64), nullable=False)  # police, invigilator, manager
    email = Column(String(256), nullable=True)
    phone = Column(String(64), nullable=True)
    min_severity = Column(String(32), default="MEDIUM", nullable=False)  # HIGH, MEDIUM, none
    active = Column(Boolean, default=True, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "email": self.email,
            "phone": self.phone,
            "min_severity": self.min_severity,
            "active": self.active,
        }


# -------------------------------------------------------------------------------
# Database Lifecycle & Helpers
# -------------------------------------------------------------------------------

def init_db() -> None:
    """Creates database parent directories and initializes all tables."""
    db_file = Path(DB_PATH)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    seed_default_recipients()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for yielding database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_default_recipients() -> None:
    """Seeds initial alert recipients if the table is empty."""
    db = SessionLocal()
    try:
        count = db.query(Recipient).count()
        if count == 0:
            default_email = os.getenv("ALERT_TO", "police_station@example.com").split(",")[0].strip()
            default_phone = os.getenv("TWILIO_TO_NUMBERS", "+15551234567").split(",")[0].strip()

            initial_recipients = [
                Recipient(
                    name="Local Police Station / Dispatch",
                    role="police",
                    email=default_email,
                    phone=default_phone,
                    min_severity="HIGH",
                    active=True,
                ),
                Recipient(
                    name="Chief Campus Invigilator",
                    role="invigilator",
                    email=default_email,
                    phone=default_phone,
                    min_severity="MEDIUM",
                    active=True,
                ),
                Recipient(
                    name="Security Operations Manager",
                    role="manager",
                    email=default_email,
                    phone=default_phone,
                    min_severity="MEDIUM",
                    active=True,
                ),
            ]
            db.add_all(initial_recipients)
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print(f"[INFO] Initialized CrimeWatch database at: {DB_PATH}")

