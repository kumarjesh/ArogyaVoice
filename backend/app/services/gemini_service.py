"""
Google Gemini service for:
  1. Intent extraction from elder's speech (regional/code-mixed)
  2. Natural regional reply generation
  3. Pill-Strip Photo Scanning (Vision) + duplicate generic detection
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from google import genai
from google.genai.types import Part

from app.models.schemas import IntentResult, PillStripScanResult

logger = logging.getLogger("gemini")

_client: Optional[genai.Client] = None
PRIMARY_MODEL: str = "gemini-3.5-flash-lite"
FALLBACK_MODEL: str = "gemini-3.8-flash"


def init_gemini() -> None:
    global _client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")
    _client = genai.Client(api_key=api_key)


def _get_client() -> genai.Client:
    if _client is None:
        init_gemini()
    return _client


def _generate_with_fallback(contents, system_instruction=None, temperature=0.3):
    """Try PRIMARY_MODEL first; if 404/503/rate-limited, fallback to FALLBACK_MODEL."""
    client = _get_client()
    config = {"temperature": temperature}
    if system_instruction:
        config["system_instruction"] = system_instruction

    try:
        return client.models.generate_content(
            model=PRIMARY_MODEL,
            contents=contents,
            config=config,
        )
    except Exception as primary_err:
        logger.warning(f"Primary model {PRIMARY_MODEL} failed: {primary_err}. Trying fallback {FALLBACK_MODEL}")
        return client.models.generate_content(
            model=FALLBACK_MODEL,
            contents=contents,
            config=config,
        )


# ── Intent Extraction ──────────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = """You are a medication reminder assistant for elderly people in India.
You MUST extract the intent from the elder's speech and respond ONLY with valid JSON.

The elder may speak in Kannada, Hindi, Telugu, Tamil, English, or code-mixed (Hinglish, Kanglish, etc.).

CRITICAL RULE: For medical confirmation (TAKEN), only mark TAKEN if the elder CLEARLY and CONFIDENTLY says they took the medicine. If the elder says they have NOT taken the medicine (e.g. "nahi liya", "thogondilla", "teesukoledhu", "not taken", "innoo illa"), you MUST classify it as NOT_TAKEN, NEVER as TAKEN. If there is ANY ambiguity, mark as UNCLEAR.

Possible intents:
- TAKEN: Elder confirms they took the medicine ("haan le liya", "thogonde", "yes taken", "aagide", "veskunnanu")
- NOT_TAKEN: Elder says they did NOT take medicine yet ("thogondilla", "teesukoledhu", "nahi liya", "have not taken", "illa")
- SNOOZE: Elder wants a callback later ("10 mins baad call karo", "naantara call maadi", "baad mein")
- DOUBLE_DOSE_CHECK: Elder asks if they already took medicine ("kya maine dawai li?", "naanu tablet thogondidna?", "nenu tablet teesukunnana?")
- CALL_PERSON: Elder wants to call someone ("connect me to my son", "maga ge call maadi", "beta ko call karo")
- WELLNESS_REPORT: Elder reports symptoms ("dizziness aara hai", "thalai valikkiratu", "tale novu ide")
- GREETING: Just a greeting ("namaskara", "hello", "namasthe")
- UNCLEAR: Cannot determine intent, background noise, or ambiguous response
- OTHER: Unrelated conversation

Respond with this exact JSON format:
{
  "intent": "TAKEN|NOT_TAKEN|SNOOZE|DOUBLE_DOSE_CHECK|CALL_PERSON|WELLNESS_REPORT|GREETING|UNCLEAR|OTHER",
  "confidence": 0.0 to 1.0,
  "snooze_minutes": 15,
  "target_person": "son/daughter/doctor/name or null",
  "wellness_summary": "brief symptom summary in English or null",
  "reply_text": "warm natural reply in the SAME language the elder spoke"
}"""


FALLBACK_UNCLEAR_TEXT = {
    "kn-IN": "Kshamisee, nanage arthavagillla. Howdu annri, athava 1 dabbisi.",
    "te-IN": "Kshaminchandi, naaku artham kaledhu. Avunu cheppandi, leda 1 noppandi.",
    "hi-IN": "Maaf kijiye, mujhe samajh nahi aaya. Haan boliye, ya 1 dabayiye.",
    "ta-IN": "Mannikkanum, enakku puriyavillai. Aamam sollunnga, illanal 1 pottunnga.",
    "en-IN": "Sorry, I could not understand. Please say yes if you took the medicine, or press 1.",
}


async def extract_intent(
    transcript: str,
    language_code: str = "kn-IN",
    medication_context: str = "",
) -> IntentResult:
    """
    Given an elder's transcribed speech, extract intent and generate a reply.
    medication_context: e.g. "two green tablets after breakfast"
    """
    user_prompt = f"""Elder's language: {language_code}
Current medication context: {medication_context or 'general conversation'}
Elder said: "{transcript}"

Extract the intent and generate a warm, respectful reply in the elder's language."""

    try:
        resp = _generate_with_fallback(
            contents=user_prompt,
            system_instruction=INTENT_SYSTEM_PROMPT,
            temperature=0.3,
        )
        import json
        text = resp.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
        if data.get("snooze_minutes") is None:
            data["snooze_minutes"] = 15
        return IntentResult(**data)
    except Exception as exc:
        logger.error(f"extract_intent failed for transcript '{transcript}': {exc}", exc_info=True)
        fallback_msg = FALLBACK_UNCLEAR_TEXT.get(language_code, FALLBACK_UNCLEAR_TEXT["en-IN"])
        return IntentResult(
            intent="UNCLEAR",
            confidence=0.0,
            reply_text=fallback_msg,
        )


# ── Natural Reply Generation ──────────────────────────────────────────

async def generate_reply(
    prompt: str,
    language_code: str = "kn-IN",
    system_context: str = "You are a warm, caring medication reminder assistant for elderly people in India. Reply briefly in the specified language.",
) -> str:
    """Generate a natural-language reply for the elder."""
    try:
        resp = _generate_with_fallback(
            contents=f"Language: {language_code}\n{prompt}",
            system_instruction=system_context,
            temperature=0.5,
        )
        return resp.text.strip()
    except Exception as exc:
        logger.error(f"generate_reply failed: {exc}")
        return prompt  # Fallback to prompt itself


# ── Pill-Strip Photo Scanner (Vision) ──────────────────────────────────

VISION_PROMPT = """Analyze this medicine strip / prescription photo carefully.

Extract the following for EACH medicine visible:
1. Medicine name (as printed on the strip/label)
2. Tablet color (if visible)
3. Tablet shape (round, oval, capsule, etc.)
4. Dosage information (if visible, e.g. "500mg", "10mg")

Respond with a JSON array:
[
  {
    "medicine_name": "...",
    "color": "...",
    "shape": "...",
    "dosage": "..."
  }
]

If you cannot clearly read the medicine name or details, include what you can see and set unclear fields to null."""


async def scan_pill_strip(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    existing_medications: list[str] | None = None,
) -> list[PillStripScanResult]:
    """
    Use Gemini Vision to scan a pill strip photo.
    Returns extracted medicine info + flags potential duplicate generics.
    """
    image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)

    try:
        resp = _generate_with_fallback(
            contents=[VISION_PROMPT, image_part],
            temperature=0.2,
        )
        import json
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        items = json.loads(text)
        results = []
        for item in items:
            scan = PillStripScanResult(
                medicine_name=item.get("medicine_name", "Unknown"),
                color=item.get("color"),
                shape=item.get("shape"),
                dosage=item.get("dosage"),
            )
            # Check for potential duplicate generics
            if existing_medications:
                scan.possible_duplicates = _find_duplicate_generics(
                    scan.medicine_name, existing_medications
                )
            results.append(scan)
        return results
    except Exception as exc:
        logger.error(f"scan_pill_strip failed: {exc}")
        return []


def _find_duplicate_generics(new_name: str, existing: list[str]) -> list[str]:
    """Simple heuristic: check if any existing medicine shares the same base compound."""
    # Common generic ↔ brand mappings for Indian medicines
    GENERIC_GROUPS = {
        "paracetamol": ["crocin", "dolo", "calpol", "paracetamol", "acetaminophen"],
        "metformin": ["glycomet", "metformin", "glucophage", "obimet"],
        "amlodipine": ["amlong", "amlodipine", "norvasc", "amlopress"],
        "atorvastatin": ["atorva", "atorvastatin", "lipitor", "tonact"],
        "omeprazole": ["omez", "omeprazole", "prilosec"],
        "pantoprazole": ["pan", "pantop", "pantocid", "pantoprazole"],
        "telmisartan": ["telma", "telmisartan", "micardis", "telvas"],
    }
    new_lower = new_name.lower().strip()
    duplicates = []
    for group_name, variants in GENERIC_GROUPS.items():
        if any(v in new_lower for v in variants):
            for existing_med in existing:
                existing_lower = existing_med.lower().strip()
                if existing_lower != new_lower and any(v in existing_lower for v in variants):
                    duplicates.append(existing_med)
    return duplicates
