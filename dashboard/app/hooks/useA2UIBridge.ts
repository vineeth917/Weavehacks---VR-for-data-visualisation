"use client";

/**
 * Bridges the raw /agui SSE stream into the A2UI renderer.
 *
 * The backend sends AG-UI CUSTOM events carrying A2UI envelopes:
 *   { event: "CUSTOM", agent: "eda", args: { envelope: { surfaceUpdate: {...} } } }
 *
 * This hook:
 *  1. Connects to /agui via EventSource
 *  2. Extracts A2UI envelopes from CUSTOM events
 *  3. Feeds them to A2UIProvider via processMessages()
 *  4. Returns a sendAction() so surface buttons can post userAction back to /agui
 */

import { useEffect, useRef, useCallback } from "react";
import { useA2UIActions } from "@copilotkit/a2ui-renderer";

export function useA2UIBridge(backendUrl: string) {
  const { processMessages, dispatch } = useA2UIActions();
  const esRef = useRef<EventSource | null>(null);

  // Feed mock envelopes when backend is unavailable
  const feedMocks = useCallback(async () => {
    try {
      const res = await fetch("/mocks/a2ui-envelopes.json");
      const envelopes: Record<string, unknown>[] = await res.json();
      // stream them with realistic delays
      envelopes.forEach((env, i) => {
        setTimeout(() => processMessages([env]), i * 800 + 1000);
      });
    } catch {
      // no mocks, silently skip
    }
  }, [processMessages]);

  useEffect(() => {
    const url = `${backendUrl}/agui`;
    const es = new EventSource(url);
    esRef.current = es;

    // Fall back to mocks if backend doesn't connect in 3s
    const fallbackTimer = setTimeout(() => {
      if (es.readyState !== EventSource.OPEN) {
        es.close();
        feedMocks();
      }
    }, 3000);

    es.onopen = () => clearTimeout(fallbackTimer);

    es.onmessage = (e) => {
      try {
        const raw = JSON.parse(e.data);
        // AG-UI CUSTOM event carries envelope in args
        if (raw.event === "CUSTOM" && raw.args?.envelope) {
          processMessages([raw.args.envelope as Record<string, unknown>]);
        }
        // Also accept bare envelope (backend may send directly)
        if (raw.surfaceUpdate || raw.dataModelUpdate || raw.beginRendering) {
          processMessages([raw]);
        }
      } catch {
        // ignore malformed
      }
    };

    es.onerror = () => {
      clearTimeout(fallbackTimer);
      es.close();
      feedMocks();
    };

    return () => {
      clearTimeout(fallbackTimer);
      es.close();
    };
  }, [backendUrl, processMessages, feedMocks]);

  // Called when user clicks a surface button
  const sendAction = useCallback(
    (actionName: string, surfaceId: string, context: Record<string, unknown> = {}) => {
      const msg = {
        userAction: {
          name: actionName,
          surfaceId,
          context,
          timestamp: new Date().toISOString(),
        },
      };
      // Tell the A2UI store (for local state)
      dispatch(msg);
      // Send to backend over a fire-and-forget POST
      fetch(`${backendUrl}/agui/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(msg),
      }).catch(() => {
        // backend may not have the endpoint yet — silently ignore
      });
    },
    [backendUrl, dispatch]
  );

  return { sendAction };
}
