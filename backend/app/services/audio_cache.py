"""
Audio caching utility.
Generates TTS audio via Sarvam, saves to disk, and serves via a public URL.
Twilio <Play> needs a reachable URL for audio files.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

AUDIO_CACHE_DIR = Path(__file__).resolve().parents[2] / "audio_cache"


def init_audio_cache() -> None:
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)


def get_audio_path(text: str, language_code: str) -> Path:
    """Return the filesystem path for a cached TTS audio file."""
    key = hashlib.md5(f"{text}:{language_code}".encode()).hexdigest()
    return AUDIO_CACHE_DIR / f"{key}.wav"


def cache_audio(text: str, language_code: str, wav_bytes: bytes) -> Path:
    """Save TTS audio to cache and return its path."""
    init_audio_cache()
    path = get_audio_path(text, language_code)
    path.write_bytes(wav_bytes)
    return path


def get_audio_url(text: str, language_code: str, base_url: str) -> str:
    """Return the public URL for a cached audio file."""
    key = hashlib.md5(f"{text}:{language_code}".encode()).hexdigest()
    return f"{base_url}/audio/{key}.wav"


async def ensure_audio(text: str, language_code: str, base_url: str) -> str:
    """Generate TTS if not cached, return public URL."""
    from app.services.sarvam_service import text_to_speech

    path = get_audio_path(text, language_code)
    if not path.exists():
        wav_bytes = await text_to_speech(text, language_code)
        cache_audio(text, language_code, wav_bytes)
    return get_audio_url(text, language_code, base_url)
