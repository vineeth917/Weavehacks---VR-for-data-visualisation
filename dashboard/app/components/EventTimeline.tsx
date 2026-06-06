"use client";

import { useEffect, useRef } from "react";
import { AguiEvent, agentColor } from "../hooks/useAguiStream";
import { AgentBadge } from "./AgentBadge";

interface Props {
  events: AguiEvent[];
}

const EVENT_ICONS: Record<string, string> = {
  RUN_STARTED: "▶",
  TEXT_MESSAGE_CONTENT: "💬",
  TOOL_CALL_START: "⚙",
  TOOL_CALL_END: "✓",
  STATE_DELTA: "~",
  HANDOFF: "→",
  RUN_FINISHED: "■",
};

function EventRow({ ev, isLatest }: { ev: AguiEvent; isLatest: boolean }) {
  const color = agentColor(ev.agent);
  const icon = EVENT_ICONS[ev.type] ?? "•";

  return (
    <div
      className={`flex gap-3 items-start py-2 px-3 rounded-lg transition-all duration-300 ${
        isLatest ? "bg-gray-800/60" : "hover:bg-gray-800/30"
      }`}
    >
      <span className="text-xs mt-0.5 w-4 text-center opacity-70" style={{ color }}>
        {icon}
      </span>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <AgentBadge agent={ev.agent} pulse={isLatest && ev.type === "TOOL_CALL_START"} />

          {ev.type === "HANDOFF" && ev.to && (
            <>
              <span className="text-gray-500 text-xs">→</span>
              <AgentBadge agent={ev.to} />
            </>
          )}

          <span className="text-gray-400 text-xs font-medium">{ev.type}</span>

          {ev.tool && (
            <code className="text-xs px-1.5 py-0.5 rounded bg-gray-700 text-yellow-300">
              {ev.tool}
            </code>
          )}
        </div>

        {ev.message && (
          <p className="text-gray-300 text-xs mt-1 truncate">{ev.message}</p>
        )}

        {ev.args && Object.keys(ev.args).length > 0 && (
          <p className="text-gray-500 text-xs mt-0.5 font-mono truncate">
            {JSON.stringify(ev.args)}
          </p>
        )}

        {ev.result && (
          <p className="text-green-400 text-xs mt-0.5 truncate">↳ {ev.result}</p>
        )}
      </div>

      <span className="text-gray-600 text-xs whitespace-nowrap">
        +{ev.ts}ms
      </span>
    </div>
  );
}

export function EventTimeline({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (events.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        Waiting for events...
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto space-y-0.5 pr-1">
      {events.map((ev, i) => (
        <EventRow key={ev.id} ev={ev} isLatest={i === events.length - 1} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
