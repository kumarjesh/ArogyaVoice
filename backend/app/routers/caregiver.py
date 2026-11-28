"""
Caregiver REST API — powers the React dashboard (View 1).
All endpoints are under /api prefix.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.models.schemas import (
    ElderProfile,
    Medication,
    SpeedDialContact,
    TriggerCallRequest,
)
from app.services import firebase_service as fb
from app.services.gemini_service import scan_pill_strip

router = APIRouter()
logger = logging.getLogger("caregiver")
IST = ZoneInfo("Asia/Kolkata")


# ── Elder Profile ──────────────────────────────────────────────────────

@router.post("/elders", response_model=dict)
async def create_elder(profile: ElderProfile):
    elder_id = fb.create_elder(profile)
    return {"id": elder_id, "status": "created"}


@router.get("/elders")
async def list_elders(caregiver_uid: str = Query("caregiver_demo_uid")):
    elders = fb.list_elders_for_caregiver(caregiver_uid)
    return [e.model_dump() for e in elders]


@router.get("/elders/{elder_id}")
async def get_elder(elder_id: str):
    elder = fb.get_elder(elder_id)
    if not elder:
        raise HTTPException(404, "Elder not found")
    return elder.model_dump()


@router.patch("/elders/{elder_id}")
async def update_elder(elder_id: str, updates: dict):
    fb.update_elder(elder_id, updates)
    return {"status": "updated"}


# ── Medications ────────────────────────────────────────────────────────

@router.post("/elders/{elder_id}/medications", response_model=dict)
async def add_medication(elder_id: str, med: Medication):
    med_id = fb.add_medication(elder_id, med)
    return {"id": med_id, "status": "created"}


@router.get("/elders/{elder_id}/medications")
async def list_medications(elder_id: str):
    meds = fb.get_medications(elder_id)
    return [m.model_dump() for m in meds]


@router.patch("/elders/{elder_id}/medications/{med_id}")
async def update_medication(elder_id: str, med_id: str, updates: dict):
    fb.update_medication(elder_id, med_id, updates)
    return {"status": "updated"}


@router.delete("/elders/{elder_id}/medications/{med_id}")
async def delete_medication(elder_id: str, med_id: str):
    fb.delete_medication(elder_id, med_id)
    return {"status": "deactivated"}


# ── Dose Logs (Timeline) ──────────────────────────────────────────────

@router.get("/elders/{elder_id}/dose-logs")
async def get_dose_logs(
    elder_id: str,
    date: str = Query(None, description="YYYY-MM-DD, defaults to today IST"),
):
    if not date:
        date = datetime.now(IST).strftime("%Y-%m-%d")
    logs = fb.get_dose_logs_for_date(elder_id, date)
    return [l.model_dump() for l in logs]


# ── Speed Dial ─────────────────────────────────────────────────────────

@router.post("/elders/{elder_id}/speed-dial")
async def set_speed_dial(elder_id: str, contact: SpeedDialContact):
    fb.set_speed_dial(elder_id, contact)
    return {"status": "saved", "slot": contact.slot}


@router.get("/elders/{elder_id}/speed-dial")
async def list_speed_dials(elder_id: str):
    contacts = fb.get_speed_dials(elder_id)
    return [c.model_dump() for c in contacts]


# ── Alerts ─────────────────────────────────────────────────────────────

@router.get("/elders/{elder_id}/alerts")
async def list_alerts(
    elder_id: str,
    unread_only: bool = Query(False),
    limit: int = Query(50),
):
    alerts = fb.get_alerts(elder_id, unread_only=unread_only, limit=limit)
    return [a.model_dump() for a in alerts]


@router.patch("/elders/{elder_id}/alerts/{alert_id}/read")
async def mark_alert_read(elder_id: str, alert_id: str):
    fb.mark_alert_read(elder_id, alert_id)
    return {"status": "read"}


# ── Pill-Strip Photo Scanner (Gemini Vision) ──────────────────────────

@router.post("/elders/{elder_id}/pill-scan")
async def pill_strip_scan(
    elder_id: str,
    image: UploadFile = File(...),
):
    """
    Upload a photo of a medicine strip/prescription.
    Gemini Vision extracts medicine names, colors, shapes, dosages.
    Flags potential duplicate generics already in the elder's schedule.
    """
    contents = await image.read()
    mime = image.content_type or "image/jpeg"

    # Get existing medications to check for duplicates
    existing_meds = fb.get_medications(elder_id)
    existing_names = [m.medicine_name for m in existing_meds]

    results = await scan_pill_strip(contents, mime, existing_names)
    return [r.model_dump() for r in results]
