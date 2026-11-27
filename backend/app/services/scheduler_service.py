"""
APScheduler-based medication scheduler.
  - Checks every minute for due medications across all elders.
  - Schedules 15-minute snooze callbacks.
  - Handles 10-minute missed-call retries (max 2 attempts).
  - Sends caregiver alerts for missed doses.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models.schemas import (
    AlertPriority,
    AlertType,
    CallDirection,
    DoseLog,
    DoseStatus,
)

logger = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None
IST = ZoneInfo("Asia/Kolkata")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=IST)
    return _scheduler


def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        # Check due medications every 60 seconds
        sched.add_job(
            check_due_medications,
            "interval",
            seconds=60,
            id="check_due_meds",
            replace_existing=True,
        )
        sched.start()
        logger.info("Scheduler started — checking due medications every 60 seconds")


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


# ── Core: Check Due Medications ────────────────────────────────────────

async def check_due_medications() -> None:
    """
    Runs every minute. For each elder, checks if any medication is due NOW
    and hasn't been logged yet. If due, creates a dose log and triggers a call.
    """
    from app.services import firebase_service as fb
    from app.services.telephony_service import make_call

    now = datetime.now(IST)
    current_time = now.strftime("%H:%M")
    today_str = now.strftime("%Y-%m-%d")
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    db = fb.get_db()
    elders_docs = db.collection("elders").get()

    for elder_doc in elders_docs:
        elder_data = elder_doc.to_dict()
        elder_id = elder_doc.id
        elder_phone = elder_data.get("phone")
        if not elder_phone:
            continue

        meds = fb.get_medications(elder_id, active_only=True)
        for med in meds:
            if med.time != current_time:
                continue

            # Check if a dose log already exists for this med + today
            existing = fb.find_dose_log(elder_id, med.id, today_str)
            if existing:
                continue  # Already handled

            # Create a new SCHEDULED dose log
            log = DoseLog(
                elder_id=elder_id,
                medication_id=med.id,
                medication_name=med.medicine_name,
                scheduled_time=med.time,
                date=today_str,
                status=DoseStatus.SCHEDULED,
                call_attempts=1,
                call_direction=CallDirection.OUTBOUND,
            )
            log_id = fb.create_dose_log(log)

            # Trigger outbound reminder call
            webhook_url = f"{base_url}/voice/outbound-greeting?elder_id={elder_id}&med_id={med.id}&log_id={log_id}"
            status_url = f"{base_url}/voice/call-status?elder_id={elder_id}&log_id={log_id}"

            try:
                call_sid = make_call(elder_phone, webhook_url, status_url)
                fb.update_dose_log(elder_id, log_id, {"call_sid": call_sid})
                logger.info(f"Call placed: elder={elder_id} med={med.medicine_name} call={call_sid}")
            except Exception as exc:
                logger.error(f"Failed to call elder {elder_id}: {exc}")


# ── Snooze Callback ────────────────────────────────────────────────────

def schedule_snooze_callback(
    elder_id: str,
    med_id: str,
    log_id: str,
    elder_phone: str,
    minutes: int = 15,
) -> None:
    """Schedule a callback after the elder requests a snooze."""
    sched = get_scheduler()
    run_at = datetime.now(IST) + timedelta(minutes=minutes)
    job_id = f"snooze_{elder_id}_{log_id}"

    sched.add_job(
        _snooze_callback,
        "date",
        run_date=run_at,
        id=job_id,
        replace_existing=True,
        args=[elder_id, med_id, log_id, elder_phone],
    )
    logger.info(f"Snooze callback scheduled in {minutes}min: elder={elder_id} log={log_id}")


async def _snooze_callback(
    elder_id: str, med_id: str, log_id: str, elder_phone: str
) -> None:
    """Execute the snooze callback — place a new call."""
    from app.services import firebase_service as fb
    from app.services.telephony_service import make_call

    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    # Update attempt count
    log = fb.find_dose_log(elder_id, med_id, datetime.now(IST).strftime("%Y-%m-%d"))
    if log and log.status == DoseStatus.TAKEN:
        return  # Already taken, no need to call

    fb.update_dose_log(elder_id, log_id, {
        "status": DoseStatus.SCHEDULED.value,
        "call_attempts": (log.call_attempts + 1) if log else 2,
    })

    webhook_url = f"{base_url}/voice/outbound-greeting?elder_id={elder_id}&med_id={med_id}&log_id={log_id}"
    status_url = f"{base_url}/voice/call-status?elder_id={elder_id}&log_id={log_id}"

    try:
        call_sid = make_call(elder_phone, webhook_url, status_url)
        fb.update_dose_log(elder_id, log_id, {"call_sid": call_sid})
        logger.info(f"Snooze callback call placed: elder={elder_id} call={call_sid}")
    except Exception as exc:
        logger.error(f"Snooze callback failed for {elder_id}: {exc}")


# ── Missed-Call Retry ──────────────────────────────────────────────────

def schedule_missed_retry(
    elder_id: str,
    med_id: str,
    log_id: str,
    elder_phone: str,
    attempt: int = 1,
) -> None:
    """
    Schedule a retry call after 10 minutes if the elder didn't answer.
    Max 2 consecutive unanswered calls → mark as MISSED + alert caregiver.
    """
    if attempt >= 2:
        # Already retried once — mark as missed and alert caregiver
        _mark_missed_and_alert(elder_id, med_id, log_id)
        return

    sched = get_scheduler()
    run_at = datetime.now(IST) + timedelta(minutes=10)
    job_id = f"retry_{elder_id}_{log_id}_{attempt}"

    sched.add_job(
        _retry_call,
        "date",
        run_date=run_at,
        id=job_id,
        replace_existing=True,
        args=[elder_id, med_id, log_id, elder_phone, attempt + 1],
    )
    logger.info(f"Missed-call retry #{attempt+1} scheduled in 10min: elder={elder_id}")


async def _retry_call(
    elder_id: str, med_id: str, log_id: str, elder_phone: str, attempt: int
) -> None:
    """Execute a retry call after the elder didn't answer."""
    from app.services import firebase_service as fb
    from app.services.telephony_service import make_call

    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    log = fb.find_dose_log(elder_id, med_id, datetime.now(IST).strftime("%Y-%m-%d"))
    if log and log.status == DoseStatus.TAKEN:
        return  # Already taken

    fb.update_dose_log(elder_id, log_id, {"call_attempts": attempt})

    webhook_url = f"{base_url}/voice/outbound-greeting?elder_id={elder_id}&med_id={med_id}&log_id={log_id}"
    status_url = f"{base_url}/voice/call-status?elder_id={elder_id}&log_id={log_id}"

    try:
        call_sid = make_call(elder_phone, webhook_url, status_url)
        fb.update_dose_log(elder_id, log_id, {"call_sid": call_sid})
        logger.info(f"Retry call #{attempt} placed: elder={elder_id} call={call_sid}")
    except Exception as exc:
        logger.error(f"Retry call failed for {elder_id}: {exc}")
        if attempt >= 2:
            _mark_missed_and_alert(elder_id, med_id, log_id)


def _mark_missed_and_alert(elder_id: str, med_id: str, log_id: str) -> None:
    """Mark dose as MISSED and send urgent alert to caregiver."""
    from app.services import firebase_service as fb

    log = None
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    log = fb.find_dose_log(elder_id, med_id, today_str)
    med_name = log.medication_name if log else "Unknown medicine"

    fb.update_dose_log(elder_id, log_id, {"status": DoseStatus.MISSED.value})

    elder = fb.get_elder(elder_id)
    elder_name = elder.name if elder else "Elder"

    fb.send_caregiver_alert(
        elder_id=elder_id,
        alert_type=AlertType.MISSED_DOSE,
        title=f"⚠️ Missed Dose: {elder_name}",
        message=f"{elder_name} did not answer 2 consecutive calls for {med_name}. Please check on them.",
        priority=AlertPriority.URGENT,
        medication_name=med_name,
    )
    logger.warning(f"MISSED DOSE: elder={elder_id} med={med_name} — caregiver alerted")


# ── Missed-Call Callback (₹0 SIM balance) ─────────────────────────────

def schedule_missed_call_callback(
    elder_id: str,
    elder_phone: str,
) -> None:
    """
    When an elder with ₹0 SIM balance calls us, we reject and callback.
    Schedule a callback 2 seconds later.
    """
    sched = get_scheduler()
    run_at = datetime.now(IST) + timedelta(seconds=2)
    job_id = f"missed_call_cb_{elder_id}"

    sched.add_job(
        _missed_call_callback,
        "date",
        run_date=run_at,
        id=job_id,
        replace_existing=True,
        args=[elder_id, elder_phone],
    )


async def _missed_call_callback(elder_id: str, elder_phone: str) -> None:
    """Place an outbound call back to the elder after they gave us a missed call."""
    from app.services.telephony_service import make_call

    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    webhook_url = f"{base_url}/voice/inbound-menu?elder_id={elder_id}"

    try:
        call_sid = make_call(elder_phone, webhook_url)
        logger.info(f"Missed-call callback placed: elder={elder_id} call={call_sid}")
    except Exception as exc:
        logger.error(f"Missed-call callback failed for {elder_id}: {exc}")
