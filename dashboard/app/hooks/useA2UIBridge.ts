"use client";

/**
 * Bridges the raw /agui SSE stream into the A2UI v0.9 renderer.
 *
 * Backend (SPEC_VERSION="0.8") sends CUSTOM events with v0.8 envelope names:
 *   surfaceUpdate / dataModelUpdate / beginRendering
 *
 * @a2ui/web_core v0.9 MessageProcessor only understands:
 *   createSurface / updateComponents / updateDataModel
 *
 * translateV08ToV09() converts on the fly so nothing needs to change on
 * Vineeth's side. Mocks are already in v0.9 format.
 */

import { useEffect, useRef, useCallback } from "react";
import { useA2UIActions } from "@copilotkit/a2ui-renderer";

const SESSION_ID = "demo-session-1";
const CATALOG_ID = "basic";

// ---------------------------------------------------------------------------
// v0.8 → v0.9 data-model decoder
// A2UI v0.8 encodes data as adjacency-list "contents" arrays:
//   [{key, valueString|valueNumber|valueBoolean|valueList|valueMap}, ...]
// v0.9 updateDataModel.value is a plain JS object.
// ---------------------------------------------------------------------------
type V08Value =
  | { valueString: string }
  | { valueNumber: number }
  | { valueBoolean: boolean }
  | { valueList: V08Entry[] }
  | { valueMap: V08Entry[] };

type V08Entry = { key: string } & V08Value;

function decodeV08Value(v: V08Value): unknown {
  if ("valueString" in v) return v.valueString;
  if ("valueNumber" in v) return v.valueNumber;
  if ("valueBoolean" in v) return v.valueBoolean;
  if ("valueList" in v) return v.valueList.map((e) => decodeV08Value(e as V08Value));
  if ("valueMap" in v) return decodeV08Contents(v.valueMap);
  return null;
}

function decodeV08Contents(contents: V08Entry[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const entry of contents) {
    const { key, ...rest } = entry;
    out[key] = decodeV08Value(rest as V08Value);
  }
  return out;
}

// ---------------------------------------------------------------------------
// v0.8 component shape: { id, component: { "Text": { text: ... } } }
// v0.9 component shape: { id, component: "Text", text: ... }
// ---------------------------------------------------------------------------
function translateComponent(c: Record<string, unknown>): Record<string, unknown> {
  const { id, component } = c as { id: string; component: Record<string, unknown> };
  if (!component || typeof component !== "object") return c;
  const [typeName, props] = Object.entries(component)[0] ?? [];
  if (!typeName) return c;
  return { id, component: typeName, ...(props as Record<string, unknown>) };
}

// ---------------------------------------------------------------------------
// Main translator: one v0.8 envelope → zero or more v0.9 messages
// ---------------------------------------------------------------------------
const _createdSurfaces = new Set<string>();

function translateV08ToV09(envelope: Record<string, unknown>): Record<string, unknown>[] {
  // surfaceUpdate → createSurface (if new) + updateComponents
  if (envelope.surfaceUpdate) {
    const su = envelope.surfaceUpdate as {
      surfaceId: string;
      components: Record<string, unknown>[];
    };
    const msgs: Record<string, unknown>[] = [];
    if (!_createdSurfaces.has(su.surfaceId)) {
      _createdSurfaces.add(su.surfaceId);
      msgs.push({
        version: "v0.9",
        createSurface: { surfaceId: su.surfaceId, catalogId: CATALOG_ID },
      });
    }
    msgs.push({
      version: "v0.9",
      updateComponents: {
        surfaceId: su.surfaceId,
        components: su.components.map(translateComponent),
      },
    });
    return msgs;
  }

  // dataModelUpdate → updateDataModel (decode contents adjacency list)
  if (envelope.dataModelUpdate) {
    const dmu = envelope.dataModelUpdate as {
      surfaceId: string;
      path?: string;
      contents?: V08Entry[];
      value?: unknown;
    };
    const value =
      dmu.value !== undefined
        ? dmu.value
        : dmu.contents
        ? decodeV08Contents(dmu.contents)
        : {};
    return [
      {
        version: "v0.9",
        updateDataModel: {
          surfaceId: dmu.surfaceId,
          path: dmu.path ?? "/",
          value,
        },
      },
    ];
  }

  // beginRendering → no-op in v0.9 (surface activates on createSurface)
  if (envelope.beginRendering) return [];

  // Already v0.9 — pass through
  if (
    envelope.createSurface ||
    envelope.updateComponents ||
    envelope.updateDataModel
  ) {
    return [envelope];
  }

  return [];
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------
export function useA2UIBridge(backendUrl: string) {
  const { processMessages, dispatch } = useA2UIActions();
  const esRef = useRef<EventSource | null>(null);

  const feedMocks = useCallback(async () => {
    try {
      const res = await fetch("/mocks/a2ui-envelopes.json");
      const envelopes: Record<string, unknown>[] = await res.json();
      envelopes.forEach((env, i) => {
        setTimeout(() => processMessages([env]), i * 800 + 1000);
      });
    } catch {
      // no mocks, silently skip
    }
  }, [processMessages]);

  const feedEnvelope = useCallback(
    (envelope: Record<string, unknown>) => {
      const msgs = translateV08ToV09(envelope);
      if (msgs.length > 0) processMessages(msgs);
    },
    [processMessages]
  );

  useEffect(() => {
    const url = `${backendUrl}/agui`;
    const es = new EventSource(url);
    esRef.current = es;

    let gotA2UIEvent = false;
    let mocksFed = false;

    const doFeedMocks = () => {
      if (mocksFed) return;
      mocksFed = true;
      feedMocks();
    };

    const connectTimer = setTimeout(() => {
      if (es.readyState !== EventSource.OPEN) {
        es.close();
        doFeedMocks();
      }
    }, 3000);

    const noEventTimer = setTimeout(() => {
      if (!gotA2UIEvent) doFeedMocks();
    }, 5000);

    es.onopen = () => clearTimeout(connectTimer);

    es.onmessage = (e) => {
      try {
        const raw = JSON.parse(e.data);
        // Backend wire format: {type:"CUSTOM", name:"surfaceUpdate"|..., value:{...}, ts}
        if ((raw.type === "CUSTOM" || raw.event === "CUSTOM") && raw.name && raw.value) {
          const envelope = { [raw.name]: (raw.value as Record<string, unknown>)[raw.name as string] ?? raw.value };
          gotA2UIEvent = true;
          clearTimeout(noEventTimer);
          feedEnvelope(envelope);
          return;
        }
        // Fallback: args.envelope shape
        if (raw.args?.envelope) {
          gotA2UIEvent = true;
          clearTimeout(noEventTimer);
          feedEnvelope(raw.args.envelope as Record<string, unknown>);
          return;
        }
        // Bare envelope
        if (raw.surfaceUpdate || raw.dataModelUpdate || raw.beginRendering ||
            raw.createSurface || raw.updateComponents || raw.updateDataModel) {
          gotA2UIEvent = true;
          clearTimeout(noEventTimer);
          feedEnvelope(raw);
        }
      } catch {
        // ignore malformed
      }
    };

    es.onerror = () => {
      clearTimeout(connectTimer);
      clearTimeout(noEventTimer);
      es.close();
      doFeedMocks();
    };

    return () => {
      clearTimeout(connectTimer);
      clearTimeout(noEventTimer);
      es.close();
    };
  }, [backendUrl, feedEnvelope, feedMocks]);

  const sendAction = useCallback(
    (actionName: string, surfaceId: string, context: Record<string, unknown> = {}) => {
      const msg = {
        session_id: SESSION_ID,
        userAction: {
          name: actionName,
          surfaceId,
          context,
          timestamp: new Date().toISOString(),
        },
      };
      dispatch(msg);
      fetch(`${backendUrl}/agui/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(msg),
      })
        .then((r) => r.json())
        .then((r) => console.log("[a2ui action]", actionName, r))
        .catch((err) => console.warn("[a2ui action] POST failed", err));
    },
    [backendUrl, dispatch]
  );

  return { sendAction };
}
