"use client";

import { useA2UIBridge } from "../hooks/useA2UIBridge";
import { EDAFindingsCard } from "./surfaces/EDAFindingsCard";
import { EDAActionCard } from "./surfaces/EDAActionCard";
import { TrainingVerdictCard } from "./surfaces/TrainingVerdictCard";

interface Props {
  backendUrl: string;
}

export function A2UIPanel({ backendUrl }: Props) {
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
