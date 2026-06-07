"use client";

import { useCallback, useState } from "react";
import { A2UIProvider, basicCatalog } from "@copilotkit/a2ui-renderer";
import { useA2UIBridge } from "../hooks/useA2UIBridge";
import { EDAFindingsCard } from "./surfaces/EDAFindingsCard";
import { EDAActionCard } from "./surfaces/EDAActionCard";
import { TrainingVerdictCard } from "./surfaces/TrainingVerdictCard";

interface Props {
  backendUrl: string;
  onStopTraining?: () => void;
}

function A2UIPanelInner({ backendUrl }: { backendUrl: string }) {
  useA2UIBridge(backendUrl);

  return (
    <div className="flex flex-col gap-2 min-h-0 overflow-y-auto h-full">
      <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider flex-shrink-0">
        Agent-Generated UI
      </p>
      <EDAFindingsCard />
      <EDAActionCard sendAction={undefined} />
      <TrainingVerdictCard sendAction={undefined} />
    </div>
  );
}

export function A2UIPanel({ backendUrl, onStopTraining }: Props) {
  const [lastAction, setLastAction] = useState<string | null>(null);

  const handleAction = useCallback(
    (msg: { userAction?: { name: string; surfaceId: string; context?: Record<string, unknown> } }) => {
      if (!msg.userAction) return;
      const { name, surfaceId, context = {} } = msg.userAction;

      setLastAction(name);
      setTimeout(() => setLastAction(null), 3000);

      // Wire stop_training to pause the chart immediately
      if (name === "stop_training") {
        onStopTraining?.();
      }

      fetch(`${backendUrl}/agui/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: "demo-session-1",
          userAction: { name, surfaceId, context, timestamp: new Date().toISOString() },
        }),
      })
        .then((r) => r.json())
        .then((r) => console.log("[a2ui button]", name, r))
        .catch((e) => console.warn("[a2ui button] failed", e));
    },
    [backendUrl, onStopTraining]
  );

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-700 p-3 flex flex-col min-h-0 h-full">
      <A2UIProvider catalog={basicCatalog} onAction={handleAction}>
        {lastAction && (
          <div className="mb-2 px-2 py-1 rounded bg-indigo-500/20 border border-indigo-500/40 text-xs text-indigo-300 flex-shrink-0">
            ✓ Sent: <span className="font-mono font-semibold">{lastAction}</span>
          </div>
        )}
        {/* a2ui-dark class applies global CSS overrides for button theming */}
        <div className="a2ui-dark flex-1 min-h-0 overflow-y-auto">
          <A2UIPanelInner backendUrl={backendUrl} />
        </div>
      </A2UIProvider>
    </div>
  );
}
