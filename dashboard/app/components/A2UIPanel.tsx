"use client";

import { useCallback } from "react";
import { A2UIProvider, basicCatalog } from "@copilotkit/a2ui-renderer";
import { useA2UIBridge } from "../hooks/useA2UIBridge";
import { EDAFindingsCard } from "./surfaces/EDAFindingsCard";
import { EDAActionCard } from "./surfaces/EDAActionCard";
import { TrainingVerdictCard } from "./surfaces/TrainingVerdictCard";

interface Props {
  backendUrl: string;
}

function A2UIPanelInner({ backendUrl }: Props) {
  const { sendAction } = useA2UIBridge(backendUrl);

  return (
    <div className="flex flex-col gap-3 min-h-0 overflow-y-auto">
      <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider">
        Agent-Generated UI
      </p>
      <EDAFindingsCard />
      <EDAActionCard sendAction={sendAction} />
      <TrainingVerdictCard sendAction={sendAction} />
    </div>
  );
}

export function A2UIPanel({ backendUrl }: Props) {
  const handleAction = useCallback(
    (msg: { userAction?: { name: string; surfaceId: string; context?: Record<string, unknown> } }) => {
      if (!msg.userAction) return;
      const { name, surfaceId, context = {} } = msg.userAction;
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
    [backendUrl]
  );

  return (
    <A2UIProvider catalog={basicCatalog} onAction={handleAction}>
      <A2UIPanelInner backendUrl={backendUrl} />
    </A2UIProvider>
  );
}
