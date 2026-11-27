"""
Firebase Admin SDK initialiser + Firestore CRUD helpers.
All dose logs, alerts, and profile operations flow through here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def init_firebase() -> None:
    """Call once at app startup."""
    global _db
    if firebase_admin._apps:
        _db = firestore.client()
        return
    sa_path = os.getenv(
        "FIREBASE_SERVICE_ACCOUNT_PATH", "firebase-service-account.json"
    )
    resolved = Path(sa_path)
    if not resolved.is_absolute():
        # Check project root first (parents[3]), then backend root (parents[2]), then cwd
        candidates = [
            Path(__file__).resolve().parents[3] / sa_path,
            Path(__file__).resolve().parents[2] / sa_path,
            Path.cwd() / sa_path,
        ]
        resolved = next((p for p in candidates if p.exists()), candidates[0])
    cred = credentials.Certificate(str(resolved))
    firebase_admin.initialize_app(cred)
    _db = firestore.client()


def get_db():
    if _db is None:
        init_firebase()
    return _db


# ── Elder Profile CRUD ─────────────────────────────────────────────────

def create_elder(profile: ElderProfile) -> str:
    db = get_db()
    profile.created_at = _now()
    profile.updated_at = _now()
    doc_ref = db.collection("elders").document()
    profile.id = doc_ref.id
    doc_ref.set(profile.model_dump(exclude_none=True))
    return doc_ref.id


def get_elder(elder_id: str) -> Optional[ElderProfile]:
    snap = get_db().collection("elders").document(elder_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    data["id"] = snap.id
    return ElderProfile(**data)


def get_elder_by_phone(phone: str) -> Optional[ElderProfile]:
    db = get_db()
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
    get_db().collection("elders").document(elder_id).update(updates)


def list_elders_for_caregiver(caregiver_uid: str) -> list[ElderProfile]:
    db = get_db()
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
    return result


# ── Medication CRUD ────────────────────────────────────────────────────

def add_medication(elder_id: str, med: Medication) -> str:
    db = get_db()
    med.elder_id = elder_id
    med.created_at = _now()
    doc_ref = db.collection("elders").document(elder_id).collection("medications").document()
    med.id = doc_ref.id
    doc_ref.set(med.model_dump(exclude_none=True))
    return doc_ref.id


def get_medications(elder_id: str, active_only: bool = True) -> list[Medication]:
    db = get_db()
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
    get_db().collection("elders").document(elder_id).collection("medications").document(med_id).update(updates)


def delete_medication(elder_id: str, med_id: str) -> None:
    update_medication(elder_id, med_id, {"is_active": False})


# ── Dose Log CRUD ──────────────────────────────────────────────────────

def create_dose_log(log: DoseLog) -> str:
    db = get_db()
    log.updated_at = _now()
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
    (
        get_db()
        .collection("elders")
        .document(elder_id)
        .collection("dose_logs")
        .document(log_id)
        .update(updates)
    )


def find_dose_log(elder_id: str, medication_id: str, date_str: str) -> Optional[DoseLog]:
    db = get_db()
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
    (
        db.collection("elders")
        .document(elder_id)
        .collection("speed_dial")
        .document(str(contact.slot))
        .set(contact.model_dump(exclude_none=True))
    )


def get_speed_dials(elder_id: str) -> list[SpeedDialContact]:
    db = get_db()
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
    (
        get_db()
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
    user = fb_auth.create_user(email=email, password=password)
    return user.uid


def verify_id_token(id_token: str) -> dict:
    return fb_auth.verify_id_token(id_token)
