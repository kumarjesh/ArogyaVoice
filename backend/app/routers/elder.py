"""
Elder API — powers the Elder View PWA (View 2).
Provides Browser Voice Simulator and double-dose check endpoints.
All endpoints are under /elder prefix.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.models.schemas import (
    AlertPriority,
    AlertType,
    CallDirection,
    DoseLog,
    DoseStatus,
)
from app.services import firebase_service as fb
from app.services.gemini_service import extract_intent
from app.services.sarvam_service import speech_to_text, text_to_speech

router = APIRouter()
logger = logging.getLogger("elder")
IST = ZoneInfo("Asia/Kolkata")


# ── Browser Voice Simulator ───────────────────────────────────────────

@router.post("/{elder_id}/voice-chat")
async def voice_chat(
    elder_id: str,
    audio: UploadFile = File(...),
):
    """
    Browser Voice Simulator: runs the exact same pipeline as a Twilio call.
    1. Receive mic audio from browser (WebM/WAV)
    2. Sarvam STT → transcript
    3. Gemini intent extraction
    4. Sarvam TTS → reply audio
    5. Update Firestore
    Returns JSON with transcript, intent, reply_text, and base64 audio.
    """
    import base64

    elder = fb.get_elder(elder_id)
    if not elder:
        raise HTTPException(404, "Elder not found")

    lang = elder.language.value
    audio_bytes = await audio.read()
    mime = audio.content_type or "audio/wav"

    # 1. Sarvam STT
    transcript = await speech_to_text(audio_bytes, lang, mime)
    if not transcript:
        from app.services.gemini_service import FALLBACK_UNCLEAR_TEXT
        reply_text = FALLBACK_UNCLEAR_TEXT.get(lang, FALLBACK_UNCLEAR_TEXT["en-IN"])
        reply_audio = await text_to_speech(reply_text, lang)
        return {
            "transcript": "",
            "intent": "UNCLEAR",
            "reply_text": reply_text,
            "reply_audio_base64": base64.b64encode(reply_audio).decode(),
        }

    # 2. Build medication context from today's schedule
    today = datetime.now(IST).strftime("%Y-%m-%d")
    meds = fb.get_medications(elder_id, active_only=True)
    med_context = ", ".join(m.medicine_name for m in meds) if meds else "general"

    # 3. Gemini intent
    intent_result = await extract_intent(transcript, lang, med_context)

    # 4. Handle specific intents
    reply_text = intent_result.reply_text

    if intent_result.intent == "TAKEN" and intent_result.confidence >= 0.7 and meds:
        # Find the next scheduled (not yet taken) medication
        logs = fb.get_dose_logs_for_date(elder_id, today)
        taken_med_ids = {l.medication_id for l in logs if l.status == DoseStatus.TAKEN}
        untaken = [m for m in meds if m.id not in taken_med_ids]

        if untaken:
            med = untaken[0]
            log = DoseLog(
                elder_id=elder_id,
                medication_id=med.id,
                medication_name=med.medicine_name,
                scheduled_time=med.time,
                date=today,
                status=DoseStatus.TAKEN,
                taken_at=datetime.now(IST),
                call_direction=CallDirection.INBOUND,
                elder_response_text=transcript,
            )
            fb.create_dose_log(log)
            fb.send_caregiver_alert(
                elder_id=elder_id,
                alert_type=AlertType.SYSTEM,
                title=f"✅ Dose Taken (App): {elder.name}",
                message=f"{elder.name} confirmed taking {med.medicine_name} via the app.",
                priority=AlertPriority.LOW,
                medication_name=med.medicine_name,
            )

    elif intent_result.intent == "DOUBLE_DOSE_CHECK":
        logs = fb.get_dose_logs_for_date(elder_id, today)
        taken = [l for l in logs if l.status == DoseStatus.TAKEN]
        from app.routers.voice import _get_text
        if taken:
            names = ", ".join(l.medication_name for l in taken)
            times = ", ".join(
                l.taken_at.strftime("%I:%M %p") if l.taken_at else l.scheduled_time
                for l in taken
            )
            reply_text = _get_text("double_dose_taken", lang, elder.honorific, names, times)
        else:
            reply_text = _get_text("double_dose_none", lang, elder.honorific)

    elif intent_result.intent == "CALL_PERSON" and intent_result.target_person:
        contact = fb.find_speed_dial_by_alias(elder_id, intent_result.target_person)
        if contact:
            reply_text = f"To call {contact.name}, please use the call button on screen or dial from your phone."
        else:
            reply_text = f"Sorry {elder.honorific}, I could not find that contact."

    elif intent_result.intent == "WELLNESS_REPORT" and intent_result.wellness_summary:
        fb.send_caregiver_alert(
            elder_id=elder_id,
            alert_type=AlertType.WELLNESS_SYMPTOM,
            title=f"🩺 Wellness Report (App): {elder.name}",
            message=f"{elder.name} reported: {intent_result.wellness_summary}",
            priority=AlertPriority.MEDIUM,
        )

    # 5. Sarvam TTS for the reply
    reply_audio = await text_to_speech(reply_text, lang)

    return {
        "transcript": transcript,
        "intent": intent_result.intent,
        "confidence": intent_result.confidence,
        "reply_text": reply_text,
        "reply_audio_base64": base64.b64encode(reply_audio).decode(),
    }


# ── Direct Touch Action (1-Tap "I Took It" or "Snooze") ───────────────

@router.post("/{elder_id}/confirm-dose")
async def confirm_dose_touch(
    elder_id: str,
    med_id: Optional[str] = Query(None),
):
    """
    1-Tap Touch confirmation from Elder View screen (equivalent to pressing 1 on dialpad).
    """
    import base64
    elder = fb.get_elder(elder_id)
    if not elder:
        raise HTTPException(404, "Elder not found")

    lang = elder.language.value
    today = datetime.now(IST).strftime("%Y-%m-%d")
    meds = fb.get_medications(elder_id, active_only=True)
    if not meds:
        raise HTTPException(404, "No active medications found")

    target_med = next((m for m in meds if m.id == med_id), None) if med_id else None
    if not target_med:
        logs = fb.get_dose_logs_for_date(elder_id, today)
        taken_ids = {l.medication_id for l in logs if l.status == DoseStatus.TAKEN}
        untaken = [m for m in meds if m.id not in taken_ids]
        target_med = untaken[0] if untaken else meds[0]

    now = datetime.now(IST)
    log = DoseLog(
        elder_id=elder_id,
        medication_id=target_med.id,
        medication_name=target_med.medicine_name,
        scheduled_time=target_med.time,
        date=today,
        status=DoseStatus.TAKEN,
        taken_at=now,
        call_direction=CallDirection.INBOUND,
        elder_response_text="1 (Touch Button)",
    )
    fb.create_dose_log(log)
    fb.send_caregiver_alert(
        elder_id=elder_id,
        alert_type=AlertType.SYSTEM,
        title=f"✅ Dose Taken (1-Tap): {elder.name}",
        message=f"{elder.name} confirmed taking {target_med.medicine_name} at {now.strftime('%I:%M %p')}.",
        priority=AlertPriority.LOW,
        medication_name=target_med.medicine_name,
    )

    from app.routers.voice import _get_text
    reply_text = _get_text("thank_taken", lang, elder.honorific)
    reply_audio = await text_to_speech(reply_text, lang)

    return {
        "success": True,
        "medication_name": target_med.medicine_name,
        "reply_text": reply_text,
        "reply_audio_base64": base64.b64encode(reply_audio).decode(),
    }


@router.post("/{elder_id}/snooze-dose")
async def snooze_dose_touch(
    elder_id: str,
    minutes: int = Query(15),
):
    """
    1-Tap Touch snooze from Elder View screen (equivalent to pressing 2 on dialpad).
    """
    import base64
    elder = fb.get_elder(elder_id)
    if not elder:
        raise HTTPException(404, "Elder not found")

    lang = elder.language.value
    from app.routers.voice import _get_text
    reply_text = _get_text("snooze_confirm", lang, elder.honorific, minutes)
    reply_audio = await text_to_speech(reply_text, lang)

    return {
        "success": True,
        "minutes": minutes,
        "reply_text": reply_text,
        "reply_audio_base64": base64.b64encode(reply_audio).decode(),
    }


# ── Double-Dose Check (REST) ──────────────────────────────────────────

@router.get("/{elder_id}/dose-check")
async def dose_check(elder_id: str):
    """Check today's dose status for the Elder View screen."""
    elder = fb.get_elder(elder_id)
    if not elder:
        raise HTTPException(404, "Elder not found")

    today = datetime.now(IST).strftime("%Y-%m-%d")
    logs = fb.get_dose_logs_for_date(elder_id, today)
    meds = fb.get_medications(elder_id, active_only=True)

    return {
        "elder_name": elder.name,
        "today": today,
        "medications": [
            {
                "id": m.id,
                "name": m.medicine_name,
                "time": m.time,
                "status": next(
                    (l.status.value for l in logs if l.medication_id == m.id),
                    "scheduled",
                ),
                "taken_at": next(
                    (l.taken_at.isoformat() if l.taken_at else None for l in logs if l.medication_id == m.id),
                    None,
                ),
            }
            for m in meds
        ],
    }


# ── Speed Dial for Elder View ─────────────────────────────────────────

@router.get("/{elder_id}/speed-dial")
async def elder_speed_dials(elder_id: str):
    contacts = fb.get_speed_dials(elder_id)
    return [
        {
            "slot": c.slot,
            "name": c.name,
            "relationship": c.relationship,
            "phone": c.phone,
        }
        for c in contacts
    ]
