import { useState, useEffect, useRef } from "react";
import {
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  CheckCircle,
  Clock,
  Volume2,
  HelpCircle,
  ArrowLeft,
} from "lucide-react";
import { db, doc, onSnapshot } from "../firebase";
import {
  getElderDoseCheck,
  elderVoiceChat,
  listSpeedDials,
} from "../services/api";
import { WebRTCVoiceCall } from "../services/webrtc";

export default function ElderView({ elder, onSwitchToCaregiver }) {
  const [doseData, setDoseData] = useState(null);
  const [speedDials, setSpeedDials] = useState([]);
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [assistantMessage, setAssistantMessage] = useState("");
  const [elderTranscript, setElderTranscript] = useState("");

  // WebRTC in-app voice call state
  const [isCallingCaregiver, setIsCallingCaregiver] = useState(false);
  const [callStatus, setCallStatus] = useState(null);
  const webrtcCallRef = useRef(null);
  const remoteAudioRef = useRef(null);

  // Audio recording refs
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  useEffect(() => {
    if (!elder?.id) return;
    loadDoses();
    loadSpeedDials();

    // Listen for WebRTC call state changes on Firestore
    const callDocRef = doc(db, "calls", elder.id);
    const unsub = onSnapshot(callDocRef, (snap) => {
      const data = snap.data();
      if (data) {
        if (data.status === "connected") {
          setCallStatus("Connected with Caregiver!");
        } else if (data.status === "ended") {
          endWebRTCCall(false);
        }
      }
    });

    return () => unsub();
  }, [elder?.id]);

  async function loadDoses() {
    try {
      const data = await getElderDoseCheck(elder.id);
      setDoseData(data);
    } catch (e) {
      console.error("Failed loading doses:", e);
    }
  }

  async function loadSpeedDials() {
    try {
      const list = await listSpeedDials(elder.id);
      setSpeedDials(list);
    } catch (e) {
      console.error("Failed loading speed dials:", e);
    }
  }

  // ── Browser Voice Simulator (Mic -> Sarvam STT -> Gemini -> Sarvam TTS) ──

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunksRef.current = [];
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/wav" });
        stream.getTracks().forEach((track) => track.stop());
        await processVoiceAudio(audioBlob);
      };

      mediaRecorder.start();
      setIsRecording(true);
      setAssistantMessage("Listening to you... Speak now.");
      setElderTranscript("");
    } catch (err) {
      alert("Microphone access denied: " + err.message);
    }
  }

  function stopRecording() {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      setIsProcessing(true);
      setAssistantMessage("Understanding your voice...");
    }
  }

  async function processVoiceAudio(blob) {
    try {
      const res = await elderVoiceChat(elder.id, blob);
      setElderTranscript(res.transcript || "No speech detected");
      setAssistantMessage(res.reply_text || "Thank you!");

      // Play Sarvam TTS reply audio
      if (res.reply_audio_base64) {
        const audio = new Audio("data:audio/wav;base64," + res.reply_audio_base64);
        audio.play().catch(() => {});
      }

      // Reload dose statuses
      loadDoses();
    } catch (err) {
      setAssistantMessage("Sorry, I could not hear you. Please try again.");
    } finally {
      setIsProcessing(false);
    }
  }

  // Quick double-dose check voice question
  async function askDoseCheck() {
    setIsProcessing(true);
    setAssistantMessage("Checking today's medicines...");
    try {
      const today = new Date().toISOString().split("T")[0];
      const data = await getElderDoseCheck(elder.id);
      const taken = data.medications.filter((m) => m.status === "taken");

      let reply = "";
      if (taken.length > 0) {
        const names = taken.map((m) => m.name).join(", ");
        reply = `Yes ${elder.honorific}, you already took ${names} today. You do not need to take it again!`;
      } else {
        reply = `No ${elder.honorific}, you have not taken any medicines yet today.`;
      }

      setAssistantMessage(reply);
      // Play speech using Web Speech API fallback or text display
      if ("speechSynthesis" in window) {
        const u = new SpeechSynthesisUtterance(reply);
        window.speechSynthesis.speak(u);
      }
    } catch (e) {
      setAssistantMessage("Could not check medications right now.");
    } finally {
      setIsProcessing(false);
    }
  }

  // ── In-App WebRTC Internet Voice Call to Caregiver ──

  async function startWebRTCCall() {
    try {
      setIsCallingCaregiver(true);
      setCallStatus("Calling caregiver's app over internet (₹0)...");

      const call = new WebRTCVoiceCall(
        elder.id,
        (remoteStream) => {
          if (remoteAudioRef.current) {
            remoteAudioRef.current.srcObject = remoteStream;
            remoteAudioRef.current.play();
          }
        },
        () => endWebRTCCall(false)
      );

      webrtcCallRef.current = call;
      await call.startCall(elder.name);
    } catch (err) {
      alert("Call error: " + err.message);
      setIsCallingCaregiver(false);
    }
  }

  function endWebRTCCall(updateFirestore = true) {
    if (webrtcCallRef.current) {
      webrtcCallRef.current.endCall(updateFirestore);
      webrtcCallRef.current = null;
    }
    setIsCallingCaregiver(false);
    setCallStatus(null);
  }

  return (
    <div className="min-h-screen bg-amber-50/50 text-slate-900 pb-20 font-sans flex flex-col justify-between">
      {/* Hidden audio element for WebRTC sound */}
      <audio ref={remoteAudioRef} autoPlay />

      {/* ── TOP HEADER ── */}
      <header className="bg-white border-b-2 border-teal-800/10 px-6 py-4 flex items-center justify-between shadow-xs">
        <div>
          <h1 className="text-2xl font-black text-teal-900 tracking-tight">
            Namaskara {elder?.honorific || "Amma"}!
          </h1>
          <p className="text-sm font-semibold text-teal-700">
            ArogyaVoice Health Companion
          </p>
        </div>

        <button
          onClick={onSwitchToCaregiver}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold rounded-xl"
        >
          <ArrowLeft className="w-4 h-4" />
          Caregiver View
        </button>
      </header>

      {/* ── MAIN ELDER INTERACTION AREA ── */}
      <main className="max-w-xl mx-auto w-full px-6 py-6 space-y-8 flex-1 flex flex-col justify-center">
        {/* ASSISTANT SPEECH / RESPONSE BUBBLE */}
        <div className="bg-white rounded-3xl p-6 border-2 border-teal-800/20 shadow-md text-center space-y-3">
          <div className="w-12 h-12 bg-teal-100 text-teal-800 rounded-full flex items-center justify-center mx-auto shadow-inner">
            <Volume2 className="w-6 h-6 animate-pulse" />
          </div>

          <p className="text-xl md:text-2xl font-bold text-slate-900 leading-relaxed">
            {assistantMessage ||
              `Press the big button below and speak in your language.`}
          </p>

          {elderTranscript && (
            <p className="text-sm font-medium text-slate-500 italic">
              You said: "{elderTranscript}"
            </p>
          )}
        </div>

        {/* ── GIANT SPEAK BUTTON (BROWSER VOICE SIMULATOR) ── */}
        <div className="flex flex-col items-center justify-center space-y-3">
          <button
            onClick={isRecording ? stopRecording : startRecording}
            disabled={isProcessing}
            className={`w-36 h-36 rounded-full flex flex-col items-center justify-center text-white font-extrabold shadow-xl transition-all transform active:scale-95 ${
              isRecording
                ? "bg-red-600 animate-ping ring-8 ring-red-200"
                : isProcessing
                ? "bg-slate-400 cursor-not-allowed"
                : "bg-teal-700 hover:bg-teal-800 ring-8 ring-teal-100 hover:ring-teal-200"
            }`}
          >
            {isRecording ? (
              <>
                <MicOff className="w-12 h-12 mb-1" />
                <span className="text-xs uppercase tracking-wider">Tap to Stop</span>
              </>
            ) : (
              <>
                <Mic className="w-12 h-12 mb-1" />
                <span className="text-xs uppercase tracking-wider">
                  {isProcessing ? "Thinking..." : "Tap to Speak"}
                </span>
              </>
            )}
          </button>
          <p className="text-xs font-bold text-slate-500 uppercase tracking-widest text-center">
            {isRecording
              ? "Listening... Tap to finish"
              : "Speak in Kannada, Hindi, Tamil, Telugu or English"}
          </p>
        </div>

        {/* ── QUICK ACTION: DID I TAKE MY MEDICINES? ── */}
        <button
          onClick={askDoseCheck}
          className="w-full bg-white hover:bg-teal-50 border-2 border-teal-700/30 text-teal-900 font-bold py-3.5 px-4 rounded-2xl flex items-center justify-center gap-2 shadow-xs text-base transition-colors"
        >
          <HelpCircle className="w-5 h-5 text-teal-700" />
          Did I take my tablets today? (Double-Dose Check)
        </button>

        {/* ── TODAY'S DOSES LIST ── */}
        {doseData && doseData.medications && (
          <div className="space-y-3">
            <h3 className="text-sm font-extrabold uppercase tracking-wider text-slate-600 px-1">
              Today's Medicines
            </h3>

            <div className="space-y-2">
              {doseData.medications.map((m) => {
                const isTaken = m.status === "taken";
                return (
                  <div
                    key={m.id}
                    className={`p-4 rounded-2xl border-2 flex items-center justify-between transition-all ${
                      isTaken
                        ? "bg-emerald-50 border-emerald-300 text-emerald-950"
                        : "bg-white border-slate-200 text-slate-900"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      {isTaken ? (
                        <CheckCircle className="w-7 h-7 text-emerald-600 shrink-0" />
                      ) : (
                        <Clock className="w-7 h-7 text-amber-500 shrink-0" />
                      )}
                      <div>
                        <p className="font-bold text-base">{m.name}</p>
                        <p className="text-xs font-semibold text-slate-500">
                          Scheduled: {m.time}
                        </p>
                      </div>
                    </div>

                    <span
                      className={`text-xs font-black uppercase px-3 py-1 rounded-full ${
                        isTaken
                          ? "bg-emerald-200 text-emerald-800"
                          : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {isTaken ? "Taken" : "Scheduled"}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── SPEED DIAL (CALL FAMILY) ── */}
        <div className="space-y-3 pt-2">
          <h3 className="text-sm font-extrabold uppercase tracking-wider text-slate-600 px-1">
            Call Family (Speed Dial)
          </h3>

          <div className="grid grid-cols-2 gap-3">
            {/* Free In-App Internet Call to Caregiver */}
            <button
              onClick={isCallingCaregiver ? endWebRTCCall : startWebRTCCall}
              className={`p-4 rounded-2xl border-2 font-bold flex flex-col items-center justify-center gap-2 shadow-xs text-sm transition-all ${
                isCallingCaregiver
                  ? "bg-red-600 text-white border-red-700 animate-pulse"
                  : "bg-white hover:bg-teal-50 border-teal-700/30 text-teal-900"
              }`}
            >
              {isCallingCaregiver ? (
                <>
                  <PhoneOff className="w-7 h-7" />
                  <span>End Internet Call</span>
                </>
              ) : (
                <>
                  <Phone className="w-7 h-7 text-teal-700" />
                  <span>Call {elder?.caregiver_name || "Son"} (Free Internet)</span>
                </>
              )}
            </button>

            {/* GSM Normal Phone Call fallback */}
            {speedDials.length > 0 && (
              <a
                href={`tel:${speedDials[0].phone}`}
                className="p-4 rounded-2xl border-2 bg-white hover:bg-slate-50 border-slate-300 text-slate-900 font-bold flex flex-col items-center justify-center gap-2 shadow-xs text-sm"
              >
                <Phone className="w-7 h-7 text-slate-700" />
                <span>Call {speedDials[0].name} (Normal GSM)</span>
              </a>
            )}
          </div>

          {callStatus && (
            <p className="text-xs font-bold text-center text-teal-800 animate-pulse">
              {callStatus}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
