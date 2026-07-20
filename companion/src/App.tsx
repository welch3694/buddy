import { CompanionShell } from "./components/CompanionShell";
import { useCompanionBridge } from "./hooks/useCompanionBridge";

export default function App() {
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
