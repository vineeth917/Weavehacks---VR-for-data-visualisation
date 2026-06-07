"use client";

import { useEffect, useRef } from "react";
import type { AguiEvent } from "../hooks/useAguiStream";
import type { ConnectionStatus } from "../hooks/useAguiStream";

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

function agentLabel(agent?: string) {
  if (!agent || agent === "system") return "system";
  return agent.replace(/_/g, " ");
}

function EventRow({ ev }: { ev: AguiEvent }) {
  const color = agentColor(ev.agent);

  if (ev.type === "voice_query" && ev.text) {
    return (
      <div className="flex gap-2 items-start py-2 border-b border-gray-800">
        <span className="text-xs mt-0.5">🎤</span>
        <div>
          <p className="text-xs text-gray-400 font-semibold uppercase tracking-wider">User query</p>
          <p className="text-sm text-white mt-0.5 italic">"{ev.text}"</p>
        </div>
      </div>
    );
  }

  if (ev.type === "speech" && ev.text) {
    return (
      <div className="flex gap-2 items-start py-2 border-b border-gray-800">
        <span className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: color }} />
        <div>
          <span className="text-xs font-semibold" style={{ color }}>{agentLabel(ev.agent)}</span>
          <span className="text-gray-500 text-xs ml-1">says</span>
          <p className="text-xs text-gray-300 mt-0.5">{ev.text}</p>
        </div>
      </div>
    );
  }

  if (ev.type === "agent_status") {
    const state = ev.state ?? ev.message ?? "";
    const stateColor =
      state === "thinking" ? "#f59e0b" :
      state === "done"     ? "#10b981" :
      state === "error"    ? "#ef4444" : "#94a3b8";
    const icon =
      state === "thinking" ? "⟳" :
      state === "done"     ? "✓" :
      state === "error"    ? "✗" : "·";
    return (
      <div className="flex gap-2 items-center py-1.5 border-b border-gray-800/50">
        <span className="text-xs" style={{ color: stateColor }}>{icon}</span>
        <span className="text-xs font-semibold" style={{ color }}>{agentLabel(ev.agent)}</span>
        <span className="text-xs" style={{ color: stateColor }}>{state}</span>
        {ev.message && state !== ev.message && (
          <span className="text-xs text-gray-500 truncate ml-1">{ev.message}</span>
        )}
      </div>
    );
  }

  if (ev.type === "HANDOFF") {
    return (
      <div className="flex gap-2 items-center py-1.5 border-b border-gray-800/50 text-xs">
        <span className="text-gray-500">→</span>
        <span style={{ color }}>{agentLabel(ev.agent)}</span>
        <span className="text-gray-500">handed off to</span>
        <span style={{ color: agentColor(ev.to) }}>{agentLabel(ev.to)}</span>
      </div>
    );
  }

  if (ev.type === "report" && ev.sections?.length) {
    return (
      <div className="py-2 border-b border-pink-500/20">
        <p className="text-xs text-pink-400 font-semibold uppercase tracking-wider mb-1">Final report</p>
        {ev.sections.slice(0, 3).map((s, i) => (
          <div key={i} className="mb-1">
            {s.heading && <p className="text-xs text-gray-300 font-semibold">{s.heading}</p>}
            {s.body && <p className="text-xs text-gray-500 line-clamp-2">{s.body}</p>}
          </div>
        ))}
      </div>
    );
  }

  if (ev.type === "TOOL_CALL_START") {
    return (
      <div className="flex gap-2 items-center py-1 border-b border-gray-800/40 text-xs text-gray-500">
        <span style={{ color }}>⚙</span>
        <span style={{ color }}>{agentLabel(ev.agent)}</span>
        <span>calling</span>
        <span className="text-gray-400 font-mono">{ev.tool}</span>
      </div>
    );
  }

  if (ev.type === "panels") {
    return (
      <div className="flex gap-2 items-center py-1.5 border-b border-gray-800/50 text-xs text-emerald-400">
        <span>▦</span>
        <span>EDA panels rendered</span>
      </div>
    );
  }

  return null;
}

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
    status === "mock"      ? "bg-yellow-400" : "bg-gray-500";

  const PIPELINE_TYPES = new Set([
    "voice_query","speech","agent_status","report","panels",
    "HANDOFF","TOOL_CALL_START","TOOL_CALL_END","RUN_STARTED","RUN_FINISHED"
  ]);

  const visible = events.filter((e) => PIPELINE_TYPES.has(e.type));

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-700 p-3 flex flex-col min-h-0 h-full">
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
        <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider">Live Pipeline</p>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
          <span className={`text-xs ${statusColor}`}>{status}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {visible.length === 0 ? (
          <div className="h-full flex items-center justify-center text-gray-600 text-xs text-center px-4">
            Waiting for Quest voice query…
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
