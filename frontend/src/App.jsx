import { useState, useEffect } from "react";
import CaregiverPortal from "./components/CaregiverPortal";
import ElderView from "./components/ElderView";
import { listElders, createElder } from "./services/api";

export default function App() {
  const [view, setView] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("view") === "elder" ? "elder" : "caregiver";
  });

  const [elder, setElder] = useState(null);
  const caregiverUid = "caregiver_demo_uid";

  useEffect(() => {
    // Sync view with URL
    const url = new URL(window.location);
    url.searchParams.set("view", view);
    window.history.replaceState({}, "", url);
  }, [view]);

  useEffect(() => {
    async function initElder() {
      try {
        const list = await listElders(caregiverUid);
        if (list.length > 0) {
          setElder(list[0]);
        } else {
          const newElder = {
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
          const res = await createElder(newElder);
          newElder.id = res.id;
          setElder(newElder);
        }
      } catch (e) {
        console.error("Init elder failed:", e);
      }
    }
    initElder();
  }, []);

  return (
    <div className="min-h-screen bg-slate-50 font-sans">
      {view === "caregiver" ? (
        <CaregiverPortal
          caregiverUid={caregiverUid}
          onSwitchToElder={() => setView("elder")}
        />
      ) : (
        <ElderView
          elder={elder}
          onSwitchToCaregiver={() => setView("caregiver")}
        />
      )}
    </div>
  );
}
