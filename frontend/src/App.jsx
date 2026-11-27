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
        if (list && list.length > 0) {
          setElder(list[0]);
          return;
        }
      } catch (e) {
        console.error("Init elder failed:", e);
      }
      // Guaranteed fallback so Elder View is never uninitialized
      setElder({
        id: "mPXwuTecKLZ9vrBTyjvY",
        name: "Savitri Devi",
        honorific: "Amma",
        phone: "+916360014779",
        language: "te-IN",
        caregiver_name: "Rajesh",
      });
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
