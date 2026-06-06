"use client";

import { AguiEvent, ConnectionStatus } from "../hooks/useAguiStream";

interface Props {
  events: AguiEvent[];
  status: ConnectionStatus;
}

const STATUS_STYLE: Record<ConnectionStatus, string> = {
  connecting: "text-yellow-400",
  connected:  "text-green-400",
  mock:       "text-blue-400",
  error:      "text-red-400",
};

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "● connecting",
  connected:  "● live",
  mock:       "● mock data",
  error:      "● error",
};

export function StatsBar({ events, status }: Props) {
  const toolCalls = events.filter((e) => e.type === "TOOL_CALL_START").length;
  const handoffs  = events.filter((e) => e.type === "HANDOFF").length;
  const messages  = events.filter((e) => e.type === "TEXT_MESSAGE_CONTENT").length;
  const isRunning = events.length > 0 && events[events.length - 1]?.type !== "RUN_FINISHED";

  return (
    <div className="flex items-center gap-4 text-xs text-gray-400 flex-wrap">
      <span className={`font-semibold ${STATUS_STYLE[status]}`}>
        {STATUS_LABEL[status]}
      </span>

      <span>{events.length} events</span>
      <span>{toolCalls} tool calls</span>
      <span>{handoffs} handoffs</span>
      <span>{messages} messages</span>

      {isRunning && (
        <span className="text-yellow-400 animate-pulse font-semibold">⚡ running</span>
      )}
    </div>
  );
}
