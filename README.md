# 🎙️ ArogyaVoice (ಆರೋಗ್ಯ ಧ್ವನಿ)

> **Voice-First Medication Reminder, Double-Dose Prevention & Family Speed-Dial Calling System for Elders in India**

---

## 🌟 Overview

Most elders (60+) and visually or motor-impaired individuals in India struggle with small smartphone screens, complex app navigation, and mobile data dependency. Millions still rely on basic feature/keypad phones.

---

## 🚀 One-Command Quickstart (Docker — Zero Secrets Required)

You can launch the complete application with a single command:

```bash
docker compose up --build
```

- **App URL**: Open **`http://localhost:3000`** in your browser.
- **Main Port**: `3000` opens the ArogyaVoice Web App (Caregiver Dashboard & Elder View).
- **Backend API**: Accessible at `http://localhost:8000`.
- **Zero Secrets / Zero Keys Required**: The app runs with zero configuration or keys out of the box. Key-dependent external features (such as live GSM calls or live Vision scanning) operate in graceful demo mode with clear user-friendly messages.


**ArogyaVoice flips the model:**
- **The Caregiver (Son/Daughter)** manages medication schedules, pill strips, and profiles through an intuitive **React + Tailwind PWA Dashboard**.
- **The Elder** uses **zero apps and zero internet** on their daily routine: they simply answer normal GSM voice calls in their native language (**Kannada, Hindi, Telugu, Tamil, or Indian English**), or long-press **Speed Dial 1** on their keypad phone to talk to the AI assistant.
- **Smart Accessibility**: Supports **Visual Color Mode** (color, shape, tonic) and **Tactile Pillbox Mode** (slot descriptions for blind elders).
- **Double-Dose Prevention**: Elders can call in and ask *"Did I take my morning medicine?"* to hear real-time verification from today's logs.
- **Speed-Dial Bridging**: Elders can say *"Call my son"* or press `1` to bridge directly to family contacts via Twilio `<Dial>`.
- **Hybrid Calling**: Works over GSM telephony (Twilio, swappable to Exotel) as well as free in-app internet WebRTC calling.

---

## 🏗️ Architecture & Tech Stack

```
                                  ┌─────────────────────────────┐
                                  │      Caregiver Portal       │
                                  │   (React + Vite + Tailwind) │
                                  └──────────────┬──────────────┘
                                                 │
                                                 ▼
┌───────────────────────┐            ┌───────────────────────┐            ┌────────────────────────┐
│     Elder Phone       │ ◄────────► │   FastAPI Backend     │ ◄────────► │     External APIs      │
│ (GSM / Feature Phone  │   Twilio   │ (Scheduler, Telephony │            │  - Sarvam AI (STT/TTS) │
│   or Elder View PWA)  │ Programmable│  Intent, Audio Cache) │            │  - Google Gemini AI    │
└───────────────────────┘    Voice   └───────────┬───────────┘            │  - Firebase Firestore │
                                                 │                        └────────────────────────┘
                                                 ▼
                                     ┌───────────────────────┐
                                     │  Firestore Database   │
                                     │ (Logs, Doses, Alerts) │
                                     └───────────────────────┘
```

- **Frontend**: React 18, Vite, Tailwind CSS v4, Lucide Icons, PWA (Service Worker + Manifest).
- **Backend**: Python 3.11+, FastAPI, APScheduler, Uvicorn.
- **Telephony**: Twilio Programmable Voice (`telephony_service.py` clean abstraction).
- **Voice AI (Regional Indian Languages)**:
  - **Speech-to-Text**: Sarvam AI (`saaras:v4` transcribe mode).
  - **Text-to-Speech**: Sarvam AI (`bulbul:v4-flash` at `pace=0.85` for calm, clear elder speech).
- **LLM & Vision Brain**: Google Gemini API (`gemini-2.0-flash` / `gemini-2.5-flash` / `gemini-1.5-flash`) for intent extraction and Pill-Strip Photo Scanning.
- **Database & Real-time Sync**: Firebase Firestore & Firebase Auth.

---

## 📁 Repository Structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app entry point & lifespan
│   │   ├── models/
│   │   │   └── schemas.py           # Pydantic data models & enums
│   │   ├── routers/
│   │   │   ├── voice.py             # Twilio Voice webhooks (outbound & inbound)
│   │   │   ├── caregiver.py         # Caregiver REST API (CRUD, pill scanner)
│   │   │   └── elder.py             # Elder View API (browser voice simulator)
│   │   └── services/
│   │       ├── firebase_service.py  # Firestore CRUD & alert dispatching
│   │       ├── sarvam_service.py    # Sarvam STT (saaras:v4) & TTS (bulbul:v4-flash)
│   │       ├── gemini_service.py    # Intent extraction & Gemini Vision scanner
│   │       ├── telephony_service.py # Twilio Voice abstraction
│   │       ├── scheduler_service.py # APScheduler (1-min dose check, snooze, retry)
│   │       └── audio_cache.py       # Audio caching for Twilio <Play>
│   ├── requirements.txt
│   └── test_connections.py          # API connection test suite
├── frontend/
│   ├── public/
│   │   ├── manifest.json            # PWA manifest (Android TWA ready)
│   │   ├── sw.js                    # Service worker
│   │   └── icon.svg
│   ├── src/
│   │   ├── components/
│   │   │   ├── CaregiverPortal.jsx  # View 1: Caregiver Dashboard
│   │   │   └── ElderView.jsx        # View 2: High-contrast Elder screen
│   │   ├── services/
│   │   │   ├── api.js               # Backend API client
│   │   │   └── webrtc.js            # In-app WebRTC internet calling
│   │   ├── App.jsx                  # Two-View routing
│   │   ├── firebase.js              # Firebase client SDK initialization
│   │   ├── main.jsx
│   │   └── index.css
│   ├── package.json
│   └── vite.config.js
├── run_dev.ps1                      # Windows one-click local stack launcher
├── .env.example                     # Environment template
└── README.md
```

---

## 🚀 Quick Start (Local Development)

### 1. Prerequisites
- Python 3.11+
- Node.js 18+
- Accounts for: Twilio, Sarvam AI, Google AI Studio (Gemini), and Firebase.

### 2. Configure Environment Variables
Copy `.env.example` to `.env` in the root folder:
```bash
cp .env.example .env
```
Fill in your credentials:
```env
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
SARVAM_API_KEY=...
GEMINI_API_KEY=...
FIREBASE_SERVICE_ACCOUNT_PATH=./firebase-service-account.json
FIREBASE_PROJECT_ID=...
PUBLIC_BASE_URL=https://your-ngrok-or-domain.ngrok-free.dev
```

Place your Firebase Service Account JSON key as `firebase-service-account.json` in the project root.

In `frontend/`, copy `.env.example` to `.env`:
```env
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=...
VITE_FIREBASE_PROJECT_ID=...
VITE_FIREBASE_STORAGE_BUCKET=...
VITE_FIREBASE_MESSAGING_SENDER_ID=...
VITE_FIREBASE_APP_ID=...
VITE_API_BASE_URL=http://localhost:8000
```

### 3. Run with One Click (Windows)
```powershell
.\run_dev.ps1
```

Or manually:

```bash
# Terminal 1 - Backend
cd backend
python -m venv .venv
# On Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Terminal 2 - Frontend
cd frontend
npm install
npm run dev

# Terminal 3 - Webhook Tunnel
ngrok http 8000
```

---

## 📱 Two-View Progressive Web App (PWA)

- **Caregiver Portal**: Access via `http://localhost:5173` or `?view=caregiver`.
- **Elder View**: Access via `http://localhost:5173/?view=elder`.
- **Android APK**: The frontend is fully PWA-compliant with `manifest.json` and service worker, ready for 1-click APK generation using [PWABuilder](https://www.pwabuilder.com/) or Bubblewrap.

---

## 🔒 Security
- All sensitive credentials (`.env`, service account keys, temporary audio recordings) are excluded via `.gitignore`.

---

## 📄 License
MIT License
