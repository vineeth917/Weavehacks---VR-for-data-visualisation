"use client";

/**
 * Read-only WebSocket spectator for the /ws backend.
 * Connects with session_id="spectator-dashboard" and never sends agent messages.
 * Exposes a typed stream of events the dashboard can render.
 */

import { useEffect, useRef, useState, useCallback } from "react";

export type WsEventType =
  | "speech"
  | "agent_status"
  | "voice_query"
  | "panels"
  | "training_update"
  | "report"
  | "scatter3d"
  | "kde_surface"
  | "field"
  | "highlight"
  | "transcript";

export interface WsEvent {
  id: string;
  type: WsEventType;
  agent?: string;
  text?: string;
  state?: string;
  message?: string;
  ts: number;
  raw: Record<string, unknown>;
}

let _counter = 0;

export type WsStatus = "connecting" | "connected" | "disconnected";

export function useWsSpectator(backendUrl: string) {
  const [events, setEvents] = useState<WsEvent[]>([]);
  const [status, setStatus] = useState<WsStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);

  const push = useCallback((ev: WsEvent) => {
    setEvents((prev) => [...prev.slice(-99), ev]); // keep last 100
  }, []);

  useEffect(() => {
    const wsUrl = backendUrl.replace(/^http/, "ws") + "/ws";

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        // Identify as spectator — backend accepts any session_id
        ws.send(JSON.stringify({
          type: "voice_query",
          text: "__spectator_ping__",
          session_id: "spectator-dashboard",
        }));
      };

      ws.onmessage = (e) => {
        try {
          const raw = JSON.parse(e.data) as Record<string, unknown>;
          const type = raw.type as WsEventType;
          if (!type) return;
          push({
            id: `ws-${_counter++}`,
            type,
            agent: raw.agent as string | undefined,
            text: (raw.text ?? raw.speech) as string | undefined,
            state: raw.state as string | undefined,
            message: raw.message as string | undefined,
            ts: Date.now(),
            raw,
          });
        } catch {
          // ignore malformed
        }
      };

      ws.onclose = () => {
        setStatus("disconnected");
        // Reconnect after 3s
        setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      wsRef.current?.close();
    };
  }, [backendUrl, push]);

  return { events, status };
}
