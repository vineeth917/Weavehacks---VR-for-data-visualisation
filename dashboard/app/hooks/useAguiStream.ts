"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export type AguiEventType =
  | "RUN_STARTED"
  | "TEXT_MESSAGE_CONTENT"
  | "TOOL_CALL_START"
  | "TOOL_CALL_END"
  | "STATE_DELTA"
  | "HANDOFF"
  | "RUN_FINISHED";

export interface AguiEvent {
  id: string;
  type: AguiEventType;
  agent: string;
  to?: string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: string;
  message?: string;
  ts: number;
  wallTs: number;
}

export type ConnectionStatus = "connecting" | "connected" | "mock" | "error";

const AGENT_COLORS: Record<string, string> = {
  router: "#6366f1",
  eda: "#10b981",
  training_monitor: "#f59e0b",
  narrator: "#ec4899",
};

export function agentColor(agent: string): string {
  return AGENT_COLORS[agent] ?? "#94a3b8";
}

let eventCounter = 0;

function assignId(ev: Omit<AguiEvent, "id" | "wallTs">): AguiEvent {
  return { ...ev, id: `ev-${eventCounter++}`, wallTs: Date.now() };
}

export function useAguiStream(backendUrl: string) {
  const [events, setEvents] = useState<AguiEvent[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const esRef = useRef<EventSource | null>(null);
  const mockTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pushEvent = useCallback((ev: AguiEvent) => {
    setEvents((prev) => [...prev, ev]);
  }, []);

  const startMockStream = useCallback(async () => {
    setStatus("mock");
    try {
      const res = await fetch("/mocks/agui-stream.json");
      const data: Omit<AguiEvent, "id" | "wallTs">[] = await res.json();
      data.forEach((ev, i) => {
        mockTimerRef.current = setTimeout(() => {
          pushEvent(assignId(ev));
        }, i * 600);
      });
    } catch {
      // silently ignore mock load failures
    }
  }, [pushEvent]);

  const replayMock = useCallback(() => {
    setEvents([]);
    eventCounter = 0;
    startMockStream();
  }, [startMockStream]);

  useEffect(() => {
    const url = `${backendUrl}/agui`;
    const es = new EventSource(url);
    esRef.current = es;

    const timeout = setTimeout(() => {
      if (status === "connecting") {
        es.close();
        startMockStream();
      }
    }, 3000);

    es.onopen = () => {
      clearTimeout(timeout);
      setStatus("connected");
    };

    es.onmessage = (e) => {
      try {
        const raw = JSON.parse(e.data);
        // Backend sends {event, agent, tool, args, result, ts (unix secs)}
        // Normalize to our internal shape {type, agent, tool, args, result, message, ts (ms)}
        const normalized: Omit<AguiEvent, "id" | "wallTs"> = {
          type: (raw.event ?? raw.type) as AguiEventType,
          agent: raw.agent ?? "system",
          to: raw.to,
          tool: raw.tool,
          args: raw.args,
          result: raw.result != null ? String(raw.result) : undefined,
          message: raw.message ?? raw.args?.message as string | undefined,
          ts: raw.ts > 1_000_000_000 ? Math.round(raw.ts * 1000) : raw.ts,
        };
        // Skip bare heartbeat STATE_DELTAs from the phase-0 stub
        if (normalized.type === "STATE_DELTA" && normalized.agent === "system") return;
        pushEvent(assignId(normalized));
      } catch {
        // ignore malformed events
      }
    };

    es.onerror = () => {
      clearTimeout(timeout);
      es.close();
      if (status !== "mock") startMockStream();
    };

    return () => {
      clearTimeout(timeout);
      es.close();
      if (mockTimerRef.current) clearTimeout(mockTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendUrl]);

  return { events, status, replayMock };
}
