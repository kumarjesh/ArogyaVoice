const RENDER_BACKEND_URL = "https://arogyavoice-backend.onrender.com";

export const API_BASE = (() => {
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (envUrl && !envUrl.includes("localhost") && !envUrl.includes("127.0.0.1")) {
    return envUrl;
  }
  if (
    typeof window !== "undefined" &&
    window.location.hostname !== "localhost" &&
    window.location.hostname !== "127.0.0.1"
  ) {
    return RENDER_BACKEND_URL;
  }
  return envUrl || "http://localhost:8000";
})();

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, options);
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`API Error ${res.status}: ${errorText}`);
  }
  return res.json();
}

// ── Caregiver API ──────────────────────────────────────────────────

export async function listElders(caregiverUid) {
  return request(`/api/elders?caregiver_uid=${encodeURIComponent(caregiverUid)}`);
}

export async function getElder(elderId) {
  return request(`/api/elders/${elderId}`);
}

export async function createElder(profile) {
  return request("/api/elders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
}

export async function updateElder(elderId, updates) {
  return request(`/api/elders/${elderId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
}

export async function listMedications(elderId) {
  return request(`/api/elders/${elderId}/medications`);
}

export async function addMedication(elderId, medication) {
  return request(`/api/elders/${elderId}/medications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(medication),
  });
}

export async function deleteMedication(elderId, medId) {
  return request(`/api/elders/${elderId}/medications/${medId}`, {
    method: "DELETE",
  });
}

export async function getDoseLogs(elderId, date) {
  const q = date ? `?date=${date}` : "";
  return request(`/api/elders/${elderId}/dose-logs${q}`);
}

export async function listSpeedDials(elderId) {
  return request(`/api/elders/${elderId}/speed-dial`);
}

export async function setSpeedDial(elderId, contact) {
  return request(`/api/elders/${elderId}/speed-dial`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(contact),
  });
}

export async function listAlerts(elderId) {
  return request(`/api/elders/${elderId}/alerts`);
}

export async function markAlertRead(elderId, alertId) {
  return request(`/api/elders/${elderId}/alerts/${alertId}/read`, {
    method: "PATCH",
  });
}

export async function triggerCallNow(elderId, medicationId = null) {
  const q = medicationId ? `&medication_id=${medicationId}` : "";
  return request(`/api/trigger-call?elder_id=${elderId}${q}`, {
    method: "POST",
  });
}

export async function scanPillStrip(elderId, file) {
  const formData = new FormData();
  formData.append("image", file);
  return request(`/api/elders/${elderId}/pill-scan`, {
    method: "POST",
    body: formData,
  });
}

// ── Elder API ──────────────────────────────────────────────────────

export async function getElderDoseCheck(elderId) {
  return request(`/elder/${elderId}/dose-check`);
}

export async function elderTextChat(elderId, text) {
  return request(`/elder/${elderId}/text-chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export async function elderVoiceChat(elderId, audioBlob) {
  const formData = new FormData();
  const ext = audioBlob.type.includes("webm") ? "webm" : audioBlob.type.includes("ogg") ? "ogg" : "wav";
  formData.append("audio", audioBlob, `speech.${ext}`);
  return request(`/elder/${elderId}/voice-chat`, {
    method: "POST",
    body: formData,
  });
}

export async function confirmElderDose(elderId, medId = null) {
  const q = medId ? `?med_id=${medId}` : "";
  return request(`/elder/${elderId}/confirm-dose${q}`, {
    method: "POST",
  });
}

export async function snoozeElderDose(elderId, minutes = 15) {
  return request(`/elder/${elderId}/snooze-dose?minutes=${minutes}`, {
    method: "POST",
  });
}
