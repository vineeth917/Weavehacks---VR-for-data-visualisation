"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export type AguiEventType =
  | "RUN_STARTED"
  | "TEXT_MESSAGE_CONTENT"
  | "TOOL_CALL_START"
  | "TOOL_CALL_END"
  | "STATE_DELTA"
  | "HANDOFF"
  | "RUN_FINISHED"
  | "speech"
  | "agent_status"
  | "voice_query"
  | "report"
  | "panels"
  | "training_update"
  | "CUSTOM";

export interface AguiEvent {
  id: string;
  type: AguiEventType;
  agent: string;
  to?: string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: string;
  message?: string;
  text?: string;
  state?: string;
  sections?: { heading?: string; body?: string }[];
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
  const mockFedRef = useRef(false);
  // track whether we ever got a real backend message — if so, never fall back to mocks
  const everConnectedRef = useRef(false);

  const pushEvent = useCallback((ev: AguiEvent) => {
    setEvents((prev) => [...prev, ev]);
  }, []);

  const startMockStream = useCallback(async () => {
    // Never load mocks if we already got real backend data
    if (mockFedRef.current || everConnectedRef.current) return;
    mockFedRef.current = true;
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
    mockFedRef.current = false;
    everConnectedRef.current = false;
    startMockStream();
  }, [startMockStream]);

  useEffect(() => {
    const url = `${backendUrl}/agui`;
    const es = new EventSource(url);
    esRef.current = es;

    // Fall back to mocks only if no connection within 5s
    const timeout = setTimeout(() => {
      if (!everConnectedRef.current) {
        startMockStream();
      }
    }, 5000);

    es.onopen = () => {
      clearTimeout(timeout);
      everConnectedRef.current = true;
      setStatus("connected");
    };

    es.onmessage = (e) => {
      // Any message = definitely connected — cancel mock fallback
      if (!everConnectedRef.current) {
        everConnectedRef.current = true;
        clearTimeout(timeout);
        setStatus("connected");
      }
      try {
        const raw = JSON.parse(e.data);
        const isCustom = (raw.event === "CUSTOM" || raw.type === "CUSTOM");
        const value = (raw.value ?? raw.args ?? {}) as Record<string, unknown>;
        const pipelineType = isCustom ? (raw.name as AguiEventType | undefined) : undefined;
        const effectiveType = (pipelineType ?? raw.event ?? raw.type) as AguiEventType;

        const normalized: Omit<AguiEvent, "id" | "wallTs"> = {
          type: effectiveType,
          agent: (raw.agent ?? value.agent ?? "system") as string,
          to: raw.to as string | undefined,
          tool: raw.tool as string | undefined,
          args: isCustom ? value : raw.args as Record<string, unknown> | undefined,
          result: raw.result != null ? String(raw.result) : undefined,
          message: (raw.message ?? value.message ?? raw.args?.message) as string | undefined,
          text: (value.text ?? value.speech ?? raw.text) as string | undefined,
          state: value.state as string | undefined,
          sections: value.sections as { heading?: string; body?: string }[] | undefined,
          ts: raw.ts > 1_000_000_000 ? Math.round(raw.ts * 1000) : raw.ts,
        };
        // Skip bare heartbeat STATE_DELTAs
        if (normalized.type === "STATE_DELTA" && normalized.agent === "system") return;
        // Skip A2UI surface events — handled by useA2UIBridge
        if (isCustom && ["surfaceUpdate","dataModelUpdate","beginRendering",
            "createSurface","updateComponents","updateDataModel"].includes(raw.name as string)) return;
        pushEvent(assignId(normalized));
      } catch {
        // ignore malformed events
      }
    };

    es.onerror = () => {
      // Only fall back to mocks if we never successfully connected
      if (!everConnectedRef.current) {
        clearTimeout(timeout);
        es.close();
        startMockStream();
      }
      // If we were connected, SSE will auto-reconnect — don't close or load mocks
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
