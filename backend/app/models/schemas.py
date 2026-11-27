"""
Pydantic schemas for the Voice Medication Reminder system.
These models define Firestore document shapes and API request/response bodies.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class Language(str, Enum):
    KANNADA = "kn-IN"
    HINDI = "hi-IN"
    TELUGU = "te-IN"
    TAMIL = "ta-IN"
    ENGLISH = "en-IN"


class AccessibilityMode(str, Enum):
    VISUAL_COLOR = "visual_color"       # Describes pills by color/shape
    TACTILE_PILLBOX = "tactile_pillbox"  # For blind users — box slot + shape


class MealTiming(str, Enum):
    BEFORE_FOOD = "before_food"
    AFTER_FOOD = "after_food"
    WITH_FOOD = "with_food"
    ANYTIME = "anytime"


class DoseStatus(str, Enum):
    SCHEDULED = "scheduled"
    TAKEN = "taken"
    SNOOZED = "snoozed"
    MISSED = "missed"


class AlertType(str, Enum):
    MISSED_DOSE = "missed_dose"
    WELLNESS_SYMPTOM = "wellness_symptom"
    UNCLEAR_RESPONSE = "unclear_response"
    ELDER_CALLED = "elder_called"
    DOUBLE_DOSE_CHECK = "double_dose_check"
    SYSTEM = "system"


class AlertPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class CallDirection(str, Enum):
    OUTBOUND = "outbound"  # System calling elder
    INBOUND = "inbound"    # Elder calling system


# ── Elder Profile ──────────────────────────────────────────────────────

class ElderProfile(BaseModel):
    """Firestore: elders/{elder_id}"""
    id: Optional[str] = None
    name: str
    honorific: str = "Amma"           # Amma, Appa, Dadaji, etc.
    phone: str                        # +91XXXXXXXXXX
    caregiver_name: str               # Son/daughter name (used in trust greeting)
    caregiver_uid: str                # Firebase Auth UID of caregiver
    language: Language = Language.KANNADA
    accessibility_mode: AccessibilityMode = AccessibilityMode.VISUAL_COLOR
    missed_call_callback: bool = False  # Elder has ₹0 SIM balance
    evening_wellness_check: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Medication Schedule ────────────────────────────────────────────────

class Medication(BaseModel):
    """Firestore: elders/{elder_id}/medications/{med_id}"""
    id: Optional[str] = None
    elder_id: Optional[str] = None
    medicine_name: str                # For caregiver reference
    time: str                         # "08:00", "14:00", "20:00" (24h IST)
    meal_timing: MealTiming = MealTiming.AFTER_FOOD
    tablet_count: int = 1
    # Visual mode fields
    color: Optional[str] = None       # "green", "white", "red"
    shape: Optional[str] = None       # "round", "oval", "capsule"
    # Tactile mode fields
    tactile_slot: Optional[str] = None  # "first box on the left"
    tactile_description: Optional[str] = None  # "one big oval tablet"
    # Tonic (liquid medicine)
    tonic_name: Optional[str] = None
    tonic_color: Optional[str] = None
    tonic_dose: Optional[str] = None  # "one spoon"
    is_active: bool = True
    created_at: Optional[datetime] = None


# ── Dose Log (Daily Tracking) ─────────────────────────────────────────

class DoseLog(BaseModel):
    """Firestore: elders/{elder_id}/dose_logs/{log_id}"""
    id: Optional[str] = None
    elder_id: Optional[str] = None
    medication_id: str
    medication_name: str
    scheduled_time: str               # "08:00"
    date: str                         # "2026-11-27"
    status: DoseStatus = DoseStatus.SCHEDULED
    taken_at: Optional[datetime] = None
    snoozed_until: Optional[datetime] = None
    snooze_count: int = 0
    call_attempts: int = 0
    elder_response_text: Optional[str] = None  # What the elder said (STT)
    wellness_summary: Optional[str] = None     # Gemini-summarised symptoms
    call_direction: Optional[CallDirection] = None
    call_sid: Optional[str] = None    # Twilio Call SID
    updated_at: Optional[datetime] = None


# ── Speed Dial Contact ─────────────────────────────────────────────────

class SpeedDialContact(BaseModel):
    """Firestore: elders/{elder_id}/speed_dial/{slot}"""
    slot: int                         # 1, 2, 3, ...
    name: str                         # "Rahul"
    relationship: str                 # "son", "daughter", "doctor"
    aliases: list[str] = []           # ["beta", "maga", "son"]
    phone: str                        # +91XXXXXXXXXX
    elder_id: Optional[str] = None


# ── Caregiver Alert ────────────────────────────────────────────────────

class Alert(BaseModel):
    """Firestore: elders/{elder_id}/alerts/{alert_id}"""
    id: Optional[str] = None
    elder_id: str
    type: AlertType
    priority: AlertPriority = AlertPriority.MEDIUM
    title: str
    message: str
    medication_name: Optional[str] = None
    is_read: bool = False
    created_at: Optional[datetime] = None


# ── API Request/Response Bodies ────────────────────────────────────────

class TriggerCallRequest(BaseModel):
    """POST /api/trigger-call — hackathon demo button"""
    elder_id: str
    medication_id: Optional[str] = None  # If None, pick next due


class PillStripScanRequest(BaseModel):
    """POST /api/pill-scan — Gemini Vision pill-strip scanner"""
    elder_id: str
    # Image sent as multipart file upload


class PillStripScanResult(BaseModel):
    medicine_name: str
    color: Optional[str] = None
    shape: Optional[str] = None
    dosage: Optional[str] = None
    possible_duplicates: list[str] = []  # Already-scheduled generics


class IntentResult(BaseModel):
    """Gemini intent extraction result from elder's speech."""
    intent: str  # TAKEN, SNOOZE, UNCLEAR, DOUBLE_DOSE_CHECK, CALL_PERSON, WELLNESS_REPORT, GREETING, OTHER
    confidence: float = 0.0
    snooze_minutes: Optional[int] = 15
    target_person: Optional[str] = None  # For CALL_PERSON intent
    wellness_summary: Optional[str] = None  # For WELLNESS_REPORT
    reply_text: str = ""  # Natural regional reply to speak back
