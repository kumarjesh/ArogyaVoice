"""
Telephony service — clean abstraction over Twilio Programmable Voice.
Designed so Exotel can be swapped in later for Indian production.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Dial, Gather, VoiceResponse

logger = logging.getLogger("telephony")

_client: Optional[TwilioClient] = None
_from_number: Optional[str] = None


def init_telephony() -> None:
    global _client, _from_number
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    _from_number = os.getenv("TWILIO_PHONE_NUMBER") or "+15555555555"
    if not sid or not token or "xxxx" in sid:
        logger.warning("Missing or placeholder TWILIO_* variables. Outbound telephony calls will be simulated (zero-key mode).")
        _client = None
        return
    try:
        _client = TwilioClient(sid, token)
    except Exception as e:
        logger.warning(f"Failed to initialize Twilio client: {e}. Outbound calls will be simulated.")
        _client = None


def _get_client() -> Optional[TwilioClient]:
    global _client
    if _client is None and os.getenv("TWILIO_ACCOUNT_SID"):
        init_telephony()
    return _client


def get_from_number() -> str:
    global _from_number
    if _from_number is None:
        _from_number = os.getenv("TWILIO_PHONE_NUMBER") or "+15555555555"
    return _from_number


# ── Outbound Calls ─────────────────────────────────────────────────────

def make_call(
    to_number: str,
    webhook_url: str,
    status_callback_url: Optional[str] = None,
) -> str:
    """
    Place an outbound call to the elder.
    webhook_url: URL that returns TwiML when the call connects.
    Returns: Twilio Call SID.
    Note: Twilio Trial accounts disallow status_callback parameters.
    """
    client = _get_client()
    if client is None:
        logger.info(f"[SIMULATED CALL] Outbound call to {to_number} (Zero-key evaluation mode). Webhook: {webhook_url}")
        return "CA_SIMULATED_ZERO_KEY"

    kwargs = {
        "to": to_number,
        "from_": get_from_number(),
        "url": webhook_url,
    }
    # Only include status_callback if explicitly supported (paid accounts)
    # Trial accounts reject status_callback with HTTP 400
    is_trial = os.getenv("TWILIO_TRIAL_ACCOUNT", "true").lower() == "true"
    if status_callback_url and not is_trial:
        kwargs["status_callback"] = status_callback_url
        kwargs["status_callback_method"] = "POST"
        kwargs["status_callback_event"] = ["completed", "no-answer", "busy", "failed"]

    call = client.calls.create(**kwargs)
    return call.sid


def hangup_call(call_sid: str) -> None:
    """Forcefully end a call."""
    client = _get_client()
    client.calls(call_sid).update(status="completed")


# ── TwiML Builders ─────────────────────────────────────────────────────

def build_play_and_gather(
    audio_url: str,
    action_url: str,
    dtmf_timeout: int = 5,
    speech_timeout: str = "auto",
    num_digits: int = 1,
    language: str = "kn-IN",
) -> str:
    """
    Build TwiML that plays audio and gathers DTMF + speech input.
    4-5 second silence timeout for elderly users.
    language: BCP-47 language code for Twilio speech recognition (e.g. kn-IN, te-IN, hi-IN).
    """
    response = VoiceResponse()
    gather = Gather(
        action=action_url,
        method="POST",
        input="dtmf speech",
        timeout=dtmf_timeout,
        speech_timeout=speech_timeout,
        num_digits=num_digits,
        language=language,
    )
    gather.play(audio_url)
    response.append(gather)
    # If no input, redirect to action with empty input
    response.redirect(action_url, method="POST")
    return str(response)


def build_play_only(audio_url: str) -> str:
    """Build TwiML that plays audio and hangs up."""
    response = VoiceResponse()
    response.play(audio_url)
    return str(response)


def build_gather_after_play(
    audio_url: str,
    action_url: str,
    timeout: int = 5,
    language: str = "kn-IN",
) -> str:
    """Play audio first, then wait for DTMF or speech."""
    response = VoiceResponse()
    response.play(audio_url)
    gather = Gather(
        action=action_url,
        method="POST",
        input="dtmf speech",
        timeout=timeout,
        speech_timeout="auto",
        num_digits=1,
        language=language,
    )
    response.append(gather)
    response.redirect(action_url, method="POST")
    return str(response)


def build_dial_bridge(target_number: str, caller_id: Optional[str] = None) -> str:
    """Build TwiML to bridge-connect the elder to a family member's phone."""
    response = VoiceResponse()
    dial = Dial(caller_id=caller_id or get_from_number())
    dial.number(target_number)
    response.append(dial)
    return str(response)


def build_reject() -> str:
    """Build TwiML to reject an incoming call (for missed-call callback mode)."""
    response = VoiceResponse()
    response.reject()
    return str(response)


def build_record(
    action_url: str,
    max_length: int = 10,
    play_beep: bool = False,
) -> str:
    """Record the elder's voice for STT processing."""
    response = VoiceResponse()
    response.record(
        action=action_url,
        method="POST",
        max_length=max_length,
        play_beep=play_beep,
        trim="trim-silence",
        recording_status_callback=action_url,
        recording_status_callback_method="POST",
    )
    return str(response)
