"""
alerts.py
---------
Multi-channel alerting engine for CrimeWatch.

Features:
- send_email: Dispatches emails via SMTP with plain-language detection reason and attached snapshot.
- send_sms: Dispatches urgent SMS via Twilio for HIGH severity alerts only.
- dispatch_alert: Routes alerts based on recipient min_severity thresholds.
- Fault-tolerant: Pipeline never crashes if an alert channel fails; records succeeded channels.
- test_alert: Credential verification endpoint/function for UI health checks.
"""

from email.message import EmailMessage
from email.utils import formatdate
import logging
import mimetypes
import os
from pathlib import Path
import smtplib
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv

# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Logging setup
logger = logging.getLogger("crimewatch.alerts")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [CrimeWatch-Alerts] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Severity ordering for filtering
SEVERITY_RANK = {
    "none": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


def should_notify_recipient(alert_severity: str, min_severity: str) -> bool:
    """Checks if alert severity meets or exceeds recipient minimum severity."""
    alert_rank = SEVERITY_RANK.get(alert_severity.upper(), 0) if alert_severity else 0
    rec_rank = SEVERITY_RANK.get(min_severity.upper(), 2) if min_severity else 2
    return alert_rank >= rec_rank


def build_plain_language_reason(label: str, confidence: float, timestamp_str: str, source: str) -> str:
    """Builds a human-readable detection summary."""
    conf_pct = int(round(confidence * 100))
    return f"Possible {label} detected ({conf_pct}% confidence) at {timestamp_str} from {source}."


# -------------------------------------------------------------------------------
# Email Alerting (SMTP)
# -------------------------------------------------------------------------------

def send_email(alert: Any, recipients: List[Any]) -> Tuple[bool, List[str], Optional[str]]:
    """
    Sends an alert email via SMTP with snapshot attachment to eligible recipients.
    Returns: (success: bool, delivered_emails: list[str], error_message: str | None)
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    alert_from = os.getenv("ALERT_FROM", smtp_user)

    if not smtp_user or not smtp_password:
        msg = "SMTP credentials (SMTP_USER/SMTP_PASSWORD) are not configured."
        logger.warning(msg)
        return False, [], msg

    # Filter recipients with valid email addresses and eligible min_severity
    eligible_emails = []
    for r in recipients:
        email = getattr(r, "email", None) or (r.get("email") if isinstance(r, dict) else None)
        active = getattr(r, "active", True) if hasattr(r, "active") else r.get("active", True)
        min_sev = getattr(r, "min_severity", "MEDIUM") if hasattr(r, "min_severity") else r.get("min_severity", "MEDIUM")

        alert_sev = getattr(alert, "severity", "none") if hasattr(alert, "severity") else alert.get("severity", "none")
        if email and active and should_notify_recipient(alert_sev, min_sev):
            if email not in eligible_emails:
                eligible_emails.append(email)

    if not eligible_emails:
        logger.info("No eligible email recipients found for this alert severity.")
        return False, [], "No eligible recipients."

    # Extract alert attributes
    label = getattr(alert, "label", "Anomaly") if hasattr(alert, "label") else alert.get("label", "Anomaly")
    confidence = getattr(alert, "confidence", 0.0) if hasattr(alert, "confidence") else alert.get("confidence", 0.0)
    severity = getattr(alert, "severity", "MEDIUM") if hasattr(alert, "severity") else alert.get("severity", "MEDIUM")
    source = getattr(alert, "source", "CCTV Feed") if hasattr(alert, "source") else alert.get("source", "CCTV Feed")
    snapshot_path = getattr(alert, "snapshot_path", None) if hasattr(alert, "snapshot_path") else alert.get("snapshot_path")

    ts = getattr(alert, "timestamp", None) if hasattr(alert, "timestamp") else alert.get("timestamp")
    time_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts or "Recently")

    conf_pct = int(round(confidence * 100))
    reason = build_plain_language_reason(label, confidence, time_str, source)

    # Compose Email
    msg = EmailMessage()
    msg["Subject"] = f"[CrimeWatch ALERT] {severity} Severity: {label} Detected ({conf_pct}%)"
    msg["From"] = alert_from
    msg["To"] = ", ".join(eligible_emails)
    msg["Date"] = formatdate(localtime=True)

    text_body = f"""CrimeWatch Security Alert
=========================
Status: Incident Detected
Severity: {severity}
Classification: {label}
Confidence: {conf_pct}%
Time: {time_str}
Source: {source}

Description:
{reason}

Please review the attached CCTV snapshot and take appropriate emergency or verification action.
Security Operations System - CrimeWatch
"""

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 20px; }}
    .card {{ max-width: 600px; margin: 0 auto; background-color: #1e293b; border-radius: 8px; border: 1px solid #334155; padding: 24px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
    .badge {{ display: inline-block; padding: 4px 12px; border-radius: 9999px; font-weight: bold; font-size: 13px; text-transform: uppercase; background-color: {'#ef4444' if severity == 'HIGH' else '#f59e0b'}; color: white; }}
    .title {{ font-size: 20px; font-weight: bold; color: #ffffff; margin: 16px 0 8px 0; }}
    .info-table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    .info-table td {{ padding: 8px 0; border-bottom: 1px solid #334155; }}
    .label {{ color: #94a3b8; width: 35%; }}
    .value {{ color: #ffffff; font-weight: 500; }}
    .footer {{ font-size: 12px; color: #64748b; margin-top: 24px; text-align: center; }}
  </style>
</head>
<body>
  <div class="card">
    <span class="badge">{severity} SEVERITY</span>
    <div class="title">{label} Incident Alert</div>
    <p style="color: #cbd5e1; font-size: 15px; margin-bottom: 16px;">{reason}</p>
    
    <table class="info-table">
      <tr><td class="label">Detection:</td><td class="value">{label} ({conf_pct}%)</td></tr>
      <tr><td class="label">Camera/Source:</td><td class="value">{source}</td></tr>
      <tr><td class="label">Timestamp:</td><td class="value">{time_str}</td></tr>
      <tr><td class="label">Incident Severity:</td><td class="value">{severity}</td></tr>
    </table>
    
    <p style="color: #94a3b8; font-size: 13px; margin-top: 20px;">
      A forensic video snapshot is attached to this transmission.
    </p>
    <div class="footer">Automated security dispatch delivered by CrimeWatch System</div>
  </div>
</body>
</html>
"""
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    # Attach snapshot if exists
    if snapshot_path:
        snap_file = Path(snapshot_path)
        if not snap_file.is_absolute():
            snap_file = PROJECT_ROOT / snap_file

        if snap_file.exists():
            try:
                ctype, _ = mimetypes.guess_type(str(snap_file))
                if ctype is None:
                    ctype = "image/jpeg"
                maintype, subtype = ctype.split("/", 1)

                with open(snap_file, "rb") as f:
                    msg.add_attachment(
                        f.read(),
                        maintype=maintype,
                        subtype=subtype,
                        filename=snap_file.name,
                    )
            except Exception as e:
                logger.error(f"Failed to attach snapshot {snap_file}: {e}")

    connected = False
    last_err = None

    # 1. Try TLS (port 587 default) with 5s timeout
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=5) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            connected = True
    except Exception as e:
        last_err = e

    # 2. If port 587 timed out or failed, try SSL (port 465) with 5s timeout
    if not connected:
        try:
            with smtplib.SMTP_SSL(smtp_host, 465, timeout=5) as server:
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
                connected = True
        except Exception as e:
            last_err = e

    if connected:
        logger.info(f"Successfully sent alert email to {len(eligible_emails)} recipients.")
        return True, eligible_emails, None
    else:
        err_str = str(last_err)
        if "timed out" in err_str.lower() or "10060" in err_str:
            error_msg = "SMTP Connection Timed Out. Your current Wi-Fi network (campus/hostel) is blocking outbound email ports (587/465). Switch to mobile hotspot to dispatch live emails."
        else:
            error_msg = f"SMTP error: {err_str}"
        logger.error(error_msg)
        return False, [], error_msg


# -------------------------------------------------------------------------------
# SMS Alerting (Twilio)
# -------------------------------------------------------------------------------

def send_sms(alert: Any, recipients: List[Any]) -> Tuple[bool, List[str], Optional[str]]:
    """
    Sends an urgent SMS via Twilio to eligible recipients.
    Rule: Only HIGH severity alerts are delivered via SMS.
    Returns: (success: bool, delivered_phones: list[str], error_message: str | None)
    """
    severity = getattr(alert, "severity", "none") if hasattr(alert, "severity") else alert.get("severity", "none")

    # Strict rule: Only HIGH severity alerts are sent via SMS
    if str(severity).upper() != "HIGH":
        logger.info(f"Skipping SMS: severity '{severity}' is not HIGH.")
        return False, [], "SMS suppressed: severity is not HIGH."

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_number = os.getenv("TWILIO_FROM_NUMBER", "")

    if not account_sid or not auth_token or not from_number:
        msg = "Twilio credentials (ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER) not configured."
        logger.warning(msg)
        return False, [], msg

    # Filter recipients with valid phone numbers and eligible min_severity
    eligible_phones = []
    for r in recipients:
        phone = getattr(r, "phone", None) or (r.get("phone") if isinstance(r, dict) else None)
        active = getattr(r, "active", True) if hasattr(r, "active") else r.get("active", True)
        min_sev = getattr(r, "min_severity", "MEDIUM") if hasattr(r, "min_severity") else r.get("min_severity", "MEDIUM")

        if phone and active and should_notify_recipient(severity, min_sev):
            if phone not in eligible_phones:
                eligible_phones.append(phone)

    if not eligible_phones:
        logger.info("No eligible phone recipients found for SMS.")
        return False, [], "No eligible phone recipients."

    # Extract alert info
    label = getattr(alert, "label", "Anomaly") if hasattr(alert, "label") else alert.get("label", "Anomaly")
    confidence = getattr(alert, "confidence", 0.0) if hasattr(alert, "confidence") else alert.get("confidence", 0.0)
    source = getattr(alert, "source", "CCTV") if hasattr(alert, "source") else alert.get("source", "CCTV")

    ts = getattr(alert, "timestamp", None) if hasattr(alert, "timestamp") else alert.get("timestamp")
    time_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts or "now")
    conf_pct = int(round(confidence * 100))

    sms_body = (
        f"[CRIMEWATCH ALERT - HIGH] {label} detected ({conf_pct}%) at {time_str} from {source}. "
        "Immediate response required."
    )

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)

        sent_phones = []
        for to_phone in eligible_phones:
            try:
                client.messages.create(
                    body=sms_body,
                    from_=from_number,
                    to=to_phone,
                )
                sent_phones.append(to_phone)
            except Exception as e:
                logger.error(f"Twilio failed sending to {to_phone}: {e}")

        if sent_phones:
            logger.info(f"Successfully sent Twilio SMS to {len(sent_phones)} numbers.")
            return True, sent_phones, None
        else:
            return False, [], "Failed to send SMS to any eligible recipient."
    except Exception as e:
        error_msg = f"Twilio client error: {str(e)}"
        logger.error(error_msg)
        return False, [], error_msg


# -------------------------------------------------------------------------------
# Dispatcher & Pipeline Router
# -------------------------------------------------------------------------------

def dispatch_alert(
    alert: Any,
    recipients: Optional[List[Any]] = None,
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Main alerting router. Dispatches email and SMS based on recipient configuration.
    Never raises an uncaught exception; records which channels succeeded.
    """
    if recipients is None:
        try:
            from backend.db import SessionLocal, Recipient
            db = db_session or SessionLocal()
            recipients = db.query(Recipient).filter(Recipient.active == True).all()
        except Exception as e:
            logger.error(f"Error fetching recipients from database: {e}")
            recipients = []

    succeeded_channels = []
    channel_details = {}

    # 1. Email channel
    try:
        email_ok, delivered_emails, email_err = send_email(alert, recipients)
        channel_details["email"] = {
            "success": email_ok,
            "delivered": delivered_emails,
            "error": email_err,
        }
        if email_ok:
            succeeded_channels.append("email")
    except Exception as e:
        logger.error(f"Unhandled exception in send_email: {e}")
        channel_details["email"] = {"success": False, "delivered": [], "error": str(e)}

    # 2. SMS channel (Only HIGH severity)
    try:
        sms_ok, delivered_phones, sms_err = send_sms(alert, recipients)
        channel_details["sms"] = {
            "success": sms_ok,
            "delivered": delivered_phones,
            "error": sms_err,
        }
        if sms_ok:
            succeeded_channels.append("sms")
    except Exception as e:
        logger.error(f"Unhandled exception in send_sms: {e}")
        channel_details["sms"] = {"success": False, "delivered": [], "error": str(e)}

    # Record notified channels on alert record if attached to db
    if hasattr(alert, "notified_channels"):
        alert.notified_channels = ",".join(succeeded_channels) if succeeded_channels else "none"
        if db_session:
            try:
                db_session.add(alert)
                db_session.commit()
            except Exception as e:
                logger.error(f"Error updating alert notified_channels in DB: {e}")

    return {
        "succeeded_channels": succeeded_channels,
        "details": channel_details,
    }


# -------------------------------------------------------------------------------
# Test Alert Verification Helper
# -------------------------------------------------------------------------------

def test_alert(
    channel: str = "all",
    test_email: Optional[str] = None,
    test_phone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verifies SMTP and Twilio credentials by sending a simulated test alert.
    Useful for UI credential validation checks.
    """
    target_email = test_email or os.getenv("ALERT_TO", "").split(",")[0].strip() or os.getenv("SMTP_USER", "")
    target_phone = test_phone or os.getenv("TWILIO_TO_NUMBERS", "").split(",")[0].strip()

    fake_alert = {
        "label": "Test Emergency",
        "confidence": 0.99,
        "severity": "HIGH",
        "source": "CrimeWatch UI Diagnostic System",
        "timestamp": None,
        "snapshot_path": None,
    }

    mock_recipients = [
        {
            "name": "Diagnostic Tester",
            "email": target_email,
            "phone": target_phone,
            "min_severity": "HIGH",
            "active": True,
        }
    ]

    results = {}

    if channel in ("all", "email"):
        email_ok, delivered, err = send_email(fake_alert, mock_recipients)
        results["email"] = {
            "success": email_ok,
            "target": target_email,
            "delivered": delivered,
            "error": err,
        }

    if channel in ("all", "sms"):
        sms_ok, delivered, err = send_sms(fake_alert, mock_recipients)
        results["sms"] = {
            "success": sms_ok,
            "target": target_phone,
            "delivered": delivered,
            "error": err,
        }

    return results

