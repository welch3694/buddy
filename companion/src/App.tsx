import { Route, Routes } from "react-router-dom";
import { CompanionShell } from "./components/CompanionShell";
import { useCompanionBridge } from "./hooks/useCompanionBridge";
import { ObsCaptionsPage } from "./pages/ObsCaptionsPage";
import { ObsSpeakingPage } from "./pages/ObsSpeakingPage";

function CompanionHud() {
  const bridge = useCompanionBridge();

  return (
    <CompanionShell
      connection={bridge.connection}
      turnState={bridge.turnState}
      reason={bridge.reason}
      persona={bridge.persona}
      captionText={bridge.captionText}
      speakingPlayback={bridge.speakingPlayback}
      pulseState={bridge.pulseState}
      toolCalls={bridge.toolCalls}
      onExpireToolCall={bridge.expireToolCall}
      mock={bridge.mock}
    />
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<CompanionHud />} />
      <Route path="/obs/speaking" element={<ObsSpeakingPage />} />
      <Route path="/obs/captions" element={<ObsCaptionsPage />} />
    </Routes>
  );
}
