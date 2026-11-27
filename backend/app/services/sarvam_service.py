"""
Sarvam AI wrapper for Indian-language Speech-to-Text and Text-to-Speech.
  STT: model='saaras:v4',  mode='transcribe'
  TTS: model='bulbul:v4-flash', pace=0.85 (calm, slower for elderly hearing)
"""

from __future__ import annotations

import base64
import io
import os
from typing import Optional

import httpx

SARVAM_BASE = "https://api.sarvam.ai"
_api_key: Optional[str] = None


def init_sarvam() -> None:
    global _api_key
    _api_key = os.getenv("SARVAM_API_KEY")
    if not _api_key:
        raise RuntimeError("SARVAM_API_KEY not set in environment")


def _headers() -> dict:
    if not _api_key:
        init_sarvam()
    return {"api-subscription-key": _api_key}


# ── Language code mapping ──────────────────────────────────────────────

# Sarvam uses standard BCP-47 codes
LANGUAGE_NAMES = {
    "kn-IN": "Kannada",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
    "ta-IN": "Tamil",
    "en-IN": "English",
}


# ── Text-to-Speech ─────────────────────────────────────────────────────

async def text_to_speech(
    text: str,
    language_code: str = "kn-IN",
    pace: float = 0.85,
    sample_rate: int = 8000,
) -> bytes:
    """
    Convert text to WAV audio using Sarvam bulbul:v4-flash.
    Returns raw WAV bytes (8kHz for Twilio <Play> compatibility).
    """
    payload = {
        "text": text,
        "target_language_code": language_code,
        "model": "bulbul:v4-flash",
        "pace": pace,
        "speech_sample_rate": sample_rate,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{SARVAM_BASE}/text-to-speech",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        audios = data.get("audios", [])
        if not audios:
            raise RuntimeError(f"Sarvam TTS returned no audio: {data}")
        return base64.b64decode(audios[0])


def text_to_speech_sync(
    text: str,
    language_code: str = "kn-IN",
    pace: float = 0.85,
    sample_rate: int = 8000,
) -> bytes:
    """Synchronous version for use in non-async contexts."""
    payload = {
        "text": text,
        "target_language_code": language_code,
        "model": "bulbul:v4-flash",
        "pace": pace,
        "speech_sample_rate": sample_rate,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{SARVAM_BASE}/text-to-speech",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        audios = data.get("audios", [])
        if not audios:
            raise RuntimeError(f"Sarvam TTS returned no audio: {data}")
        return base64.b64decode(audios[0])


# ── Speech-to-Text ─────────────────────────────────────────────────────

async def speech_to_text(
    audio_bytes: bytes,
    language_code: str = "kn-IN",
    mime_type: str = "audio/wav",
) -> str:
    """
    Transcribe audio using Sarvam saaras:v4.
    Handles Kannada, Hindi, Telugu, Tamil, English, and code-mixed speech.
    Returns the transcript string.
    """
    ext = "wav" if "wav" in mime_type else "mp3" if "mp3" in mime_type else "ogg"
    files = {"file": (f"audio.{ext}", io.BytesIO(audio_bytes), mime_type)}
    data = {"model": "saaras:v4", "mode": "transcribe"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{SARVAM_BASE}/speech-to-text",
            headers=_headers(),
            files=files,
            data=data,
        )
        resp.raise_for_status()
        return resp.json().get("transcript", "")


def speech_to_text_sync(
    audio_bytes: bytes,
    language_code: str = "kn-IN",
    mime_type: str = "audio/wav",
) -> str:
    """Synchronous version for use in non-async contexts."""
    ext = "wav" if "wav" in mime_type else "mp3" if "mp3" in mime_type else "ogg"
    files = {"file": (f"audio.{ext}", io.BytesIO(audio_bytes), mime_type)}
    data = {"model": "saaras:v4", "mode": "transcribe"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{SARVAM_BASE}/speech-to-text",
            headers=_headers(),
            files=files,
            data=data,
        )
        resp.raise_for_status()
        return resp.json().get("transcript", "")
