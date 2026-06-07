"use client";

import { AguiEvent, agentColor } from "../hooks/useAguiStream";

interface Props {
  events: AguiEvent[];
}

const CORE_AGENTS = ["router", "eda", "training_monitor", "narrator"];

const AGENT_LABELS: Record<string, string> = {
  router:           "Router",
  eda:              "EDA Agent",
  training_monitor: "Training Monitor",
  narrator:         "Narrator",
  preprocessor:     "Preprocessor",
  evals:            "Evaluator",
  trainer:          "Trainer",
};

const AGENT_DESC: Record<string, string> = {
  router:           "Routes queries to specialists",
  eda:              "Profiles data, generates panels",
  training_monitor: "Watches train/val curves",
  narrator:         "Speaks the final report",
  preprocessor:     "Pre-processes incoming data",
  evals:            "Runs evaluation suite",
  trainer:          "Trains the model",
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

function deriveDetail(agent: string, events: AguiEvent[]): { label: string; value: string } | null {
  const relevant = events.filter((e) => e.agent === agent);
  if (relevant.length === 0) return null;
  const last = relevant[relevant.length - 1];
  if (last.type === "TOOL_CALL_START" && last.tool)
    return { label: "calling", value: last.tool };
  if (last.type === "TOOL_CALL_END" && last.tool)
    return { label: "done", value: last.tool };
  if (last.type === "speech" && last.text)
    return { label: "says", value: `"${last.text.slice(0, 40)}${last.text.length > 40 ? "…" : ""}"` };
  if (last.type === "agent_status" && last.message)
    return { label: "status", value: last.message.slice(0, 45) };
  if (last.message)
    return { label: "", value: last.message.slice(0, 50) };
  return null;
}

const STATE_STYLES: Record<string, string> = {
  idle:     "bg-gray-900 border-gray-800 opacity-40",
  active:   "bg-gray-900 border-gray-600",
  working:  "bg-gray-900 border-yellow-400/80 shadow-yellow-400/15 shadow-lg",
  speaking: "bg-gray-900 border-pink-400/80 shadow-pink-400/15 shadow-lg",
  done:     "bg-gray-900 border-green-400/60",
  error:    "bg-gray-900 border-red-400/70",
};

const STATE_LABEL: Record<string, string> = {
  idle:     "idle",
  active:   "active",
  working:  "thinking…",
  speaking: "speaking",
  done:     "done",
  error:    "error",
};

const STATE_LABEL_COLOR: Record<string, string> = {
  idle:     "text-gray-600",
  active:   "text-blue-400",
  working:  "text-yellow-400",
  speaking: "text-pink-400",
  done:     "text-green-400",
  error:    "text-red-400",
};

export function AgentStatusGrid({ events }: Props) {
  const extraAgents = [...new Set(events.map((e) => e.agent))]
    .filter((a) => a && a !== "system" && !CORE_AGENTS.includes(a));

  const displayed = [...new Set([...CORE_AGENTS, ...extraAgents])];

  return (
    <div className="grid grid-cols-2 gap-2">
      {displayed.map((agent) => {
        const state = deriveAgentState(agent, events);
        const detail = deriveDetail(agent, events);
        const color = agentColor(agent);
        const isActive = state !== "idle";

        return (
          <div
            key={agent}
            className={`rounded-xl border-2 p-2.5 transition-all duration-500 ${STATE_STYLES[state] ?? "bg-gray-900 border-gray-700"}`}
          >
            {/* Name + state badge */}
            <div className="flex items-start justify-between gap-1 mb-1">
              <span className="text-xs font-extrabold leading-tight" style={{ color }}>
                {AGENT_LABELS[agent] ?? agent}
              </span>
              <span className={`text-xs font-bold flex-shrink-0 ${STATE_LABEL_COLOR[state] ?? "text-gray-400"}`}>
                {STATE_LABEL[state] ?? state}
              </span>
            </div>

            {/* Description */}
            <p className="text-gray-500 text-xs mb-1.5 leading-snug">
              {AGENT_DESC[agent] ?? "Agent"}
            </p>

            {/* Detail row — tool call / speech / status */}
            {isActive && detail ? (
              <div className="flex items-center gap-1 mt-1">
                {detail.label && (
                  <span className="text-gray-600 text-xs">{detail.label}</span>
                )}
                <span
                  className="text-xs font-semibold truncate"
                  style={{ color: detail.label === "calling" ? "#fbbf24" : detail.label === "says" ? color : "#d1d5db" }}
                >
                  {detail.value}
                </span>
              </div>
            ) : !isActive ? (
              <p className="text-gray-700 text-xs">—</p>
            ) : null}

            {/* Activity bar */}
            {(state === "working" || state === "speaking") && (
              <div className="mt-2 h-0.5 rounded-full overflow-hidden bg-gray-800">
                <div
                  className="h-full rounded-full animate-pulse"
                  style={{ backgroundColor: color, width: "65%" }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
