"""
FastAPI main application — entry point for the Voice Medication Reminder backend.

Startup:
  cd backend
  .venv\\Scripts\\activate        (Windows)
  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Load .env from project root
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

from app.services.audio_cache import AUDIO_CACHE_DIR, init_audio_cache

# Ensure audio_cache dir exists before StaticFiles mount
AUDIO_CACHE_DIR.mkdir(exist_ok=True)
from app.services.firebase_service import init_firebase
from app.services.gemini_service import init_gemini
from app.services.sarvam_service import init_sarvam
from app.services.scheduler_service import start_scheduler, stop_scheduler
from app.services.telephony_service import init_telephony

from app.routers import voice, caregiver, elder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise all services on startup, clean up on shutdown."""
    logger.info("Initialising services...")
    init_firebase()
    init_sarvam()
    init_gemini()
    init_telephony()
    init_audio_cache()
    start_scheduler()
    logger.info("All services initialised — backend is ready!")
    yield
    stop_scheduler()
    logger.info("Backend shut down cleanly.")


app = FastAPI(
    title="ArogyaVoice — Medication Reminder API",
    description="Voice-first medication reminder, double-dose prevention & family speed-dial for elderly users in India.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow React dev server and any TWA/PWA origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve cached TTS audio files for Twilio <Play>
app.mount("/audio", StaticFiles(directory=str(AUDIO_CACHE_DIR)), name="audio")


# ── Health Check ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ArogyaVoice Backend"}


# ── Register Routers ───────────────────────────────────────────────────
app.include_router(voice.router, prefix="/voice", tags=["Voice Webhooks"])
app.include_router(caregiver.router, prefix="/api", tags=["Caregiver API"])
app.include_router(elder.router, prefix="/elder", tags=["Elder API"])


# ── Convenience: direct trigger for hackathon testing ──────────────────

@app.post("/api/trigger-call")
async def trigger_call_now(elder_id: str, medication_id: str | None = None):
    """
    Hackathon demo button: immediately trigger a medication reminder call
    without waiting for the scheduler.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.models.schemas import CallDirection, DoseLog, DoseStatus
    from app.services import firebase_service as fb
    from app.services.telephony_service import make_call

    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    elder = fb.get_elder(elder_id)
    if not elder:
        return {"error": "Elder not found"}, 404

    # Pick the specific medication or the first active one
    meds = fb.get_medications(elder_id, active_only=True)
    if not meds:
        return {"error": "No active medications found"}, 404

    med = None
    if medication_id:
        med = next((m for m in meds if m.id == medication_id), None)
    if not med:
        med = meds[0]

    # Create dose log
    log = DoseLog(
        elder_id=elder_id,
        medication_id=med.id,
        medication_name=med.medicine_name,
        scheduled_time=now.strftime("%H:%M"),
        date=today_str,
        status=DoseStatus.SCHEDULED,
        call_attempts=1,
        call_direction=CallDirection.OUTBOUND,
    )
    log_id = fb.create_dose_log(log)

    webhook_url = f"{base_url}/voice/outbound-greeting?elder_id={elder_id}&med_id={med.id}&log_id={log_id}"
    status_url = f"{base_url}/voice/call-status?elder_id={elder_id}&log_id={log_id}"

    try:
        call_sid = make_call(elder.phone, webhook_url, status_url)
        fb.update_dose_log(elder_id, log_id, {"call_sid": call_sid})
        return {
            "status": "call_placed",
            "call_sid": call_sid,
            "elder": elder.name,
            "medication": med.medicine_name,
            "log_id": log_id,
        }
    except Exception as exc:
        return {"error": str(exc)}, 500
