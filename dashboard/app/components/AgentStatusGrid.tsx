"use client";

import { AguiEvent, agentColor } from "../hooks/useAguiStream";

interface Props {
  events: AguiEvent[];
}

const AGENTS = ["router", "eda", "training_monitor", "narrator"];
const AGENT_LABELS: Record<string, string> = {
  router: "Router",
  eda: "EDA Agent",
  training_monitor: "Training Monitor",
  narrator: "Narrator",
};
const AGENT_DESC: Record<string, string> = {
  router: "Routes queries to specialists",
  eda: "Profiles data, generates panels",
  training_monitor: "Watches train/val curves",
  narrator: "Speaks the final report",
};

function deriveAgentState(agent: string, events: AguiEvent[]) {
  const relevant = events.filter(
    (e) => e.agent === agent || (e.type === "HANDOFF" && e.to === agent)
  );
  if (relevant.length === 0) return "idle";
  const last = relevant[relevant.length - 1];
  if (last.type === "RUN_FINISHED") return "done";
  if (last.type === "HANDOFF" && last.to === agent) return "active";
  if (last.type === "TOOL_CALL_START") return "working";
  if (last.type === "TOOL_CALL_END") return "active";
  if (last.type === "TEXT_MESSAGE_CONTENT") return "speaking";
  return "active";
}

function deriveLastAction(agent: string, events: AguiEvent[]): string {
  const relevant = events.filter((e) => e.agent === agent);
  if (relevant.length === 0) return "—";
  const last = relevant[relevant.length - 1];
  if (last.tool) return `${last.type === "TOOL_CALL_START" ? "calling" : "done"} ${last.tool}`;
  if (last.message) return last.message.slice(0, 50);
  return last.type;
}

const STATE_STYLES: Record<string, string> = {
  idle: "bg-gray-800 border-gray-700 opacity-50",
  active: "bg-gray-800 border-gray-600",
  working: "bg-gray-800 border-yellow-500/60 shadow-yellow-500/20 shadow-lg",
  speaking: "bg-gray-800 border-pink-500/60 shadow-pink-500/20 shadow-lg",
  done: "bg-gray-800 border-green-500/60",
};

const STATE_LABEL: Record<string, string> = {
  idle: "idle",
  active: "active",
  working: "working...",
  speaking: "speaking",
  done: "done",
};

export function AgentStatusGrid({ events }: Props) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {AGENTS.map((agent) => {
        const state = deriveAgentState(agent, events);
        const lastAction = deriveLastAction(agent, events);
        const color = agentColor(agent);

        return (
          <div
            key={agent}
            className={`rounded-xl border p-3 transition-all duration-500 ${STATE_STYLES[state]}`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-semibold" style={{ color }}>
                {AGENT_LABELS[agent]}
              </span>
              <span
                className="text-xs px-1.5 py-0.5 rounded-full"
                style={{
                  backgroundColor: color + "22",
                  color,
                  border: `1px solid ${color}44`,
                }}
              >
                {STATE_LABEL[state]}
              </span>
            </div>
            <p className="text-gray-500 text-xs mb-2">{AGENT_DESC[agent]}</p>
            <p className="text-gray-400 text-xs truncate">{lastAction}</p>

            {(state === "working" || state === "speaking") && (
              <div className="mt-2 h-0.5 rounded-full overflow-hidden bg-gray-700">
                <div
                  className="h-full rounded-full animate-pulse"
                  style={{ backgroundColor: color, width: "60%" }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
