"use client";

import { useEffect, useRef } from "react";
import type { AguiEvent, ConnectionStatus } from "../hooks/useAguiStream";

interface Props {
  events: AguiEvent[];
  status: ConnectionStatus;
}

const AGENT_COLOR: Record<string, string> = {
  router:           "#6366f1",
  eda:              "#10b981",
  trainer:          "#f59e0b",
  training_monitor: "#f59e0b",
  narrator:         "#ec4899",
  preprocessor:     "#38bdf8",
  evals:            "#a78bfa",
};

function agentColor(agent?: string) {
  return agent ? (AGENT_COLOR[agent] ?? "#94a3b8") : "#94a3b8";
}

function AgentPill({ agent }: { agent?: string }) {
  if (!agent || agent === "system") return null;
  const color = agentColor(agent);
  const label = agent.replace(/_/g, " ");
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-bold"
      style={{ backgroundColor: color + "22", color, border: `1px solid ${color}55` }}
    >
      {label}
    </span>
  );
}

function EventRow({ ev }: { ev: AguiEvent }) {
  if (ev.type === "voice_query" && ev.text) {
    return (
      <div className="flex gap-2 items-start py-2.5 border-b border-indigo-500/20 bg-indigo-500/5 px-2 rounded-lg mb-1">
        <span className="text-base mt-0.5 flex-shrink-0">🎤</span>
        <div>
          <p className="text-xs text-indigo-400 font-bold uppercase tracking-wider">User Query</p>
          <p className="text-sm text-white mt-0.5 italic font-medium">&ldquo;{ev.text}&rdquo;</p>
        </div>
      </div>
    );
  }

  if (ev.type === "HANDOFF") {
    return (
      <div className="flex gap-1.5 items-center py-1.5 border-b border-gray-800/60 text-xs">
        <span className="text-gray-500 font-bold">→</span>
        <AgentPill agent={ev.agent} />
        <span className="text-gray-500">handed off to</span>
        <AgentPill agent={ev.to} />
      </div>
    );
  }

  if (ev.type === "TOOL_CALL_START") {
    const color = agentColor(ev.agent);
    return (
      <div className="flex gap-1.5 items-center py-1.5 border-b border-gray-800/60 text-xs">
        <span style={{ color }}>⚙</span>
        <AgentPill agent={ev.agent} />
        <span className="text-gray-400">calling</span>
        <code className="text-yellow-300 font-mono bg-yellow-500/10 px-1 py-0.5 rounded">{ev.tool}</code>
      </div>
    );
  }

  if (ev.type === "TOOL_CALL_END" && ev.result) {
    return (
      <div className="flex gap-1.5 items-center py-1 border-b border-gray-800/40 text-xs text-green-400 pl-3">
        <span>✓</span>
        <span className="text-gray-500 truncate">↳ {ev.result}</span>
      </div>
    );
  }

  if (ev.type === "speech" && ev.text) {
    const color = agentColor(ev.agent);
    return (
      <div className="flex gap-2 items-start py-2 border-b border-gray-800">
        <span className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: color }} />
        <div>
          <AgentPill agent={ev.agent} />
          <p className="text-xs text-gray-300 mt-1">{ev.text}</p>
        </div>
      </div>
    );
  }

  if (ev.type === "agent_status") {
    const state = ev.state ?? "";
    if (!state || state === "idle") return null;
    const stateColor = state === "thinking" ? "#f59e0b" : state === "done" ? "#10b981" : state === "error" ? "#ef4444" : "#94a3b8";
    const icon = state === "thinking" ? "⟳" : state === "done" ? "✓" : state === "error" ? "✗" : "·";
    return (
      <div className="flex gap-1.5 items-center py-1 border-b border-gray-800/40 text-xs pl-1">
        <span style={{ color: stateColor }}>{icon}</span>
        <AgentPill agent={ev.agent} />
        <span style={{ color: stateColor }}>{state}</span>
      </div>
    );
  }

  if (ev.type === "report" && ev.sections?.length) {
    return (
      <div className="py-2 border-b border-pink-500/20">
        <p className="text-xs text-pink-400 font-bold uppercase tracking-wider mb-1">Final Report</p>
        {ev.sections.slice(0, 3).map((s, i) => (
          <div key={i} className="mb-1">
            {s.heading && <p className="text-xs text-gray-200 font-semibold">{s.heading}</p>}
            {s.body && <p className="text-xs text-gray-500 line-clamp-2">{s.body}</p>}
          </div>
        ))}
      </div>
    );
  }

  if (ev.type === "RUN_STARTED") {
    return (
      <div className="flex gap-1.5 items-center py-1.5 border-b border-gray-800/60 text-xs">
        <span className="text-green-400">▶</span>
        <AgentPill agent={ev.agent} />
        <span className="text-gray-400">run started</span>
        {ev.message && <span className="text-gray-500 truncate">{ev.message}</span>}
      </div>
    );
  }

  if (ev.type === "RUN_FINISHED") {
    return (
      <div className="flex gap-1.5 items-center py-1.5 text-xs">
        <span className="text-gray-500">■</span>
        <AgentPill agent={ev.agent} />
        <span className="text-gray-500">run complete</span>
      </div>
    );
  }

  if (ev.type === "panels") {
    return (
      <div className="flex gap-1.5 items-center py-1.5 border-b border-gray-800/50 text-xs text-emerald-400">
        <span>▦</span>
        <span>EDA panels rendered</span>
      </div>
    );
  }

  return null;
}

const PIPELINE_TYPES = new Set([
  "voice_query","speech","agent_status","report","panels",
  "HANDOFF","TOOL_CALL_START","TOOL_CALL_END","RUN_STARTED","RUN_FINISHED",
]);

export function PipelineFeed({ events, status }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  const statusColor =
    status === "connected" ? "text-green-400" :
    status === "mock"      ? "text-yellow-400" : "text-gray-500";
  const dotColor =
    status === "connected" ? "bg-green-400" :
    status === "mock"      ? "bg-yellow-400 animate-pulse" : "bg-gray-500";

  const visible = events.filter((e) => PIPELINE_TYPES.has(e.type));

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-700 p-3 flex flex-col min-h-0 h-full">
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
        <p className="text-xs text-white font-bold uppercase tracking-widest">Live Pipeline</p>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
          <span className={`text-xs font-semibold ${statusColor}`}>{status}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 space-y-0.5">
        {visible.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center gap-2 text-gray-600 text-xs text-center px-4">
            <span className="text-2xl opacity-30">🎤</span>
            <span>Waiting for Quest voice query…</span>
          </div>
        ) : (
          <>
            {visible.map((ev) => <EventRow key={ev.id} ev={ev} />)}
            <div ref={bottomRef} />
          </>
        )}
      </div>
    </div>
  );
}
