"""
Twilio Voice Webhooks — the complete call flow for outbound medication
reminders and inbound elder calls (double-dose check + speed-dial bridging).

Outbound flow:
  1. /outbound-greeting   → Scam-safe trust greeting + Play audio + Gather
  2. /gather-response     → DTMF or Recording URL → STT → Gemini intent
  3. /wellness-check      → Optional 10-sec evening wellness question
  4. /wellness-response   → Capture wellness report → alert caregiver
  5. /call-status         → No-answer / busy / failed → retry or mark missed

Inbound flow:
  1. /inbound             → Caller ID lookup → reject+callback or menu
  2. /inbound-menu        → Greeting + Gather voice/DTMF
  3. /inbound-response    → Double-dose check or speed-dial bridge
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.models.schemas import (
    AlertPriority,
    AlertType,
    CallDirection,
    DoseLog,
    DoseStatus,
)
from app.services import firebase_service as fb
from app.services.audio_cache import ensure_audio
from app.services.gemini_service import extract_intent
from app.services.sarvam_service import speech_to_text, text_to_speech
from app.services.scheduler_service import (
    schedule_missed_call_callback,
    schedule_missed_retry,
    schedule_snooze_callback,
)
from app.services.telephony_service import (
    build_dial_bridge,
    build_play_and_gather,
    build_play_only,
    build_gather_after_play,
    build_reject,
    build_record,
)

router = APIRouter()
logger = logging.getLogger("voice")
IST = ZoneInfo("Asia/Kolkata")


def _twiml(content: str) -> Response:
    """Return a TwiML XML response."""
    return Response(content=content, media_type="application/xml")


def _base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")


# ══════════════════════════════════════════════════════════════════════
#  OUTBOUND MEDICATION REMINDER CALL FLOW
# ══════════════════════════════════════════════════════════════════════


@router.post("/outbound-greeting")
async def outbound_greeting(
    elder_id: str = Query(...),
    med_id: str = Query(...),
    log_id: str = Query(...),
):
    """
    Step 1: When the elder picks up, play a warm trust greeting in their
    mother tongue mentioning the caregiver's name, then gather voice/DTMF.
    """
    base = _base_url()
    elder = fb.get_elder(elder_id)
    if not elder:
        return _twiml(build_play_only(f"{base}/audio/error.wav"))

    meds = fb.get_medications(elder_id, active_only=True)
    med = next((m for m in meds if m.id == med_id), None)
    if not med:
        return _twiml(build_play_only(f"{base}/audio/error.wav"))

    # Build the medication description based on accessibility mode
    med_desc = _build_med_description(med, elder.accessibility_mode.value)
    meal_text = _meal_timing_text(med.meal_timing.value, elder.language.value)

    # Build greeting text
    now = datetime.now(IST)
    hour = now.hour
    if hour < 12:
        time_greeting = "morning"
    elif hour < 17:
        time_greeting = "afternoon"
    else:
        time_greeting = "evening"

    greeting = _build_greeting(
        honorific=elder.honorific,
        caregiver_name=elder.caregiver_name,
        time_greeting=time_greeting,
        med_desc=med_desc,
        meal_text=meal_text,
        language=elder.language.value,
    )

    # Generate TTS audio
    audio_url = await ensure_audio(greeting, elder.language.value, base)

    # Build TwiML: Play greeting, then record elder's response
    action_url = f"{base}/voice/gather-response?elder_id={elder_id}&med_id={med_id}&log_id={log_id}&attempt=1"

    twiml = build_play_and_gather(
        audio_url=audio_url,
        action_url=action_url,
        dtmf_timeout=5,
        language=elder.language.value,
    )
    return _twiml(twiml)


@router.post("/gather-response")
async def gather_response(
    request: Request,
    elder_id: str = Query(...),
    med_id: str = Query(...),
    log_id: str = Query(...),
    attempt: int = Query(1),
):
    """
    Step 2: Process the elder's response — either DTMF digits or speech.
    DTMF: 1 = Taken, 2 = Snooze 15 min.
    Speech: Transcribe with Sarvam STT → extract intent with Gemini.
    """
    form = await request.form()
    digits = form.get("Digits", "")
    speech_result = form.get("SpeechResult", "")
    recording_url = form.get("RecordingUrl", "")
    base = _base_url()

    elder = fb.get_elder(elder_id)
    if not elder:
        return _twiml(build_play_only(f"{base}/audio/error.wav"))

    med = None
    meds = fb.get_medications(elder_id, active_only=True)
    med = next((m for m in meds if m.id == med_id), None)
    med_name = med.medicine_name if med else "medicine"
    med_desc = _build_med_description(med, elder.accessibility_mode.value) if med else "your medicine"
    lang = elder.language.value

    # ── Handle DTMF keypad input ──
    if digits:
        if digits == "1":
            return await _handle_taken(elder, med, med_desc, log_id, base, lang)
        elif digits == "2":
            return await _handle_snooze(elder, med_id, log_id, base, lang)
        else:
            # Unknown digit — treat as unclear
            return await _handle_unclear(elder, med_desc, log_id, base, lang, attempt)

    # ── Handle speech (via Twilio's built-in or recording) ──
    transcript = ""
    if speech_result:
        transcript = speech_result
    elif recording_url:
        # Download recording and run through Sarvam STT
        try:
            sid = os.getenv("TWILIO_ACCOUNT_SID")
            token = os.getenv("TWILIO_AUTH_TOKEN")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{recording_url}.wav",
                    auth=(sid, token),
                )
                if resp.status_code == 200:
                    transcript = await speech_to_text(resp.content, lang)
        except Exception as exc:
            logger.error(f"Recording download/STT failed: {exc}")

    if not transcript:
        # No input at all — handle as unclear / no response
        return await _handle_unclear(elder, med_desc, log_id, base, lang, attempt)

    # Update dose log with what the elder said
    fb.update_dose_log(elder_id, log_id, {"elder_response_text": transcript})

    # Extract intent with Gemini
    intent_result = await extract_intent(transcript, lang, med_desc)
    logger.info(f"Intent: {intent_result.intent} ({intent_result.confidence}) for elder={elder_id}")

    if intent_result.intent == "TAKEN" and intent_result.confidence >= 0.7:
        return await _handle_taken(elder, med, med_desc, log_id, base, lang)
    elif intent_result.intent == "SNOOZE":
        minutes = intent_result.snooze_minutes or 15
        return await _handle_snooze(elder, med_id, log_id, base, lang, minutes)
    else:
        # UNCLEAR, OTHER, or low-confidence TAKEN → never guess on medical confirmation
        return await _handle_unclear(elder, med_desc, log_id, base, lang, attempt)


# ── Intent Handlers ────────────────────────────────────────────────────

async def _handle_taken(elder, med, med_desc, log_id, base, lang):
    """Mark dose as TAKEN, thank the elder, optionally ask wellness check."""
    now = datetime.now(IST)
    fb.update_dose_log(elder.id, log_id, {
        "status": DoseStatus.TAKEN.value,
        "taken_at": now,
    })

    # Send confirmation alert to caregiver dashboard
    fb.send_caregiver_alert(
        elder_id=elder.id,
        alert_type=AlertType.SYSTEM,
        title=f"✅ Dose Taken: {elder.name}",
        message=f"{elder.name} confirmed taking {med_desc} at {now.strftime('%I:%M %p')}.",
        priority=AlertPriority.LOW,
        medication_name=med.medicine_name if med else None,
    )

    # Check if evening wellness check is enabled
    if elder.evening_wellness_check and now.hour >= 17:
        thank_text = _get_text("thank_wellness_intro", lang, elder.honorific)
        audio_url = await ensure_audio(thank_text, lang, base)
        action_url = f"{base}/voice/wellness-response?elder_id={elder.id}&log_id={log_id}"
        return _twiml(build_play_and_gather(audio_url, action_url, dtmf_timeout=8, language=lang))

    # Simple thank you and hang up
    thank_text = _get_text("thank_taken", lang, elder.honorific)
    audio_url = await ensure_audio(thank_text, lang, base)
    return _twiml(build_play_only(audio_url))


async def _handle_snooze(elder, med_id, log_id, base, lang, minutes=15):
    """Mark dose as SNOOZED and schedule a callback."""
    fb.update_dose_log(elder.id, log_id, {
        "status": DoseStatus.SNOOZED.value,
        "snooze_count": 1,  # Will be incremented on subsequent snoozes
    })

    schedule_snooze_callback(elder.id, med_id, log_id, elder.phone, minutes)

    snooze_text = _get_text("snooze_confirm", lang, elder.honorific, minutes)
    audio_url = await ensure_audio(snooze_text, lang, base)
    return _twiml(build_play_only(audio_url))


async def _handle_unclear(elder, med_desc, log_id, base, lang, attempt):
    """
    Handle unclear/noisy/ambiguous response.
    Attempt 1: Repeat in simpler words + offer keypad fallback.
    Attempt 2: End call gently + alert caregiver.
    """
    if attempt >= 2:
        # Too many unclear attempts — end call, alert caregiver
        fb.send_caregiver_alert(
            elder_id=elder.id,
            alert_type=AlertType.UNCLEAR_RESPONSE,
            title=f"❓ Unclear Response: {elder.name}",
            message=f"Could not understand {elder.name}'s response after 2 attempts for {med_desc}. Please check on them.",
            priority=AlertPriority.HIGH,
            medication_name=med_desc,
        )
        goodbye_text = _get_text("unclear_goodbye", lang, elder.honorific)
        audio_url = await ensure_audio(goodbye_text, lang, base)
        return _twiml(build_play_only(audio_url))

    # First unclear attempt — repeat with keypad fallback
    retry_text = _get_text("unclear_retry", lang, elder.honorific, med_desc)
    audio_url = await ensure_audio(retry_text, lang, base)
    action_url = f"{base}/voice/gather-response?elder_id={elder.id}&med_id={log_id}&log_id={log_id}&attempt=2"
    return _twiml(build_play_and_gather(audio_url, action_url, dtmf_timeout=6, language=lang))


# ── Wellness Check ─────────────────────────────────────────────────────

@router.post("/wellness-response")
async def wellness_response(
    request: Request,
    elder_id: str = Query(...),
    log_id: str = Query(...),
):
    """Capture the elder's 10-second wellness report after dose confirmation."""
    form = await request.form()
    speech_result = form.get("SpeechResult", "")
    recording_url = form.get("RecordingUrl", "")
    base = _base_url()

    elder = fb.get_elder(elder_id)
    if not elder:
        return _twiml(build_play_only(f"{base}/audio/error.wav"))

    lang = elder.language.value
    transcript = speech_result

    if not transcript and recording_url:
        try:
            sid = os.getenv("TWILIO_ACCOUNT_SID")
            token = os.getenv("TWILIO_AUTH_TOKEN")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{recording_url}.wav", auth=(sid, token))
                if resp.status_code == 200:
                    transcript = await speech_to_text(resp.content, lang)
        except Exception as exc:
            logger.error(f"Wellness recording STT failed: {exc}")

    if transcript:
        # Use Gemini to summarise the wellness report
        intent_result = await extract_intent(transcript, lang, "wellness check")

        wellness_summary = intent_result.wellness_summary or transcript
        fb.update_dose_log(elder_id, log_id, {"wellness_summary": wellness_summary})

        if intent_result.intent == "WELLNESS_REPORT" and intent_result.wellness_summary:
            fb.send_caregiver_alert(
                elder_id=elder_id,
                alert_type=AlertType.WELLNESS_SYMPTOM,
                title=f"🩺 Wellness Report: {elder.name}",
                message=f"{elder.name} reported: {wellness_summary}",
                priority=AlertPriority.MEDIUM,
            )

    # Thank them and hang up
    thank_text = _get_text("wellness_thanks", lang, elder.honorific)
    audio_url = await ensure_audio(thank_text, lang, base)
    return _twiml(build_play_only(audio_url))


# ══════════════════════════════════════════════════════════════════════
#  CALL STATUS CALLBACK (no-answer, busy, failed)
# ══════════════════════════════════════════════════════════════════════

@router.post("/call-status")
async def call_status(
    request: Request,
    elder_id: str = Query(...),
    log_id: str = Query(...),
):
    """
    Twilio calls this when the call ends.
    If no-answer / busy / failed → schedule a retry (max 2 attempts).
    """
    form = await request.form()
    status = form.get("CallStatus", "")
    logger.info(f"Call status for elder={elder_id} log={log_id}: {status}")

    if status in ("no-answer", "busy", "failed"):
        elder = fb.get_elder(elder_id)
        if not elder:
            return PlainTextResponse("OK")

        # Find the dose log to check attempt count
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        logs = fb.get_dose_logs_for_date(elder_id, today_str)
        log = next((l for l in logs if l.id == log_id), None)
        attempt = log.call_attempts if log else 1

        # Get medication ID from dose log
        med_id = log.medication_id if log else ""

        schedule_missed_retry(elder_id, med_id, log_id, elder.phone, attempt)

    return PlainTextResponse("OK")


# ══════════════════════════════════════════════════════════════════════
#  INBOUND CALL FLOW (Elder calls us)
# ══════════════════════════════════════════════════════════════════════

@router.post("/inbound")
async def inbound_call(request: Request):
    """
    Entry point when an elder calls our Twilio number.
    1. Look up elder by Caller ID.
    2. If missed-call callback mode → reject + schedule callback (₹0 cost).
    3. Otherwise → play voice menu.
    """
    form = await request.form()
    caller = form.get("From", "")
    base = _base_url()

    elder = fb.get_elder_by_phone(caller)
    if not elder:
        # Unknown caller — play a generic message
        msg = "Sorry, your number is not registered. Please ask your caregiver to add you."
        audio_url = await ensure_audio(msg, "en-IN", base)
        return _twiml(build_play_only(audio_url))

    # Log that the elder called
    fb.send_caregiver_alert(
        elder_id=elder.id,
        alert_type=AlertType.ELDER_CALLED,
        title=f"📞 {elder.name} Called",
        message=f"{elder.name} called the ArogyaVoice number.",
        priority=AlertPriority.LOW,
    )

    if elder.missed_call_callback:
        # Reject call (₹0 cost to elder) and callback in 2 seconds
        schedule_missed_call_callback(elder.id, elder.phone)
        return _twiml(build_reject())

    # Serve the inbound voice menu directly
    return await _serve_inbound_menu(elder, base)


@router.post("/inbound-menu")
async def inbound_menu(elder_id: str = Query(...)):
    """Serve the inbound voice menu (also used for missed-call callbacks)."""
    base = _base_url()
    elder = fb.get_elder(elder_id)
    if not elder:
        return _twiml(build_play_only(f"{base}/audio/error.wav"))
    return await _serve_inbound_menu(elder, base)


async def _serve_inbound_menu(elder, base):
    """Build the inbound greeting + gather for elder's request."""
    lang = elder.language.value
    menu_text = _get_text("inbound_menu", lang, elder.honorific)
    audio_url = await ensure_audio(menu_text, lang, base)
    action_url = f"{base}/voice/inbound-response?elder_id={elder.id}"
    return _twiml(build_play_and_gather(audio_url, action_url, dtmf_timeout=6, language=lang))


@router.post("/inbound-response")
async def inbound_response(
    request: Request,
    elder_id: str = Query(...),
):
    """
    Handle the elder's inbound request:
    - Double-dose check ("Did I take my tablets?")
    - Speed-dial call ("Call my son", "Call 1")
    - General conversation
    """
    form = await request.form()
    digits = form.get("Digits", "")
    speech_result = form.get("SpeechResult", "")
    recording_url = form.get("RecordingUrl", "")
    base = _base_url()

    elder = fb.get_elder(elder_id)
    if not elder:
        return _twiml(build_play_only(f"{base}/audio/error.wav"))

    lang = elder.language.value
    transcript = speech_result

    # Handle DTMF: digit = speed-dial slot
    if digits and digits.isdigit():
        contact = fb.find_speed_dial_by_alias(elder_id, digits)
        if contact:
            return await _bridge_call(elder, contact, base, lang)
        else:
            msg = _get_text("speed_dial_not_found", lang, elder.honorific, digits)
            audio_url = await ensure_audio(msg, lang, base)
            return _twiml(build_play_only(audio_url))

    # Handle speech
    if not transcript and recording_url:
        try:
            sid = os.getenv("TWILIO_ACCOUNT_SID")
            token = os.getenv("TWILIO_AUTH_TOKEN")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{recording_url}.wav", auth=(sid, token))
                if resp.status_code == 200:
                    transcript = await speech_to_text(resp.content, lang)
        except Exception as exc:
            logger.error(f"Inbound recording STT failed: {exc}")

    if not transcript:
        msg = _get_text("no_input", lang, elder.honorific)
        audio_url = await ensure_audio(msg, lang, base)
        return _twiml(build_play_only(audio_url))

    # Extract intent
    intent_result = await extract_intent(transcript, lang, "inbound help menu")

    if intent_result.intent == "DOUBLE_DOSE_CHECK":
        return await _handle_double_dose_check(elder, base, lang)
    elif intent_result.intent == "CALL_PERSON" and intent_result.target_person:
        contact = fb.find_speed_dial_by_alias(elder_id, intent_result.target_person)
        if contact:
            return await _bridge_call(elder, contact, base, lang)
        else:
            msg = _get_text("contact_not_found", lang, elder.honorific, intent_result.target_person)
            audio_url = await ensure_audio(msg, lang, base)
            return _twiml(build_play_only(audio_url))
    else:
        # General reply
        reply = intent_result.reply_text or _get_text("general_help", lang, elder.honorific)
        audio_url = await ensure_audio(reply, lang, base)
        return _twiml(build_play_only(audio_url))


# ── Double-Dose Prevention Check ──────────────────────────────────────

async def _handle_double_dose_check(elder, base, lang):
    """Query today's dose logs and tell the elder what they've already taken."""
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    logs = fb.get_dose_logs_for_date(elder.id, today_str)

    taken_logs = [l for l in logs if l.status == DoseStatus.TAKEN]
    scheduled_logs = [l for l in logs if l.status == DoseStatus.SCHEDULED]

    if taken_logs:
        taken_names = ", ".join(l.medication_name for l in taken_logs)
        taken_times = ", ".join(
            l.taken_at.strftime("%I:%M %p") if l.taken_at else l.scheduled_time
            for l in taken_logs
        )
        msg = _get_text("double_dose_taken", lang, elder.honorific, taken_names, taken_times)
        if scheduled_logs:
            next_med = scheduled_logs[0]
            msg += f" Your next medicine is at {next_med.scheduled_time}."
    else:
        msg = _get_text("double_dose_none", lang, elder.honorific)

    # Alert caregiver that elder checked
    fb.send_caregiver_alert(
        elder_id=elder.id,
        alert_type=AlertType.DOUBLE_DOSE_CHECK,
        title=f"💊 Double-Dose Check: {elder.name}",
        message=f"{elder.name} asked if they took their medicine. Status communicated.",
        priority=AlertPriority.LOW,
    )

    audio_url = await ensure_audio(msg, lang, base)
    return _twiml(build_play_only(audio_url))


# ── Speed-Dial Call Bridging ──────────────────────────────────────────

async def _bridge_call(elder, contact, base, lang):
    """Announce the connection and bridge-dial to the family member."""
    announce = _get_text("bridge_announce", lang, elder.honorific, contact.name, contact.relationship)
    audio_url = await ensure_audio(announce, lang, base)

    from twilio.twiml.voice_response import VoiceResponse, Dial
    response = VoiceResponse()
    response.play(audio_url)
    dial = Dial(caller_id=os.getenv("TWILIO_PHONE_NUMBER"))
    dial.number(contact.phone)
    response.append(dial)
    return _twiml(str(response))


# ══════════════════════════════════════════════════════════════════════
#  TEXT TEMPLATES (Multi-language)
# ══════════════════════════════════════════════════════════════════════

def _get_text(key: str, lang: str, *args) -> str:
    """
    Get localised text template. Falls back to English.
    Templates use {} positional args: {0}=honorific, {1}=caregiver, etc.
    """
    templates = TEXTS.get(key, {})
    text = templates.get(lang, templates.get("en-IN", ""))
    try:
        return text.format(*args) if args else text
    except (IndexError, KeyError):
        return text


TEXTS = {
    "thank_taken": {
        "en-IN": "Very good {0}! Thank you for taking your medicine. Stay healthy!",
        "hi-IN": "Bahut accha {0}! Dawai lene ke liye dhanyavaad. Apna khayal rakhiye!",
        "kn-IN": "Thumba olledu {0}! Aushadhi thegedukondiddakke dhanyavadagalu. Arogya irru!",
        "te-IN": "Chala bagundi {0}! Mandhu teskunninanduku dhanyavaadaalu. Aarogyam ga undandi!",
        "ta-IN": "Romba nalla irukku {0}! Marundhu edutthatharku nandri. Udal nalam kaappaarunga!",
    },
    "thank_wellness_intro": {
        "en-IN": "Very good {0}! Thank you for taking your medicine. One quick question — are you feeling okay today? Any dizziness, pain, or discomfort?",
        "hi-IN": "Bahut accha {0}! Ek chhoti baat — aaj tabiyat kaisi hai? Koi chakkar, dard ya takleef?",
        "kn-IN": "Thumba olledu {0}! Ondu sanna prashne — indu hege anisuthide? Thaledovu, novu, yenaadru thondre ide?",
        "te-IN": "Chala bagundi {0}! Oka chinna vishayam — ee roju meeru ela unnaru? Tala tindam, noppi emanna unda?",
        "ta-IN": "Romba nalla irukku {0}! Oru sinna kelvee — innaiku epdi irukkinga? Talaivali, vali ethaavadhu irukkaa?",
    },
    "wellness_thanks": {
        "en-IN": "Thank you {0}. Your caregiver will see your update. Take care!",
        "hi-IN": "Dhanyavaad {0}. Aapke parivaar ko pata chal jayega. Apna khayal rakhiye!",
        "kn-IN": "Dhanyavadagalu {0}. Nimma kutumbakke gottagutthe. Jopana thegedukoli!",
        "te-IN": "Dhanyavaadaalu {0}. Mee vaallu chusthaaru. Jaagrattaga undandi!",
        "ta-IN": "Nandri {0}. Unga kudumbatthukkku theriyum. Kavanama irunga!",
    },
    "snooze_confirm": {
        "en-IN": "Okay {0}, I will call you again in {1} minutes. Please take your medicine then.",
        "hi-IN": "Theek hai {0}, main {1} minute baad dobara call karungi. Tab dawai zaroor lena.",
        "kn-IN": "Sari {0}, {1} nimisha nanthara matte call maadthini. Aaga aushadhi thegedukoli.",
        "te-IN": "Sare {0}, {1} nimishaala tharvaatha malli call chesthanu. Appudu mandhu vesukondi.",
        "ta-IN": "Sari {0}, {1} nimisham kazhichu meendum call pannuven. Appodhuthu marundhu edungunga.",
    },
    "unclear_retry": {
        "en-IN": "Sorry {0}, I could not hear clearly. Did you take {1}? Please say yes, or press 1 on your phone.",
        "hi-IN": "Maaf kijiye {0}, mujhe sunai nahi diya. Kya aapne {1} li? Haan boliye, ya phone pe 1 dabayiye.",
        "kn-IN": "Kshamisee {0}, nanage spashtavagi kelisillla. {1} thegondira? Howdu annri, athava phone nalli 1 dabbisi.",
        "te-IN": "Kshaminchandi {0}, naaku vinaledhu. {1} veskunnaara? Avunu cheppandi, leda phone lo 1 noppandi.",
        "ta-IN": "Mannikkanum {0}, enakku kaetkavillai. {1} eduttingalaa? Aamam sollunnga, illanal phone la 1 pottunnga.",
    },
    "unclear_goodbye": {
        "en-IN": "I'm sorry {0}, I still could not understand. Your caregiver has been notified. Take care!",
        "hi-IN": "Maaf kijiye {0}, mujhe samajh nahi aaya. Aapke parivaar ko bataa diya hai. Apna khayal rakhiye!",
        "kn-IN": "Kshamisee {0}, nanage arthavagillla. Nimma kutumbakke tilisiddeeni. Jopana!",
        "te-IN": "Kshaminchandi {0}, naaku artham kaledhu. Mee vaallu ku cheppesanu. Jaagratta!",
        "ta-IN": "Mannikkanum {0}, enakku puriyavillai. Unga kudumbatthukku theriyapaduthinein. Kavanama irunga!",
    },
    "inbound_menu": {
        "en-IN": "Namaskara {0}, how can I help you? You can ask if you took your tablets, or tell me who you want to call.",
        "hi-IN": "Namaste {0}, main aapki kya madad kar sakti hoon? Aap pooch sakte hain ki aapne dawai li ya nahi, ya batayiye kisko call karna hai.",
        "kn-IN": "Namaskara {0}, naan hege sahaya maadali? Neevu tablet thegondiddeera antha kelabahu, athava yaarige call maadabeku antha heli.",
        "te-IN": "Namaskaram {0}, nenu ela sahayam cheyagalanu? Meeru mandhu veskunnaara ani adagachu, leda evariki call cheyaalo cheppandi.",
        "ta-IN": "Vanakkam {0}, naan eppadi udhavuvathu? Marundhu edutthingalaa nu kekkalam, illanal yaaru kku call pannanum nu sollunnga.",
    },
    "double_dose_taken": {
        "en-IN": "Yes {0}, you already took {1} at {2}.",
        "hi-IN": "Haan {0}, aapne {1} {2} baje le li thi.",
        "kn-IN": "Howdu {0}, neevu {1} {2} horege thegondiddira.",
        "te-IN": "Avunu {0}, meeru {1} ni {2} ki veskunnaaru.",
        "ta-IN": "Aamam {0}, neengal {1} ai {2} kku eduthu vittingal.",
    },
    "double_dose_none": {
        "en-IN": "No {0}, you have not taken any medicine yet today. Please wait for the reminder call.",
        "hi-IN": "Nahi {0}, aaj aapne abhi dawai nahi li hai. Reminder call ka intezaar kijiye.",
        "kn-IN": "Illa {0}, indu neevu innoo aushadhi thegondilla. Reminder call ge kaayri.",
        "te-IN": "Ledhu {0}, ee roju meeru inka mandhu vesukoledhu. Reminder call kosam wait cheyandi.",
        "ta-IN": "Illai {0}, innaiku neengal innum marundhu edukkavillai. Reminder call ku kaathirunga.",
    },
    "bridge_announce": {
        "en-IN": "Okay {0}, connecting you to {1} your {2} now.",
        "hi-IN": "Theek hai {0}, aapko {1}, aapke {2}, se jod raha hoon.",
        "kn-IN": "Sari {0}, nimma {2} {1} ge connect maadthiddeeni.",
        "te-IN": "Sare {0}, mee {2} {1} ki connect chesthunnanu.",
        "ta-IN": "Sari {0}, unga {2} {1} ku connect pannukinren.",
    },
    "speed_dial_not_found": {
        "en-IN": "Sorry {0}, speed dial {1} is not set. Please ask your caregiver to add it.",
        "hi-IN": "Maaf kijiye {0}, speed dial {1} set nahi hai.",
        "kn-IN": "Kshamisee {0}, speed dial {1} set aagilla.",
        "te-IN": "Kshaminchandi {0}, speed dial {1} set cheyyaledhu.",
        "ta-IN": "Mannikkanum {0}, speed dial {1} set aagavillai.",
    },
    "contact_not_found": {
        "en-IN": "Sorry {0}, I could not find {1} in your contacts.",
        "hi-IN": "Maaf kijiye {0}, {1} aapke contacts mein nahi mila.",
        "kn-IN": "Kshamisee {0}, {1} nimma contacts nalli sigalilla.",
        "te-IN": "Kshaminchandi {0}, {1} mee contacts lo dorakaledhu.",
        "ta-IN": "Mannikkanum {0}, {1} unga contacts la kaanomey.",
    },
    "no_input": {
        "en-IN": "I did not hear anything {0}. Please try calling again.",
        "hi-IN": "{0}, kuch sunai nahi diya. Dobara call kijiye.",
        "kn-IN": "{0}, enoo kelisillla. Matte call maadi.",
        "te-IN": "{0}, emee vinaledu. Malli call cheyandi.",
        "ta-IN": "{0}, onnum kaetkavillai. Meendum call pannunga.",
    },
    "general_help": {
        "en-IN": "I am here to help you {0}. You can ask about your medicines or tell me who to call.",
        "hi-IN": "{0}, main aapki madad ke liye hoon. Dawai ke baare mein poochiye ya batayiye kisko call karna hai.",
        "kn-IN": "{0}, naanu nimma sahayakke iddeeni. Aushadhi bagge kelri athava yaarige call maadabeku antha heli.",
        "te-IN": "{0}, nenu mee sahayaaniki unnanu. Mandhu gurinchi adagandi leda evariki call cheyaalo cheppandi.",
        "ta-IN": "{0}, naan unga udhavikku irukkein. Marundhu patti kelunga illanal yaaru kku call pannanum sollunnga.",
    },
}


# ══════════════════════════════════════════════════════════════════════
#  HELPER: Build Medication Description
# ══════════════════════════════════════════════════════════════════════

def _build_med_description(med, accessibility_mode: str) -> str:
    """Build a human-readable medication description based on accessibility mode."""
    if not med:
        return "your medicine"

    parts = []
    if med.tablet_count:
        parts.append(f"{med.tablet_count}")

    if accessibility_mode == "tactile_pillbox":
        if med.tactile_description:
            parts.append(med.tactile_description)
        elif med.tactile_slot:
            parts.append(f"from {med.tactile_slot}")
        else:
            parts.append("tablet" if med.tablet_count == 1 else "tablets")
    else:
        # Visual color mode
        if med.color:
            parts.append(med.color)
        if med.shape:
            parts.append(med.shape)
        else:
            parts.append("tablet" if med.tablet_count == 1 else "tablets")

    desc = " ".join(parts)

    if med.tonic_name:
        tonic = med.tonic_name
        if med.tonic_color:
            tonic = f"{med.tonic_color} {tonic}"
        if med.tonic_dose:
            tonic = f"{med.tonic_dose} of {tonic}"
        desc += f" and {tonic}"

    return desc


def _meal_timing_text(meal_timing: str, lang: str) -> str:
    """Get meal timing in the elder's language."""
    timings = {
        "before_food": {
            "en-IN": "before food", "hi-IN": "khana khane se pehle",
            "kn-IN": "oota maaduvudakke munche", "te-IN": "bhojanam ki mundhu",
            "ta-IN": "saappiduvatharkku munnaadi",
        },
        "after_food": {
            "en-IN": "after food", "hi-IN": "khana khane ke baad",
            "kn-IN": "oota maadida mele", "te-IN": "bhojanam tharvaatha",
            "ta-IN": "saappitta appuram",
        },
        "with_food": {
            "en-IN": "with food", "hi-IN": "khane ke saath",
            "kn-IN": "oota jothege", "te-IN": "bhojanam tho paatu",
            "ta-IN": "saappaattodu",
        },
        "anytime": {
            "en-IN": "", "hi-IN": "", "kn-IN": "", "te-IN": "", "ta-IN": "",
        },
    }
    return timings.get(meal_timing, {}).get(lang, "")


def _build_greeting(honorific, caregiver_name, time_greeting, med_desc, meal_text, language):
    """Build the full trust greeting in the elder's language."""
    greetings = {
        "en-IN": f"Namaskara {honorific}, your {caregiver_name} set this reminder. Good {time_greeting}! Please take {med_desc} {meal_text}. Tell me once you have taken them, or press 1 on your phone.",
        "hi-IN": f"Namaste {honorific}, aapke {caregiver_name} ne yeh reminder lagaya hai. {med_desc} {meal_text} leni hai. Jab le lein to bata dijiye, ya phone pe 1 dabayiye.",
        "kn-IN": f"Namaskara {honorific}, nimma {caregiver_name} ee reminder set maadidaare. {med_desc} {meal_text} thegedukoli. Thegondamele nanage heli, athava phone nalli 1 dabbisi.",
        "te-IN": f"Namaskaram {honorific}, mee {caregiver_name} ee reminder set chesaaru. {med_desc} {meal_text} vesukondi. Veskunna tharvaatha cheppandi, leda phone lo 1 noppandi.",
        "ta-IN": f"Vanakkam {honorific}, unga {caregiver_name} indha ninaivoorutthali set pannaanga. {med_desc} {meal_text} edungunga. Eduttha aprom sollunnga, illanal phone la 1 pottunnga.",
    }
    return greetings.get(language, greetings["en-IN"])
