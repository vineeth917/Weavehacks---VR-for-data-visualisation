"use client";

import { AguiEvent, agentColor } from "../hooks/useAguiStream";

interface Props {
  events: AguiEvent[];
}

const AGENTS = ["router", "eda", "training_monitor", "narrator", "preprocessor"];
const AGENT_LABELS: Record<string, string> = {
  router: "Router",
  eda: "EDA Agent",
  training_monitor: "Training Monitor",
  narrator: "Narrator",
  preprocessor: "Preprocessor",
};
const AGENT_DESC: Record<string, string> = {
  router: "Routes queries to specialists",
  eda: "Profiles data, generates panels",
  training_monitor: "Watches train/val curves",
  narrator: "Speaks the final report",
  preprocessor: "Pre-processes incoming data",
};

function deriveAgentState(agent: string, events: AguiEvent[]): string {
  const relevant = events.filter(
    (e) =>
      e.agent === agent ||
      (e.type === "HANDOFF" && e.to === agent) ||
      (e.type === "agent_status" && e.agent === agent)
  );
  if (relevant.length === 0) return "idle";
  const last = relevant[relevant.length - 1];
  if (last.type === "agent_status") {
    if (last.state === "done") return "done";
    if (last.state === "error") return "error";
    if (last.state === "thinking") return "working";
    return "active";
  }
  if (last.type === "RUN_FINISHED") return "done";
  if (last.type === "HANDOFF" && last.to === agent) return "active";
  if (last.type === "TOOL_CALL_START") return "working";
  if (last.type === "TOOL_CALL_END") return "active";
  if (last.type === "speech") return "speaking";
  if (last.type === "TEXT_MESSAGE_CONTENT") return "speaking";
  return "active";
}

function deriveLastAction(agent: string, events: AguiEvent[]): string {
  const relevant = events.filter((e) => e.agent === agent);
  if (relevant.length === 0) return "—";
  const last = relevant[relevant.length - 1];
  if (last.type === "speech" && last.text) return `"${last.text.slice(0, 45)}…"`;
  if (last.type === "agent_status" && last.message) return last.message.slice(0, 50);
  if (last.tool) return `${last.type === "TOOL_CALL_START" ? "calling" : "done"} ${last.tool}`;
  if (last.message) return last.message.slice(0, 50);
  return last.type;
}

const STATE_STYLES: Record<string, string> = {
  idle:     "bg-gray-900 border-gray-800 opacity-50",
  active:   "bg-gray-800 border-gray-600",
  working:  "bg-gray-800 border-yellow-500/60 shadow-yellow-500/10 shadow-lg",
  speaking: "bg-gray-800 border-pink-500/60 shadow-pink-500/10 shadow-lg",
  done:     "bg-gray-800 border-green-500/50",
  error:    "bg-gray-800 border-red-500/60",
};

const STATE_LABEL: Record<string, string> = {
  idle:     "idle",
  active:   "active",
  working:  "thinking…",
  speaking: "speaking",
  done:     "done",
  error:    "error",
};

export function AgentStatusGrid({ events }: Props) {
  const activeAgents = AGENTS.filter((a) =>
    events.some(
      (e) =>
        e.agent === a ||
        (e.type === "HANDOFF" && e.to === a) ||
        (e.type === "agent_status" && e.agent === a)
    )
  );

  // Always show the 4 core agents; add real-backend agents dynamically
  const extraAgents = [...new Set(events.map((e) => e.agent))]
    .filter((a) => a && a !== "system" && !AGENTS.includes(a));

  const displayed = [...new Set([...AGENTS.slice(0, 4), ...activeAgents, ...extraAgents])];

  return (
    <div className="grid grid-cols-2 gap-2">
      {displayed.map((agent) => {
        const state = deriveAgentState(agent, events);
        const lastAction = deriveLastAction(agent, events);
        const color = agentColor(agent);

        return (
          <div
            key={agent}
            className={`rounded-xl border p-2.5 transition-all duration-500 ${STATE_STYLES[state] ?? "bg-gray-800 border-gray-600"}`}
          >
            <div className="flex items-center justify-between mb-1 gap-1">
              <span className="text-xs font-bold truncate" style={{ color }}>
                {AGENT_LABELS[agent] ?? agent}
              </span>
              <span
                className="text-xs px-1.5 py-0.5 rounded-full flex-shrink-0"
                style={{
                  backgroundColor: color + "22",
                  color,
                  border: `1px solid ${color}44`,
                }}
              >
                {STATE_LABEL[state] ?? state}
              </span>
            </div>
            <p className="text-gray-500 text-xs mb-1 leading-tight">
              {AGENT_DESC[agent] ?? "Agent"}
            </p>
            <p className="text-gray-400 text-xs truncate leading-snug">{lastAction}</p>

            {(state === "working" || state === "speaking") && (
              <div className="mt-1.5 h-0.5 rounded-full overflow-hidden bg-gray-700">
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
