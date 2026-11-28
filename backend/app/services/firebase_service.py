"""
Firebase Admin SDK initialiser + Firestore CRUD helpers.
All dose logs, alerts, and profile operations flow through here.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("firebase")

import firebase_admin
from firebase_admin import auth as fb_auth, credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

from app.models.schemas import (
    Alert,
    AlertPriority,
    AlertType,
    DoseLog,
    DoseStatus,
    ElderProfile,
    Medication,
    SpeedDialContact,
)

_db = None
_init_attempted = False

# ── In-Memory Fallback Store (for zero-key / offline evaluation) ────────
_IN_MEMORY_ELDERS: dict[str, ElderProfile] = {
    "mPXwuTecKLZ9vrBTyjvY": ElderProfile(
        id="mPXwuTecKLZ9vrBTyjvY",
        name="Savitri Devi",
        honorific="Amma",
        phone="+916360014779",
        caregiver_name="Rajesh",
        caregiver_uid="caregiver_demo_uid",
        language="te-IN",
        accessibility_mode="visual_color",
        missed_call_callback=False,
        evening_wellness_check=True,
    )
}

_IN_MEMORY_MEDICATIONS: dict[str, dict[str, Medication]] = {
    "mPXwuTecKLZ9vrBTyjvY": {
        "med_glycomet": Medication(
            id="med_glycomet",
            elder_id="mPXwuTecKLZ9vrBTyjvY",
            medicine_name="Glycomet 500mg",
            time="08:00",
            meal_timing="after_food",
            tablet_count=1,
            color="white",
            shape="round",
            is_active=True,
        ),
        "med_amlong": Medication(
            id="med_amlong",
            elder_id="mPXwuTecKLZ9vrBTyjvY",
            medicine_name="Amlong 5mg",
            time="20:00",
            meal_timing="after_food",
            tablet_count=1,
            color="green",
            shape="round",
            is_active=True,
        ),
    }
}

_IN_MEMORY_SPEED_DIALS: dict[str, dict[int, SpeedDialContact]] = {
    "mPXwuTecKLZ9vrBTyjvY": {
        1: SpeedDialContact(
            slot=1,
            name="Rajesh",
            relationship="son",
            aliases=["son", "maga", "koduku"],
            phone="+916360014779",
            elder_id="mPXwuTecKLZ9vrBTyjvY",
        )
    }
}

_IN_MEMORY_DOSE_LOGS: dict[str, dict[str, DoseLog]] = {}
_IN_MEMORY_ALERTS: dict[str, list[Alert]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def init_firebase() -> None:
    """Initialize Firebase Admin or gracefully fall back to in-memory store."""
    global _db, _init_attempted
    _init_attempted = True
    if firebase_admin._apps:
        try:
            _db = firestore.client()
            return
        except Exception:
            _db = None

    sa_path = os.getenv(
        "FIREBASE_SERVICE_ACCOUNT_PATH", "firebase-service-account.json"
    )
    resolved = Path(sa_path)
    if not resolved.is_absolute():
        candidates = [
            Path(__file__).resolve().parents[3] / sa_path,
            Path(__file__).resolve().parents[2] / sa_path,
            Path.cwd() / sa_path,
        ]
        resolved = next((p for p in candidates if p.exists()), None)

    if not resolved or not resolved.exists():
        logger.warning(
            f"Firebase service account file '{sa_path}' not found. "
            "Running with in-memory database (zero-key mode)."
        )
        _db = None
        return

    try:
        cred = credentials.Certificate(str(resolved))
        firebase_admin.initialize_app(cred)
        _db = firestore.client()
        logger.info("Firebase Admin & Firestore successfully initialized!")
    except Exception as exc:
        logger.warning(f"Firebase init error: {exc}. Using in-memory fallback.")
        _db = None


def get_db():
    global _init_attempted
    if not _init_attempted:
        init_firebase()
    return _db


# ── Elder Profile CRUD ─────────────────────────────────────────────────

def create_elder(profile: ElderProfile) -> str:
    db = get_db()
    profile.created_at = _now()
    profile.updated_at = _now()
    if db is None:
        if not profile.id:
            profile.id = str(uuid.uuid4())[:8]
        _IN_MEMORY_ELDERS[profile.id] = profile
        return profile.id
    doc_ref = db.collection("elders").document()
    profile.id = doc_ref.id
    doc_ref.set(profile.model_dump(exclude_none=True))
    return doc_ref.id


def get_elder(elder_id: str) -> Optional[ElderProfile]:
    db = get_db()
    if db is None:
        return _IN_MEMORY_ELDERS.get(elder_id) or next(iter(_IN_MEMORY_ELDERS.values()), None)
    snap = db.collection("elders").document(elder_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    data["id"] = snap.id
    return ElderProfile(**data)


def get_elder_by_phone(phone: str) -> Optional[ElderProfile]:
    db = get_db()
    if db is None:
        for e in _IN_MEMORY_ELDERS.values():
            if e.phone == phone:
                return e
        return next(iter(_IN_MEMORY_ELDERS.values()), None)
    docs = (
        db.collection("elders")
        .where(filter=FieldFilter("phone", "==", phone))
        .limit(1)
        .get()
    )
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        return ElderProfile(**data)
    return None


def update_elder(elder_id: str, updates: dict) -> None:
    updates["updated_at"] = _now()
    db = get_db()
    if db is None:
        elder = _IN_MEMORY_ELDERS.get(elder_id)
        if elder:
            for k, v in updates.items():
                if hasattr(elder, k):
                    setattr(elder, k, v)
        return
    db.collection("elders").document(elder_id).update(updates)


def list_elders_for_caregiver(caregiver_uid: str) -> list[ElderProfile]:
    db = get_db()
    if db is None:
        return list(_IN_MEMORY_ELDERS.values())
    docs = (
        db.collection("elders")
        .where(filter=FieldFilter("caregiver_uid", "==", caregiver_uid))
        .get()
    )
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(ElderProfile(**data))
    return result or list(_IN_MEMORY_ELDERS.values())


# ── Medication CRUD ────────────────────────────────────────────────────

def add_medication(elder_id: str, med: Medication) -> str:
    db = get_db()
    med.elder_id = elder_id
    med.created_at = _now()
    if db is None:
        if not med.id:
            med.id = f"med_{str(uuid.uuid4())[:8]}"
        if elder_id not in _IN_MEMORY_MEDICATIONS:
            _IN_MEMORY_MEDICATIONS[elder_id] = {}
        _IN_MEMORY_MEDICATIONS[elder_id][med.id] = med
        return med.id
    doc_ref = db.collection("elders").document(elder_id).collection("medications").document()
    med.id = doc_ref.id
    doc_ref.set(med.model_dump(exclude_none=True))
    return doc_ref.id


def get_medications(elder_id: str, active_only: bool = True) -> list[Medication]:
    db = get_db()
    if db is None:
        meds = list(_IN_MEMORY_MEDICATIONS.get(elder_id, {}).values())
        if active_only:
            meds = [m for m in meds if m.is_active]
        return meds
    query = db.collection("elders").document(elder_id).collection("medications")
    if active_only:
        query = query.where(filter=FieldFilter("is_active", "==", True))
    docs = query.get()
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(Medication(**data))
    return result


def update_medication(elder_id: str, med_id: str, updates: dict) -> None:
    db = get_db()
    if db is None:
        med = _IN_MEMORY_MEDICATIONS.get(elder_id, {}).get(med_id)
        if med:
            for k, v in updates.items():
                if hasattr(med, k):
                    setattr(med, k, v)
        return
    db.collection("elders").document(elder_id).collection("medications").document(med_id).update(updates)


def delete_medication(elder_id: str, med_id: str) -> None:
    update_medication(elder_id, med_id, {"is_active": False})


# ── Dose Log CRUD ──────────────────────────────────────────────────────

def create_dose_log(log: DoseLog) -> str:
    db = get_db()
    log.updated_at = _now()
    if db is None:
        if not log.id:
            log.id = f"log_{str(uuid.uuid4())[:8]}"
        if log.elder_id not in _IN_MEMORY_DOSE_LOGS:
            _IN_MEMORY_DOSE_LOGS[log.elder_id] = {}
        _IN_MEMORY_DOSE_LOGS[log.elder_id][log.id] = log
        return log.id
    doc_ref = (
        db.collection("elders")
        .document(log.elder_id)
        .collection("dose_logs")
        .document()
    )
    log.id = doc_ref.id
    doc_ref.set(log.model_dump(exclude_none=True))
    return doc_ref.id


def get_dose_logs_for_date(elder_id: str, date_str: str) -> list[DoseLog]:
    db = get_db()
    if db is None:
        logs = [
            l for l in _IN_MEMORY_DOSE_LOGS.get(elder_id, {}).values()
            if l.date == date_str
        ]
        if not logs:
            # Auto-generate mock dose log for existing medications so timeline shows data
            meds = get_medications(elder_id, active_only=True)
            for m in meds:
                auto_log = DoseLog(
                    id=f"log_{m.id}_{date_str}",
                    elder_id=elder_id,
                    medication_id=m.id,
                    medication_name=m.medicine_name,
                    scheduled_time=m.time,
                    date=date_str,
                    status=DoseStatus.SCHEDULED,
                    call_attempts=1,
                    call_direction=CallDirection.OUTBOUND,
                )
                create_dose_log(auto_log)
            logs = [
                l for l in _IN_MEMORY_DOSE_LOGS.get(elder_id, {}).values()
                if l.date == date_str
            ]
        return logs
    docs = (
        db.collection("elders")
        .document(elder_id)
        .collection("dose_logs")
        .where(filter=FieldFilter("date", "==", date_str))
        .get()
    )
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(DoseLog(**data))
    return result


def update_dose_log(elder_id: str, log_id: str, updates: dict) -> None:
    updates["updated_at"] = _now()
    db = get_db()
    if db is None:
        log = _IN_MEMORY_DOSE_LOGS.get(elder_id, {}).get(log_id)
        if log:
            for k, v in updates.items():
                if hasattr(log, k):
                    setattr(log, k, v)
        return
    (
        db
        .collection("elders")
        .document(elder_id)
        .collection("dose_logs")
        .document(log_id)
        .update(updates)
    )


def find_dose_log(elder_id: str, medication_id: str, date_str: str) -> Optional[DoseLog]:
    db = get_db()
    if db is None:
        for l in _IN_MEMORY_DOSE_LOGS.get(elder_id, {}).values():
            if l.medication_id == medication_id and l.date == date_str:
                return l
        return None
    docs = (
        db.collection("elders")
        .document(elder_id)
        .collection("dose_logs")
        .where(filter=FieldFilter("medication_id", "==", medication_id))
        .where(filter=FieldFilter("date", "==", date_str))
        .limit(1)
        .get()
    )
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        return DoseLog(**data)
    return None


# ── Speed Dial CRUD ────────────────────────────────────────────────────

def set_speed_dial(elder_id: str, contact: SpeedDialContact) -> None:
    db = get_db()
    contact.elder_id = elder_id
    if db is None:
        if elder_id not in _IN_MEMORY_SPEED_DIALS:
            _IN_MEMORY_SPEED_DIALS[elder_id] = {}
        _IN_MEMORY_SPEED_DIALS[elder_id][contact.slot] = contact
        return
    (
        db.collection("elders")
        .document(elder_id)
        .collection("speed_dial")
        .document(str(contact.slot))
        .set(contact.model_dump(exclude_none=True))
    )


def get_speed_dials(elder_id: str) -> list[SpeedDialContact]:
    db = get_db()
    if db is None:
        return list(_IN_MEMORY_SPEED_DIALS.get(elder_id, {}).values())
    docs = (
        db.collection("elders")
        .document(elder_id)
        .collection("speed_dial")
        .order_by("slot")
        .get()
    )
    return [SpeedDialContact(**doc.to_dict()) for doc in docs]


def find_speed_dial_by_alias(elder_id: str, spoken_name: str) -> Optional[SpeedDialContact]:
    contacts = get_speed_dials(elder_id)
    spoken_lower = spoken_name.lower().strip()
    for c in contacts:
        if spoken_lower in [a.lower() for a in c.aliases]:
            return c
        if spoken_lower == c.name.lower() or spoken_lower == c.relationship.lower():
            return c
        if spoken_lower.isdigit() and int(spoken_lower) == c.slot:
            return c
    return None


# ── Caregiver Alerts ───────────────────────────────────────────────────

def create_alert(alert: Alert) -> str:
    db = get_db()
    alert.created_at = _now()
    if db is None:
        if not alert.id:
            alert.id = f"alert_{str(uuid.uuid4())[:8]}"
        if alert.elder_id not in _IN_MEMORY_ALERTS:
            _IN_MEMORY_ALERTS[alert.elder_id] = []
        _IN_MEMORY_ALERTS[alert.elder_id].insert(0, alert)
        return alert.id
    doc_ref = (
        db.collection("elders")
        .document(alert.elder_id)
        .collection("alerts")
        .document()
    )
    alert.id = doc_ref.id
    doc_ref.set(alert.model_dump(exclude_none=True))
    return doc_ref.id


def get_alerts(elder_id: str, unread_only: bool = False, limit: int = 50) -> list[Alert]:
    db = get_db()
    if db is None:
        alerts = _IN_MEMORY_ALERTS.get(elder_id, [])
        if unread_only:
            alerts = [a for a in alerts if not a.is_read]
        return alerts[:limit]
    query = (
        db.collection("elders")
        .document(elder_id)
        .collection("alerts")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    if unread_only:
        query = query.where(filter=FieldFilter("is_read", "==", False))
    return [Alert(**doc.to_dict() | {"id": doc.id}) for doc in query.get()]


def mark_alert_read(elder_id: str, alert_id: str) -> None:
    db = get_db()
    if db is None:
        for a in _IN_MEMORY_ALERTS.get(elder_id, []):
            if a.id == alert_id:
                a.is_read = True
        return
    (
        db
        .collection("elders")
        .document(elder_id)
        .collection("alerts")
        .document(alert_id)
        .update({"is_read": True})
    )


# ── Helper: Send alert to caregiver dashboard ─────────────────────────

def send_caregiver_alert(
    elder_id: str,
    alert_type: AlertType,
    title: str,
    message: str,
    priority: AlertPriority = AlertPriority.MEDIUM,
    medication_name: Optional[str] = None,
) -> str:
    alert = Alert(
        elder_id=elder_id,
        type=alert_type,
        priority=priority,
        title=title,
        message=message,
        medication_name=medication_name,
    )
    return create_alert(alert)


# ── Firebase Auth: Create caregiver user ───────────────────────────────

def create_caregiver_user(email: str, password: str) -> str:
    if get_db() is None:
        return "demo_caregiver_uid"
    user = fb_auth.create_user(email=email, password=password)
    return user.uid


def verify_id_token(id_token: str) -> dict:
    if get_db() is None:
        return {"uid": "demo_caregiver_uid", "email": "demo@caregiver.com"}
    return fb_auth.verify_id_token(id_token)
