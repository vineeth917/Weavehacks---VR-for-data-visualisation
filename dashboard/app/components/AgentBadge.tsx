"use client";

import { agentColor } from "../hooks/useAguiStream";

interface Props {
  agent: string;
  pulse?: boolean;
}

const AGENT_LABELS: Record<string, string> = {
  router: "Router",
  eda: "EDA",
  training_monitor: "Training Monitor",
  narrator: "Narrator",
};

export function AgentBadge({ agent, pulse }: Props) {
  const color = agentColor(agent);
  const label = AGENT_LABELS[agent] ?? agent;

  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ backgroundColor: color + "22", border: `1px solid ${color}`, color }}
    >
      {pulse && (
        <span
          className="w-1.5 h-1.5 rounded-full animate-pulse"
          style={{ backgroundColor: color }}
        />
      )}
      {label}
    </span>
  );
}
