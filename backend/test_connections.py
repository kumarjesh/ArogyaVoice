"""
Phase 1 - Connection Test Script
Verifies all 4 core APIs before writing application logic:
  1. Twilio (Account & Trial Number)
  2. Sarvam AI (TTS bulbul:v4-flash + STT saaras:v4)
  3. Google Gemini API (Text generation)
  4. Firebase Admin SDK (Firestore write + read)
"""

import base64
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def test_twilio() -> bool:
    print("\n--- 1. Testing Twilio ---")
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    phone = os.getenv("TWILIO_PHONE_NUMBER")
    if not sid or not token or not phone:
        print("FAIL: Missing TWILIO_* variables in .env")
        return False
    try:
        from twilio.rest import Client

        client = Client(sid, token)
        account = client.api.accounts(sid).fetch()
        print(f"OK: Twilio account '{account.friendly_name}' | status={account.status} | type={account.type}")
        print(f"OK: Configured From number = {phone}")
        return True
    except Exception as exc:
        print(f"FAIL: Twilio error -> {exc}")
        return False


def test_sarvam() -> bool:
    print("\n--- 2. Testing Sarvam AI (TTS + STT) ---")
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key or "your_" in api_key:
        print("FAIL: Missing SARVAM_API_KEY in .env")
        return False

    headers = {"api-subscription-key": api_key}
    tts_url = "https://api.sarvam.ai/text-to-speech"
    tts_payload = {
        "text": "Namaskara Amma, please take your morning tablet.",
        "target_language_code": "en-IN",
        "model": "bulbul:v4-flash",
        "pace": 0.85,
        "speech_sample_rate": 8000,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            tts_resp = client.post(tts_url, headers=headers, json=tts_payload)
            if tts_resp.status_code != 200:
                print(f"FAIL: Sarvam TTS {tts_resp.status_code} -> {tts_resp.text}")
                return False

            audios = tts_resp.json().get("audios", [])
            if not audios:
                print(f"FAIL: Sarvam TTS returned no audio -> {tts_resp.text}")
                return False

            wav_bytes = base64.b64decode(audios[0])
            print(f"OK: Sarvam TTS (bulbul:v4-flash) generated {len(wav_bytes)} bytes of WAV audio")

            # Feed generated WAV into Sarvam STT saaras:v4
            stt_url = "https://api.sarvam.ai/speech-to-text"
            files = {"file": ("test.wav", wav_bytes, "audio/wav")}
            data = {"model": "saaras:v4", "mode": "transcribe"}
            stt_resp = client.post(stt_url, headers=headers, files=files, data=data)
            if stt_resp.status_code != 200:
                print(f"FAIL: Sarvam STT {stt_resp.status_code} -> {stt_resp.text}")
                return False

            transcript = stt_resp.json().get("transcript", "")
            print(f"OK: Sarvam STT (saaras:v4) transcript -> '{transcript}'")
            return True
    except Exception as exc:
        print(f"FAIL: Sarvam error -> {exc}")
        return False


def test_gemini() -> bool:
    print("\n--- 3. Testing Google Gemini API ---")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or "your_" in api_key:
        print("FAIL: Missing GEMINI_API_KEY in .env")
        return False

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        # Try preferred models in order
        candidate_models = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
        for model_name in candidate_models:
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents="Reply with exactly one word: READY",
                )
                print(f"OK: Gemini ({model_name}) replied -> '{resp.text.strip()}'")
                return True
            except Exception as model_err:
                print(f"INFO: {model_name} unavailable ({model_err}), trying next...")
        print("FAIL: No tested Gemini model succeeded.")
        return False
    except Exception as exc:
        print(f"FAIL: Gemini error -> {exc}")
        return False


def test_firebase() -> bool:
    print("\n--- 4. Testing Firebase Firestore ---")
    sa_rel = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "./firebase-service-account.json")
    sa_path = (ROOT_DIR / sa_rel).resolve()
    if not sa_path.exists():
        print(f"FAIL: Service account file not found at {sa_path}")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            cred = credentials.Certificate(str(sa_path))
            firebase_admin.initialize_app(cred)

        db = firestore.client()
        doc_ref = db.collection("_healthcheck").document("phase1_ping")
        doc_ref.set({"status": "ok", "source": "test_connections.py"})
        snap = doc_ref.get()
        if snap.exists and snap.to_dict().get("status") == "ok":
            print("OK: Firebase Firestore write + read succeeded!")
            return True
        print("FAIL: Firebase document read back unexpected data.")
        return False
    except Exception as exc:
        print(f"FAIL: Firebase error -> {exc}")
        return False


def main() -> int:
    print("=" * 60)
    print("Voice Medication Reminder - Phase 1 API Connection Check")
    print("=" * 60)

    results = {
        "Twilio": test_twilio(),
        "Sarvam AI": test_sarvam(),
        "Gemini API": test_gemini(),
        "Firebase": test_firebase(),
    }

    print("\n" + "=" * 60)
    print("SUMMARY:")
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("=" * 60)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
