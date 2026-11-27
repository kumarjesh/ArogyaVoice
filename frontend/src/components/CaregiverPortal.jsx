import { useState, useEffect, useRef } from "react";
import {
  Bell,
  PhoneCall,
  Plus,
  Trash2,
  Camera,
  CheckCircle2,
  Clock,
  AlertTriangle,
  User,
  HeartPulse,
  PhoneForwarded,
  ShieldAlert,
  Volume2,
  ExternalLink,
  Pencil,
} from "lucide-react";
import {
  db,
  collection,
  query,
  where,
  orderBy,
  onSnapshot,
  doc,
} from "../firebase";
import {
  listElders,
  createElder,
  updateElder,
  listMedications,
  addMedication,
  updateMedication,
  deleteMedication,
  getDoseLogs,
  listSpeedDials,
  setSpeedDial,
  triggerCallNow,
  scanPillStrip,
  markAlertRead,
} from "../services/api";
import { WebRTCVoiceCall } from "../services/webrtc";

export default function CaregiverPortal({ caregiverUid = "demo_caregiver", onSwitchToElder }) {
  const [elders, setElders] = useState([]);
  const [selectedElder, setSelectedElder] = useState(null);
  const [activeTab, setActiveTab] = useState("timeline");
  const [loading, setLoading] = useState(true);

  // Data states
  const [medications, setMedications] = useState([]);
  const [doseLogs, setDoseLogs] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [speedDials, setSpeedDials] = useState([]);

  // Modals & form states
  const [showAddElder, setShowAddElder] = useState(false);
  const [showAddMed, setShowAddMed] = useState(false);
  const [editingMed, setEditingMed] = useState(null);
  const [customizingScannedMed, setCustomizingScannedMed] = useState(null);
  const [showAddSpeedDial, setShowAddSpeedDial] = useState(false);
  const [showAlertsDrawer, setShowAlertsDrawer] = useState(false);
  const [isTriggeringCall, setIsTriggeringCall] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState(null);

  // Vision scanner
  const [isScanning, setIsScanning] = useState(false);
  const [scanResults, setScanResults] = useState(null);

  // WebRTC Incoming Call state
  const [incomingCall, setIncomingCall] = useState(null);
  const [activeWebRTCCall, setActiveWebRTCCall] = useState(null);
  const remoteAudioRef = useRef(null);

  // Load Elders
  useEffect(() => {
    loadElders();
  }, [caregiverUid]);

  async function loadElders() {
    try {
      setLoading(true);
      const list = await listElders(caregiverUid);
      setElders(list);
      if (list.length > 0 && !selectedElder) {
        setSelectedElder(list[0]);
      } else if (list.length === 0) {
        // Auto-seed a demo elder if none exists
        createDefaultElder();
      }
    } catch (err) {
      console.error("Failed to load elders:", err);
    } finally {
      setLoading(false);
    }
  }

  async function createDefaultElder() {
    try {
      const demo = {
        name: "Savitri Devi",
        honorific: "Amma",
        phone: "+916360014779",
        caregiver_name: "Rajesh",
        caregiver_uid: caregiverUid,
        language: "kn-IN",
        accessibility_mode: "visual_color",
        missed_call_callback: false,
        evening_wellness_check: true,
      };
      const res = await createElder(demo);
      demo.id = res.id;
      setElders([demo]);
      setSelectedElder(demo);

      // Seed 2 default medications
      await addMedication(res.id, {
        medicine_name: "Glycomet 500mg",
        time: "08:00",
        meal_timing: "after_food",
        tablet_count: 1,
        color: "white",
        shape: "round",
        is_active: true,
      });
      await addMedication(res.id, {
        medicine_name: "Amlong 5mg",
        time: "20:00",
        meal_timing: "after_food",
        tablet_count: 2,
        color: "green",
        shape: "round",
        is_active: true,
      });
      // Seed speed dial
      await setSpeedDial(res.id, {
        slot: 1,
        name: "Rajesh",
        relationship: "son",
        aliases: ["son", "maga", "beta"],
        phone: "+916360014779",
      });
      loadElderData(demo);
    } catch (e) {
      console.error("Failed seeding default elder:", e);
    }
  }

  // Real-time Firestore Listeners for active elder
  useEffect(() => {
    if (!selectedElder?.id) return;
    loadElderData(selectedElder);

    // Real-time dose logs
    const todayStr = new Date().toISOString().split("T")[0];
    const doseLogsRef = collection(db, "elders", selectedElder.id, "dose_logs");
    const qLogs = query(doseLogsRef, where("date", "==", todayStr));
    const unsubLogs = onSnapshot(qLogs, (snap) => {
      const logs = snap.docs.map((d) => ({ ...d.data(), id: d.id }));
      setDoseLogs(logs);
    });

    // Real-time alerts
    const alertsRef = collection(db, "elders", selectedElder.id, "alerts");
    const qAlerts = query(alertsRef, orderBy("created_at", "desc"));
    const unsubAlerts = onSnapshot(alertsRef, (snap) => {
      const al = snap.docs.map((d) => ({ ...d.data(), id: d.id }));
      setAlerts(al);
    });

    // WebRTC call listener (room id = elder.id)
    const callDocRef = doc(db, "calls", selectedElder.id);
    const unsubCall = onSnapshot(callDocRef, (snap) => {
      const data = snap.data();
      if (data && data.status === "calling" && !activeWebRTCCall) {
        setIncomingCall(data);
      } else if (data && data.status === "ended") {
        setIncomingCall(null);
        if (activeWebRTCCall) {
          activeWebRTCCall.endCall(false);
          setActiveWebRTCCall(null);
        }
      }
    });

    return () => {
      unsubLogs();
      unsubAlerts();
      unsubCall();
    };
  }, [selectedElder?.id]);

  async function loadElderData(elder) {
    if (!elder?.id) return;
    try {
      const [meds, logs, dials] = await Promise.all([
        listMedications(elder.id),
        getDoseLogs(elder.id),
        listSpeedDials(elder.id),
      ]);
      setMedications(meds);
      setDoseLogs(logs);
      setSpeedDials(dials);
    } catch (err) {
      console.error("Error loading elder data:", err);
    }
  }

  // WebRTC actions
  async function answerWebRTCCall() {
    try {
      const call = new WebRTCVoiceCall(
        selectedElder.id,
        (remoteStream) => {
          if (remoteAudioRef.current) {
            remoteAudioRef.current.srcObject = remoteStream;
            remoteAudioRef.current.play();
          }
        },
        () => {
          setActiveWebRTCCall(null);
          setIncomingCall(null);
        }
      );
      await call.answerCall();
      setActiveWebRTCCall(call);
      setIncomingCall(null);
    } catch (e) {
      console.error("Failed to answer WebRTC call:", e);
    }
  }

  function hangupWebRTCCall() {
    if (activeWebRTCCall) {
      activeWebRTCCall.endCall(true);
      setActiveWebRTCCall(null);
    }
  }

  // Trigger call now
  async function handleTriggerCall(medId = null) {
    if (!selectedElder?.id) return;
    try {
      setIsTriggeringCall(true);
      setTriggerStatus("Calling elder's phone...");
      const res = await triggerCallNow(selectedElder.id, medId);
      setTriggerStatus(`Call placed successfully! Call SID: ${res.call_sid?.slice(0, 8)}...`);
      setTimeout(() => setTriggerStatus(null), 6000);
    } catch (err) {
      setTriggerStatus(`Call failed: ${err.message}`);
    } finally {
      setIsTriggeringCall(false);
    }
  }

  // Pill strip photo scan
  async function handlePillScan(file) {
    if (!file || !selectedElder?.id) return;
    try {
      setIsScanning(true);
      setScanResults(null);
      const results = await scanPillStrip(selectedElder.id, file);
      setScanResults(results);
    } catch (err) {
      alert("Vision scan failed: " + err.message);
    } finally {
      setIsScanning(false);
    }
  }

  // Open customize modal from scan result
  function handleStartAddScannedMed(scanned) {
    setCustomizingScannedMed({
      medicine_name: scanned.medicine_name || "",
      dosage: scanned.dosage || "",
      time: "08:00",
      meal_timing: "after_food",
      tablet_count: 1,
      color: scanned.color || "white",
      shape: scanned.shape || "round",
    });
  }

  const unreadAlerts = alerts.filter((a) => !a.is_read);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen text-teal-800">
        <div className="text-center space-y-2">
          <div className="w-8 h-8 border-4 border-teal-600 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm font-medium">Loading Caregiver Dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 pb-16">
      {/* Hidden audio element for WebRTC in-app calls */}
      <audio ref={remoteAudioRef} autoPlay />

      {/* ── TOP NAV HEADER ── */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-xs">
        <div className="max-w-6xl mx-auto px-3 sm:px-4 py-2.5 sm:py-3 flex flex-col md:flex-row md:items-center md:justify-between gap-2.5">
          {/* Top row: Brand + Mobile quick actions */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-teal-700 text-white flex items-center justify-center font-bold text-base sm:text-lg shadow-xs">
                आ
              </div>
              <div>
                <h1 className="text-base sm:text-lg font-bold text-slate-900 leading-tight">
                  ArogyaVoice
                </h1>
                <p className="text-[11px] sm:text-xs text-teal-700 font-medium">
                  Caregiver Control Center
                </p>
              </div>
            </div>

            {/* Mobile Header Quick Actions (Immediately visible on phones without horizontal scroll!) */}
            <div className="flex md:hidden items-center gap-1.5">
              <button
                onClick={() => setShowAlertsDrawer(!showAlertsDrawer)}
                className="relative p-2 text-slate-600 bg-slate-100 rounded-lg hover:bg-teal-50"
                title="Alerts"
              >
                <Bell className="w-4 h-4" />
                {unreadAlerts.length > 0 && (
                  <span className="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center animate-pulse">
                    {unreadAlerts.length}
                  </span>
                )}
              </button>
              <button
                onClick={onSwitchToElder}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-teal-700 text-white text-xs font-bold rounded-lg shadow-xs active:scale-95"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Elder View
              </button>
            </div>
          </div>

          {/* Controls: Elder selector & Desktop actions */}
          <div className="flex items-center justify-between md:justify-end gap-2 w-full md:w-auto">
            {/* Elder Selector */}
            <div className="flex items-center gap-1.5 flex-1 md:flex-initial">
              <select
                value={selectedElder?.id || ""}
                onChange={(e) => {
                  const el = elders.find((x) => x.id === e.target.value);
                  if (el) setSelectedElder(el);
                }}
                className="w-full md:w-auto bg-slate-100 border border-slate-300 text-slate-800 text-xs sm:text-sm rounded-lg px-2.5 py-1.5 font-medium truncate"
              >
                {elders.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.honorific} {e.name} ({e.language})
                  </option>
                ))}
              </select>

              <button
                onClick={() => setShowAddElder(true)}
                title="Add Elder"
                className="p-1.5 sm:p-2 text-slate-600 hover:text-teal-700 bg-slate-100 rounded-lg hover:bg-teal-50 shrink-0"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>

            {/* Desktop-only action buttons */}
            <div className="hidden md:flex items-center gap-2">
              <button
                onClick={() => setShowAlertsDrawer(!showAlertsDrawer)}
                className="relative p-2 text-slate-600 hover:text-teal-700 bg-slate-100 rounded-lg hover:bg-teal-50"
              >
                <Bell className="w-4 h-4" />
                {unreadAlerts.length > 0 && (
                  <span className="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center animate-pulse">
                    {unreadAlerts.length}
                  </span>
                )}
              </button>

              <button
                onClick={onSwitchToElder}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-teal-50 text-teal-800 border border-teal-200 text-xs font-semibold rounded-lg hover:bg-teal-100"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Elder View
              </button>
            </div>
          </div>
        </div>

        {/* Demo Call Bar */}
        <div className="bg-teal-900 text-white px-4 py-2 text-xs flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="bg-teal-700 text-white px-2 py-0.5 rounded font-mono text-[10px]">
              DEMO CONTROLS
            </span>
            <span>
              Target: <strong>{selectedElder?.phone}</strong> (Twilio Trial Call)
            </span>
          </div>

          <div className="flex items-center gap-2">
            {triggerStatus && (
              <span className="text-amber-200 font-medium">{triggerStatus}</span>
            )}
            <button
              onClick={() => handleTriggerCall()}
              disabled={isTriggeringCall}
              className="bg-amber-400 hover:bg-amber-300 text-slate-950 font-bold px-3 py-1 rounded-md text-xs flex items-center gap-1.5 transition-all disabled:opacity-50"
            >
              <PhoneCall className="w-3.5 h-3.5" />
              {isTriggeringCall ? "Placing Call..." : "Trigger Medicine Call Now"}
            </button>
          </div>
        </div>
      </header>

      {/* ── INCOMING WEBRTC CALL BANNER ── */}
      {incomingCall && (
        <div className="max-w-6xl mx-auto px-4 mt-4">
          <div className="bg-amber-500 text-slate-950 p-4 rounded-xl flex items-center justify-between shadow-lg animate-bounce">
            <div className="flex items-center gap-3">
              <PhoneForwarded className="w-6 h-6 animate-pulse" />
              <div>
                <p className="font-bold text-sm">
                  Incoming Voice Call from {selectedElder?.honorific} {selectedElder?.name}!
                </p>
                <p className="text-xs">Free in-app internet voice call (WebRTC)</p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={answerWebRTCCall}
                className="bg-green-700 text-white font-bold px-4 py-1.5 rounded-lg text-xs"
              >
                Answer
              </button>
              <button
                onClick={() => {
                  const callDocRef = doc(db, "calls", selectedElder.id);
                  callDocRef.set({ status: "ended" }, { merge: true });
                  setIncomingCall(null);
                }}
                className="bg-red-700 text-white font-bold px-3 py-1.5 rounded-lg text-xs"
              >
                Decline
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Active WebRTC Call Banner */}
      {activeWebRTCCall && (
        <div className="max-w-6xl mx-auto px-4 mt-4">
          <div className="bg-emerald-600 text-white p-3 rounded-xl flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Volume2 className="w-5 h-5 animate-pulse" />
              Connected in-app internet call with {selectedElder?.name}
            </div>
            <button
              onClick={hangupWebRTCCall}
              className="bg-red-600 hover:bg-red-700 text-white font-bold px-3 py-1 rounded-md text-xs"
            >
              End Call
            </button>
          </div>
        </div>
      )}

      {/* ── TAB NAV ── */}
      <div className="max-w-6xl mx-auto px-3 sm:px-4 mt-3 sm:mt-4">
        <div className="flex border-b border-slate-200 gap-3 sm:gap-6 text-xs sm:text-sm font-medium overflow-x-auto scrollbar-none whitespace-nowrap pb-0.5">
          {[
            { id: "timeline", label: "Live Daily Timeline" },
            { id: "schedule", label: "Medication Schedule" },
            { id: "scanner", label: "Pill-Strip Vision Scanner" },
            { id: "speeddial", label: "Speed-Dial Directory" },
            { id: "settings", label: "Elder Profile & Settings" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`pb-2.5 sm:pb-3 px-1 sm:px-0 font-semibold transition-all shrink-0 relative ${
                activeTab === tab.id
                  ? "text-teal-700 border-b-2 border-teal-700"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              {tab.label}
              {tab.id === "timeline" && doseLogs.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] bg-slate-200 text-slate-700">
                  {doseLogs.length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      <main className="max-w-6xl mx-auto px-4 mt-6">
        {/* 1. DAILY TIMELINE */}
        {activeTab === "timeline" && (
          <div className="space-y-6">
            <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-xs">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h2 className="text-base font-bold text-slate-900">
                    Today's Medication Status
                  </h2>
                  <p className="text-xs text-slate-500">
                    Real-time dose confirmations from phone calls, SMS & Elder View app
                  </p>
                </div>
                <div className="text-xs font-semibold px-3 py-1 bg-slate-100 text-slate-600 rounded-full">
                  {new Date().toLocaleDateString("en-IN", {
                    weekday: "long",
                    month: "short",
                    day: "numeric",
                  })}
                </div>
              </div>

              {doseLogs.length === 0 ? (
                <div className="py-12 text-center text-slate-400">
                  <Clock className="w-10 h-10 mx-auto mb-2 opacity-50" />
                  <p className="text-sm font-medium">No dose calls triggered yet today.</p>
                  <p className="text-xs mt-1">
                    Click <strong>"Trigger Medicine Call Now"</strong> above to test!
                  </p>
                </div>
              ) : (
                <div className="divide-y divide-slate-100">
                  {doseLogs.map((log) => {
                    const isTaken = log.status === "taken";
                    const isSnoozed = log.status === "snoozed";
                    const isMissed = log.status === "missed";

                    return (
                      <div key={log.id} className="py-4 flex flex-col md:flex-row md:items-center justify-between gap-3">
                        <div className="flex items-start gap-3">
                          <div
                            className={`p-2 rounded-xl mt-1 ${
                              isTaken
                                ? "bg-emerald-100 text-emerald-700"
                                : isSnoozed
                                ? "bg-amber-100 text-amber-700"
                                : isMissed
                                ? "bg-red-100 text-red-700"
                                : "bg-slate-100 text-slate-500"
                            }`}
                          >
                            {isTaken ? (
                              <CheckCircle2 className="w-5 h-5" />
                            ) : isMissed ? (
                              <AlertTriangle className="w-5 h-5" />
                            ) : (
                              <Clock className="w-5 h-5" />
                            )}
                          </div>

                          <div>
                            <div className="flex items-center gap-2">
                              <h3 className="font-bold text-sm text-slate-900">
                                {log.medication_name}
                              </h3>
                              <span className="text-xs text-slate-400 font-mono">
                                {log.scheduled_time}
                              </span>
                              <span
                                className={`text-[11px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                                  isTaken
                                    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                                    : isSnoozed
                                    ? "bg-amber-50 text-amber-700 border border-amber-200"
                                    : isMissed
                                    ? "bg-red-50 text-red-700 border border-red-200"
                                    : "bg-slate-100 text-slate-600"
                                }`}
                              >
                                {log.status}
                              </span>
                            </div>

                            {log.elder_response_text && (
                              <p className="text-xs text-slate-600 mt-1 italic">
                                Elder said: "{log.elder_response_text}"
                              </p>
                            )}

                            {log.wellness_summary && (
                              <div className="mt-1 flex items-center gap-1.5 text-xs text-purple-700 font-medium">
                                <HeartPulse className="w-3.5 h-3.5" />
                                Wellness symptom: {log.wellness_summary}
                              </div>
                            )}
                          </div>
                        </div>

                        <div className="text-right text-xs text-slate-400">
                          {log.taken_at ? (
                            <p className="text-emerald-700 font-semibold">
                              Taken at{" "}
                              {new Date(log.taken_at).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </p>
                          ) : (
                            <p>Call attempts: {log.call_attempts || 1}</p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 2. MEDICATION SCHEDULE MANAGER */}
        {activeTab === "schedule" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-slate-900">
                  Scheduled Medications
                </h2>
                <p className="text-xs text-slate-500">
                  Descriptions adapt automatically for {selectedElder?.accessibility_mode === "tactile_pillbox" ? "Tactile Pillbox" : "Visual Color"} mode
                </p>
              </div>
              <button
                onClick={() => setShowAddMed(true)}
                className="bg-teal-700 hover:bg-teal-800 text-white font-semibold text-xs px-3 py-2 rounded-lg flex items-center gap-1.5"
              >
                <Plus className="w-4 h-4" /> Add Dose
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {medications.map((med) => (
                <div
                  key={med.id}
                  className="bg-white rounded-xl p-4 border border-slate-200 shadow-xs flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold px-2 py-0.5 bg-teal-50 text-teal-800 rounded">
                        {med.time}
                      </span>
                      <span className="text-[11px] text-slate-500 capitalize">
                        {med.meal_timing.replace("_", " ")}
                      </span>
                    </div>

                    <h3 className="font-bold text-slate-900 mt-2 text-sm">
                      {med.medicine_name}
                    </h3>

                    <div className="mt-2 text-xs text-slate-600 space-y-0.5">
                      <p>
                        <strong>Count:</strong> {med.tablet_count} tablet(s)
                      </p>
                      {med.color && <p><strong>Color:</strong> {med.color}</p>}
                      {med.shape && <p><strong>Shape:</strong> {med.shape}</p>}
                      {med.tactile_slot && (
                        <p className="text-amber-700 font-medium">
                          <strong>Box Slot:</strong> {med.tactile_slot}
                        </p>
                      )}
                      {med.tonic_name && (
                        <p className="text-purple-700 font-medium">
                          <strong>Tonic:</strong> {med.tonic_name} ({med.tonic_dose || "1 spoon"})
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between">
                    <button
                      onClick={() => handleTriggerCall(med.id)}
                      className="text-xs text-teal-700 hover:text-teal-900 font-semibold flex items-center gap-1"
                      title="Trigger Call Now"
                    >
                      <PhoneCall className="w-3 h-3" /> Call dose
                    </button>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setEditingMed(med)}
                        className="text-xs text-slate-700 hover:text-teal-700 font-semibold flex items-center gap-1 px-2 py-1 rounded bg-slate-100 hover:bg-teal-50"
                        title="Edit Medication"
                      >
                        <Pencil className="w-3 h-3" /> Edit
                      </button>

                      <button
                        onClick={() => deleteMedication(selectedElder.id, med.id).then(() => loadElderData(selectedElder))}
                        className="text-slate-400 hover:text-red-600 p-1"
                        title="Delete Medication"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 3. PILL-STRIP VISION SCANNER */}
        {activeTab === "scanner" && (
          <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-xs space-y-6">
            <div>
              <h2 className="text-base font-bold text-slate-900">
                Pill-Strip & Prescription Scanner (Gemini Vision)
              </h2>
              <p className="text-xs text-slate-500">
                Upload or snap a photo of a medicine strip. Gemini extracts name, color, shape, dosage, and checks for generic duplicates in {selectedElder?.name}'s current schedule.
              </p>
            </div>

            <div className="border-2 border-dashed border-slate-300 rounded-xl p-8 text-center hover:border-teal-600 transition-colors bg-slate-50">
              <Camera className="w-10 h-10 mx-auto text-teal-700 mb-2" />
              <label className="cursor-pointer">
                <span className="text-sm font-bold text-teal-700 hover:underline">
                  Click to choose a photo
                </span>
                <span className="text-xs text-slate-500 block mt-1">
                  Supports JPG, PNG, WEBP of medicine strips or boxes
                </span>
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.[0]) handlePillScan(e.target.files[0]);
                  }}
                />
              </label>
            </div>

            {isScanning && (
              <div className="py-6 text-center text-teal-800">
                <div className="w-6 h-6 border-2 border-teal-700 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                <p className="text-xs font-semibold">Gemini Vision is analyzing medicine strip...</p>
              </div>
            )}

            {scanResults && (
              <div className="space-y-4">
                <h3 className="text-sm font-bold text-slate-900">
                  Extracted Medicines ({scanResults.length})
                </h3>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {scanResults.map((item, idx) => (
                    <div
                      key={idx}
                      className="border border-slate-200 rounded-xl p-4 bg-slate-50 space-y-2"
                    >
                      <div className="flex items-start justify-between">
                        <h4 className="font-bold text-sm text-slate-900">
                          {item.medicine_name}
                        </h4>
                        {item.dosage && (
                          <span className="text-xs font-mono bg-teal-100 text-teal-800 px-2 py-0.5 rounded">
                            {item.dosage}
                          </span>
                        )}
                      </div>

                      <div className="text-xs text-slate-600 flex gap-4">
                        <span><strong>Color:</strong> {item.color || "Not specified"}</span>
                        <span><strong>Shape:</strong> {item.shape || "Not specified"}</span>
                      </div>

                      {/* Duplicate generic warning */}
                      {item.possible_duplicates && item.possible_duplicates.length > 0 && (
                        <div className="bg-red-50 border border-red-200 text-red-800 p-2 rounded-lg text-xs flex items-center gap-2">
                          <ShieldAlert className="w-4 h-4 shrink-0" />
                          <span>
                            <strong>Warning:</strong> Potential duplicate generic found in schedule:{" "}
                            {item.possible_duplicates.join(", ")}!
                          </span>
                        </div>
                      )}

                      <button
                        onClick={() => handleStartAddScannedMed(item)}
                        className="w-full mt-2 bg-teal-700 hover:bg-teal-800 text-white font-semibold text-xs py-2 rounded-lg flex items-center justify-center gap-1.5 transition-all shadow-xs"
                      >
                        <Plus className="w-3.5 h-3.5" /> Customize & Add to Schedule
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* 4. SPEED DIAL DIRECTORY */}
        {activeTab === "speeddial" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-slate-900">
                  Speed-Dial Directory (Keypad & Voice Bridging)
                </h2>
                <p className="text-xs text-slate-500">
                  Maps slots (e.g. 1) & relationships ("son", "maga", "doctor") for live call bridging
                </p>
              </div>
              <button
                onClick={() => setShowAddSpeedDial(true)}
                className="bg-teal-700 hover:bg-teal-800 text-white font-semibold text-xs px-3 py-2 rounded-lg flex items-center gap-1.5"
              >
                <Plus className="w-4 h-4" /> Add Contact
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {speedDials.map((dial) => (
                <div
                  key={dial.slot}
                  className="bg-white rounded-xl p-4 border border-slate-200 shadow-xs flex items-center justify-between"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-teal-100 text-teal-800 flex items-center justify-center font-bold text-base">
                      {dial.slot}
                    </div>
                    <div>
                      <h4 className="font-bold text-sm text-slate-900">
                        {dial.name} ({dial.relationship})
                      </h4>
                      <p className="text-xs text-slate-500 font-mono">
                        {dial.phone}
                      </p>
                      {dial.aliases && dial.aliases.length > 0 && (
                        <p className="text-[10px] text-teal-700 mt-0.5">
                          Aliases: {dial.aliases.join(", ")}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 5. ELDER PROFILE & SETTINGS */}
        {activeTab === "settings" && selectedElder && (
          <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-xs max-w-xl space-y-4">
            <h2 className="text-base font-bold text-slate-900">
              Elder Profile & Accessibility Settings
            </h2>

            <div className="space-y-3 text-sm">
              <div>
                <label className="text-xs font-semibold text-slate-600 block mb-1">
                  Elder Name
                </label>
                <input
                  type="text"
                  value={selectedElder.name}
                  onChange={(e) => setSelectedElder({ ...selectedElder, name: e.target.value })}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-semibold text-slate-600 block mb-1">
                    Honorific
                  </label>
                  <input
                    type="text"
                    value={selectedElder.honorific}
                    onChange={(e) => setSelectedElder({ ...selectedElder, honorific: e.target.value })}
                    className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-600 block mb-1">
                    Phone (+91...)
                  </label>
                  <input
                    type="text"
                    value={selectedElder.phone}
                    onChange={(e) => setSelectedElder({ ...selectedElder, phone: e.target.value })}
                    className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-slate-600 block mb-1">
                  Caregiver Name (Used in Scam-Safe Greeting)
                </label>
                <input
                  type="text"
                  value={selectedElder.caregiver_name}
                  onChange={(e) => setSelectedElder({ ...selectedElder, caregiver_name: e.target.value })}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-slate-600 block mb-1">
                  Spoken Language
                </label>
                <select
                  value={selectedElder.language}
                  onChange={(e) => setSelectedElder({ ...selectedElder, language: e.target.value })}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
                >
                  <option value="kn-IN">Kannada (ಕನ್ನಡ)</option>
                  <option value="hi-IN">Hindi (हिन्दी)</option>
                  <option value="te-IN">Telugu (తెలుగు)</option>
                  <option value="ta-IN">Tamil (தமிழ்)</option>
                  <option value="en-IN">Indian English</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-semibold text-slate-600 block mb-1">
                  Accessibility Mode
                </label>
                <select
                  value={selectedElder.accessibility_mode}
                  onChange={(e) => setSelectedElder({ ...selectedElder, accessibility_mode: e.target.value })}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
                >
                  <option value="visual_color">Visual Color Mode (Color & Shape)</option>
                  <option value="tactile_pillbox">Tactile Pillbox Mode (For Visually Impaired)</option>
                </select>
              </div>

              <div className="pt-2 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedElder.missed_call_callback}
                    onChange={(e) => setSelectedElder({ ...selectedElder, missed_call_callback: e.target.checked })}
                    className="rounded text-teal-700"
                  />
                  <span className="text-xs font-medium text-slate-700">
                    Enable "Missed-Call Callback Mode" (for elders with ₹0 SIM balance)
                  </span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedElder.evening_wellness_check}
                    onChange={(e) => setSelectedElder({ ...selectedElder, evening_wellness_check: e.target.checked })}
                    className="rounded text-teal-700"
                  />
                  <span className="text-xs font-medium text-slate-700">
                    Enable 10-second Evening Wellness Check
                  </span>
                </label>
              </div>

              <button
                onClick={() => {
                  updateElder(selectedElder.id, selectedElder);
                  alert("Settings saved!");
                }}
                className="w-full bg-teal-700 hover:bg-teal-800 text-white font-bold py-2 rounded-lg text-sm mt-4"
              >
                Save Settings
              </button>
            </div>
          </div>
        )}
      </main>

      {/* ── ALERTS DRAWER ── */}
      {showAlertsDrawer && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs z-40 flex justify-end">
          <div className="w-full max-w-md bg-white h-full shadow-2xl p-6 overflow-y-auto">
            <div className="flex items-center justify-between pb-4 border-b border-slate-200">
              <h3 className="font-bold text-base text-slate-900 flex items-center gap-2">
                <Bell className="w-5 h-5 text-teal-700" />
                Caregiver Alerts ({alerts.length})
              </h3>
              <button
                onClick={() => setShowAlertsDrawer(false)}
                className="text-slate-400 hover:text-slate-700 text-sm font-bold"
              >
                ✕
              </button>
            </div>

            <div className="divide-y divide-slate-100 mt-4">
              {alerts.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-8">No alerts yet.</p>
              ) : (
                alerts.map((alert) => (
                  <div key={alert.id} className="py-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-xs text-slate-900">
                        {alert.title}
                      </span>
                      <span className="text-[10px] text-slate-400">
                        {alert.created_at ? new Date(alert.created_at).toLocaleTimeString() : ""}
                      </span>
                    </div>
                    <p className="text-xs text-slate-600">{alert.message}</p>
                    {!alert.is_read && (
                      <button
                        onClick={() => markAlertRead(selectedElder.id, alert.id)}
                        className="text-[11px] text-teal-700 font-semibold hover:underline"
                      >
                        Mark as read
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── ADD MEDICATION MODAL ── */}
      {showAddMed && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs z-40 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl space-y-4">
            <h3 className="font-bold text-base text-slate-900">Add Medication Dose</h3>

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const formData = new FormData(e.target);
                const newMed = {
                  medicine_name: formData.get("name"),
                  time: formData.get("time"),
                  meal_timing: formData.get("meal_timing"),
                  tablet_count: parseInt(formData.get("tablet_count") || "1"),
                  color: formData.get("color"),
                  shape: formData.get("shape"),
                  tactile_slot: formData.get("tactile_slot"),
                  tactile_description: formData.get("tactile_description"),
                  tonic_name: formData.get("tonic_name"),
                  is_active: true,
                };
                await addMedication(selectedElder.id, newMed);
                setShowAddMed(false);
                loadElderData(selectedElder);
              }}
              className="space-y-3 text-xs"
            >
              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  Medicine Name (Caregiver reference)
                </label>
                <input
                  name="name"
                  required
                  placeholder="e.g. Glycomet 500mg"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">
                    Scheduled Time (24h)
                  </label>
                  <input
                    name="time"
                    type="time"
                    required
                    defaultValue="08:00"
                    className="w-full border border-slate-300 rounded-lg p-2 font-mono"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">
                    Meal Timing
                  </label>
                  <select name="meal_timing" className="w-full border border-slate-300 rounded-lg p-2">
                    <option value="after_food">After Food</option>
                    <option value="before_food">Before Food</option>
                    <option value="with_food">With Food</option>
                    <option value="anytime">Anytime</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Tablet Count</label>
                  <input
                    name="tablet_count"
                    type="number"
                    defaultValue="1"
                    min="1"
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Color</label>
                  <input
                    name="color"
                    placeholder="green, white..."
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Shape</label>
                  <input
                    name="shape"
                    placeholder="round, oval..."
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
              </div>

              <div className="border-t border-slate-100 pt-2 space-y-2">
                <p className="text-[11px] text-slate-500 font-medium">
                  Tactile / Liquid Options (Optional)
                </p>
                <input
                  name="tactile_slot"
                  placeholder="Tactile slot (e.g. first box on the left)"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
                <input
                  name="tonic_name"
                  placeholder="Liquid / Tonic name (if any)"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  type="submit"
                  className="flex-1 bg-teal-700 text-white font-bold py-2 rounded-lg"
                >
                  Save Dose
                </button>
                <button
                  type="button"
                  onClick={() => setShowAddMed(false)}
                  className="px-4 bg-slate-100 text-slate-700 font-bold py-2 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── CUSTOMIZE & ADD SCANNED MEDICATION MODAL ── */}
      {customizingScannedMed && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs z-40 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
              <div>
                <h3 className="font-bold text-base text-slate-900">Schedule Scanned Medication</h3>
                <p className="text-xs text-slate-500">Customize dose count, timings, color & food timing</p>
              </div>
              <button
                onClick={() => setCustomizingScannedMed(null)}
                className="text-slate-400 hover:text-slate-700 font-bold"
              >
                ✕
              </button>
            </div>

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const formData = new FormData(e.target);
                const newMed = {
                  medicine_name: formData.get("name"),
                  time: formData.get("time"),
                  meal_timing: formData.get("meal_timing"),
                  tablet_count: parseInt(formData.get("tablet_count") || "1"),
                  color: formData.get("color") || "white",
                  shape: formData.get("shape") || "round",
                  tactile_slot: formData.get("tactile_slot") || "",
                  tonic_name: formData.get("tonic_name") || "",
                  is_active: true,
                };
                await addMedication(selectedElder.id, newMed);
                setCustomizingScannedMed(null);
                loadElderData(selectedElder);
                alert(`Added "${newMed.medicine_name}" to schedule!`);
              }}
              className="space-y-3 text-xs"
            >
              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  Medicine Name & Dosage
                </label>
                <input
                  name="name"
                  required
                  defaultValue={customizingScannedMed.medicine_name}
                  className="w-full border border-slate-300 rounded-lg p-2 font-medium text-slate-900"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">
                    Scheduled Time (24h)
                  </label>
                  <input
                    name="time"
                    type="time"
                    required
                    defaultValue={customizingScannedMed.time || "08:00"}
                    className="w-full border border-slate-300 rounded-lg p-2 font-mono"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">
                    Meal Timing
                  </label>
                  <select
                    name="meal_timing"
                    defaultValue={customizingScannedMed.meal_timing || "after_food"}
                    className="w-full border border-slate-300 rounded-lg p-2 font-medium"
                  >
                    <option value="after_food">After Food (oota maadida mele)</option>
                    <option value="before_food">Before Food (oota munche)</option>
                    <option value="with_food">With Food (oota jothege)</option>
                    <option value="anytime">Anytime</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Tablet Count</label>
                  <input
                    name="tablet_count"
                    type="number"
                    min="1"
                    max="10"
                    defaultValue={customizingScannedMed.tablet_count || 1}
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Color</label>
                  <input
                    name="color"
                    defaultValue={customizingScannedMed.color || "white"}
                    placeholder="white, green, yellow..."
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Shape</label>
                  <input
                    name="shape"
                    defaultValue={customizingScannedMed.shape || "round"}
                    placeholder="round, oval, capsule..."
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
              </div>

              <div className="border-t border-slate-100 pt-2 space-y-2">
                <p className="text-[11px] text-slate-500 font-medium">
                  Additional Details (Optional)
                </p>
                <input
                  name="tactile_slot"
                  defaultValue={customizingScannedMed.tactile_slot || ""}
                  placeholder="Tactile slot (e.g. morning box slot 1)"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
                <input
                  name="tonic_name"
                  defaultValue={customizingScannedMed.tonic_name || ""}
                  placeholder="Liquid / Tonic name (if any)"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  type="submit"
                  className="flex-1 bg-teal-700 hover:bg-teal-800 text-white font-bold py-2 rounded-lg transition-all"
                >
                  Save to Schedule
                </button>
                <button
                  type="button"
                  onClick={() => setCustomizingScannedMed(null)}
                  className="px-4 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold py-2 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── EDIT SCHEDULED MEDICATION MODAL ── */}
      {editingMed && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs z-40 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
              <div>
                <h3 className="font-bold text-base text-slate-900">Edit Scheduled Medication</h3>
                <p className="text-xs text-slate-500">Update dose timings, counts, colors & instructions</p>
              </div>
              <button
                onClick={() => setEditingMed(null)}
                className="text-slate-400 hover:text-slate-700 font-bold"
              >
                ✕
              </button>
            </div>

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const formData = new FormData(e.target);
                const updates = {
                  medicine_name: formData.get("name"),
                  time: formData.get("time"),
                  meal_timing: formData.get("meal_timing"),
                  tablet_count: parseInt(formData.get("tablet_count") || "1"),
                  color: formData.get("color") || "white",
                  shape: formData.get("shape") || "round",
                  tactile_slot: formData.get("tactile_slot") || "",
                  tonic_name: formData.get("tonic_name") || "",
                };
                await updateMedication(selectedElder.id, editingMed.id, updates);
                setEditingMed(null);
                loadElderData(selectedElder);
                alert(`Updated "${updates.medicine_name}" successfully!`);
              }}
              className="space-y-3 text-xs"
            >
              <div>
                <label className="font-semibold text-slate-700 block mb-1">
                  Medicine Name
                </label>
                <input
                  name="name"
                  required
                  defaultValue={editingMed.medicine_name}
                  className="w-full border border-slate-300 rounded-lg p-2 font-medium text-slate-900"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">
                    Scheduled Time (24h)
                  </label>
                  <input
                    name="time"
                    type="time"
                    required
                    defaultValue={editingMed.time}
                    className="w-full border border-slate-300 rounded-lg p-2 font-mono"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">
                    Meal Timing
                  </label>
                  <select
                    name="meal_timing"
                    defaultValue={editingMed.meal_timing}
                    className="w-full border border-slate-300 rounded-lg p-2 font-medium"
                  >
                    <option value="after_food">After Food (oota maadida mele)</option>
                    <option value="before_food">Before Food (oota munche)</option>
                    <option value="with_food">With Food (oota jothege)</option>
                    <option value="anytime">Anytime</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Tablet Count</label>
                  <input
                    name="tablet_count"
                    type="number"
                    min="1"
                    max="10"
                    defaultValue={editingMed.tablet_count || 1}
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Color</label>
                  <input
                    name="color"
                    defaultValue={editingMed.color || ""}
                    placeholder="white, green, yellow..."
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="font-semibold text-slate-700 block mb-1">Shape</label>
                  <input
                    name="shape"
                    defaultValue={editingMed.shape || ""}
                    placeholder="round, oval, capsule..."
                    className="w-full border border-slate-300 rounded-lg p-2"
                  />
                </div>
              </div>

              <div className="border-t border-slate-100 pt-2 space-y-2">
                <p className="text-[11px] text-slate-500 font-medium">
                  Additional Details (Optional)
                </p>
                <input
                  name="tactile_slot"
                  defaultValue={editingMed.tactile_slot || ""}
                  placeholder="Tactile slot (e.g. slot 1)"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
                <input
                  name="tonic_name"
                  defaultValue={editingMed.tonic_name || ""}
                  placeholder="Tonic name / spoon dose (if any)"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  type="submit"
                  className="flex-1 bg-teal-700 hover:bg-teal-800 text-white font-bold py-2 rounded-lg transition-all"
                >
                  Update Medication
                </button>
                <button
                  type="button"
                  onClick={() => setEditingMed(null)}
                  className="px-4 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold py-2 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── ADD SPEED DIAL MODAL ── */}
      {showAddSpeedDial && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs z-40 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-sm w-full p-6 shadow-xl space-y-4">
            <h3 className="font-bold text-base text-slate-900">Add Speed-Dial Contact</h3>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const formData = new FormData(e.target);
                const aliases = (formData.get("aliases") || "")
                  .split(",")
                  .map((s) => s.trim().toLowerCase())
                  .filter(Boolean);

                await setSpeedDial(selectedElder.id, {
                  slot: parseInt(formData.get("slot")),
                  name: formData.get("name"),
                  relationship: formData.get("relationship"),
                  aliases,
                  phone: formData.get("phone"),
                });
                setShowAddSpeedDial(false);
                loadElderData(selectedElder);
              }}
              className="space-y-3 text-xs"
            >
              <div>
                <label className="font-semibold block mb-1">Slot Number (1-9)</label>
                <input
                  name="slot"
                  type="number"
                  min="1"
                  max="9"
                  required
                  defaultValue={speedDials.length + 1}
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>
              <div>
                <label className="font-semibold block mb-1">Contact Name</label>
                <input
                  name="name"
                  required
                  placeholder="Rahul"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>
              <div>
                <label className="font-semibold block mb-1">Relationship</label>
                <input
                  name="relationship"
                  required
                  placeholder="son / daughter / doctor"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>
              <div>
                <label className="font-semibold block mb-1">Aliases (comma-separated)</label>
                <input
                  name="aliases"
                  placeholder="son, maga, beta"
                  className="w-full border border-slate-300 rounded-lg p-2"
                />
              </div>
              <div>
                <label className="font-semibold block mb-1">Phone Number (+91...)</label>
                <input
                  name="phone"
                  required
                  placeholder="+91..."
                  className="w-full border border-slate-300 rounded-lg p-2 font-mono"
                />
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  type="submit"
                  className="flex-1 bg-teal-700 text-white font-bold py-2 rounded-lg"
                >
                  Save Contact
                </button>
                <button
                  type="button"
                  onClick={() => setShowAddSpeedDial(false)}
                  className="px-4 bg-slate-100 font-bold py-2 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
