"use client";

import { useEffect, useRef } from "react";
import type { WsEvent } from "../hooks/useWsSpectator";

interface Props {
  events: WsEvent[];
  wsStatus: string;
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
  if (!agent) return "system";
  return agent.replace(/_/g, " ");
}

function EventRow({ ev }: { ev: WsEvent }) {
  const color = agentColor(ev.agent);

  if (ev.type === "voice_query" && ev.text !== "__spectator_ping__") {
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

  if (ev.type === "speech") {
    return (
      <div className="flex gap-2 items-start py-2 border-b border-gray-800">
        <span className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: color }} />
        <div>
          <span className="text-xs font-semibold" style={{ color }}>
            {agentLabel(ev.agent)}
          </span>
          <span className="text-gray-500 text-xs ml-1">says</span>
          <p className="text-xs text-gray-300 mt-0.5">{ev.text}</p>
        </div>
      </div>
    );
  }

  if (ev.type === "agent_status") {
    const state = ev.state ?? "";
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
        <span className="text-xs font-semibold" style={{ color }}>
          {agentLabel(ev.agent)}
        </span>
        <span className="text-xs" style={{ color: stateColor }}>{state}</span>
        {ev.message && (
          <span className="text-xs text-gray-500 truncate ml-1">{ev.message}</span>
        )}
      </div>
    );
  }

  if (ev.type === "report") {
    const raw = ev.raw as { sections?: { heading?: string; body?: string }[] };
    const sections = raw.sections ?? [];
    return (
      <div className="py-2 border-b border-pink-500/20">
        <p className="text-xs text-pink-400 font-semibold uppercase tracking-wider mb-1">
          Final report
        </p>
        {sections.slice(0, 3).map((s, i) => (
          <div key={i} className="mb-1">
            {s.heading && <p className="text-xs text-gray-300 font-semibold">{s.heading}</p>}
            {s.body && <p className="text-xs text-gray-500 line-clamp-2">{s.body}</p>}
          </div>
        ))}
      </div>
    );
  }

  if (ev.type === "training_update") {
    const raw = ev.raw as { step?: number; train_loss?: number; val_loss?: number };
    return (
      <div className="flex gap-3 items-center py-1 border-b border-gray-800/40 text-xs text-gray-500">
        <span className="text-yellow-500">↗</span>
        <span>step {raw.step}</span>
        <span>train {raw.train_loss?.toFixed(4)}</span>
        <span>val {raw.val_loss?.toFixed(4)}</span>
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

export function PipelineFeed({ events, wsStatus }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  const statusColor =
    wsStatus === "connected"    ? "text-green-400" :
    wsStatus === "disconnected" ? "text-red-400"   : "text-yellow-400";

  const statusDot =
    wsStatus === "connected"    ? "bg-green-400" :
    wsStatus === "disconnected" ? "bg-red-400"   : "bg-yellow-400";

  const visible = events.filter(
    (e) => e.type !== "voice_query" || e.text !== "__spectator_ping__"
  );

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-700 p-3 flex flex-col min-h-0 h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
        <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider">
          Live Pipeline
        </p>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${statusDot} ${wsStatus === "connecting" ? "animate-pulse" : ""}`} />
          <span className={`text-xs ${statusColor}`}>{wsStatus}</span>
        </div>
      </div>

      {/* Feed */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {visible.length === 0 ? (
          <div className="h-full flex items-center justify-center text-gray-600 text-xs text-center px-4">
            Waiting for Quest voice query…
          </div>
        ) : (
          <>
            {visible.map((ev) => (
              <EventRow key={ev.id} ev={ev} />
            ))}
            <div ref={bottomRef} />
          </>
        )}
      </div>
    </div>
  );
}
