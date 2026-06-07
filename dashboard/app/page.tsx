"use client";

import { useRef, useCallback } from "react";
import { useAguiStream } from "./hooks/useAguiStream";
import { EventTimeline } from "./components/EventTimeline";
import { AgentStatusGrid } from "./components/AgentStatusGrid";
import { SwarmGraph } from "./components/SwarmGraph";
import { StatsBar } from "./components/StatsBar";
import { LossChart } from "./components/LossChart";
import { A2UIPanel } from "./components/A2UIPanel";
import { PipelineFeed } from "./components/PipelineFeed";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8080";

export default function Dashboard() {
  const { events, status, replayMock } = useAguiStream(BACKEND_URL);
  const stopTrainingRef = useRef<(() => void) | null>(null);

  const handleStopTraining = useCallback(() => {
    stopTrainingRef.current?.();
  }, []);

  const lastQuery = [...events].reverse().find((e) => e.type === "voice_query" && e.text);

  // Extract dataset name from real backend events.
  // Backend emits AgentStatus with message "panels=N findings=N ds=<name>"
  // Also falls back to TOOL_CALL_START profile_dataset args.dataset
  const datasetName = (() => {
    // 1. agent_status message contains "ds=<name>"
    for (const ev of [...events].reverse()) {
      if ((ev.type === "agent_status" || ev.type === "STATE_DELTA") && ev.message) {
        const m = ev.message.match(/\bds=([^\s,]+)/);
        if (m) return m[1];
      }
    }
    // 2. TOOL_CALL_START with tool=profile_dataset, args.dataset or args.name
    const profileCall = events.find(
      (e) => e.type === "TOOL_CALL_START" && e.tool === "profile_dataset" && e.args
    );
    if (profileCall?.args) {
      const val = profileCall.args.dataset ?? profileCall.args.name;
      if (val) return String(val);
    }
    // 3. voice_query or RUN_STARTED message mentioning "dataset loaded: <name>"
    for (const ev of events) {
      if (ev.message) {
        const m = ev.message.match(/dataset loaded:\s*([^\s]+)/i);
        if (m) return m[1];
      }
    }
    return undefined;
  })();

  return (
    // outer wrapper scrolls horizontally if window < 1280px so nothing is cut off
    <div className="h-screen overflow-x-auto">
      <div className="h-full flex flex-col p-3 gap-2" style={{ minWidth: 1280 }}>

        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between flex-shrink-0">
          <div>
            <h1 className="text-lg font-bold text-white tracking-tight">
              DataDive — Agent Swarm
            </h1>
            <p className="text-gray-500 text-xs">
              Live orchestration view · WeaveHacks 4
            </p>
          </div>
          <div className="flex items-center gap-2">
            {lastQuery && (
              <div className="flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/30 rounded-lg px-3 py-1.5">
                <span className="text-xs">🎤</span>
                <span className="text-xs text-indigo-300 max-w-xs truncate italic">
                  &ldquo;{lastQuery.text}&rdquo;
                </span>
              </div>
            )}
            <StatsBar events={events} status={status} />
            <button
              onClick={replayMock}
              className="text-xs px-3 py-1.5 rounded-lg bg-gray-800 border border-gray-600 text-gray-300 hover:bg-gray-700 transition-colors"
            >
              ↺ replay mock
            </button>
          </div>
        </div>

        {/* ── Main 5-column grid ─────────────────────────────────────────── */}
        <div
          className="flex-1 grid gap-2 min-h-0"
          style={{ gridTemplateColumns: "minmax(200px,1fr) minmax(220px,1.2fr) minmax(220px,1fr) minmax(200px,1fr) minmax(200px,1fr)" }}
        >

          {/* Col 1: Swarm graph + agent cards (no overflow-hidden — let cards expand) */}
          <div className="flex flex-col gap-2 min-h-0">
            <SwarmGraph events={events} />
            {/* agent cards scroll independently */}
            <div className="flex-1 min-h-0 overflow-y-auto">
              <AgentStatusGrid events={events} />
            </div>
          </div>

          {/* Col 2: Training loss chart */}
          <div className="flex flex-col min-h-0">
            <LossChart onStopRef={stopTrainingRef} />
          </div>

          {/* Col 3: A2UI agent-generated surfaces */}
          <div className="flex flex-col min-h-0">
            <A2UIPanel backendUrl={BACKEND_URL} onStopTraining={handleStopTraining} />
          </div>

          {/* Col 4: Live pipeline feed */}
          <div className="flex flex-col min-h-0">
            <PipelineFeed events={events} status={status} datasetName={datasetName} />
          </div>

          {/* Col 5: AG-UI event stream */}
          <div className="flex flex-col rounded-xl bg-gray-900 border border-gray-700 p-3 min-h-0">
            <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-2">
              Event Stream
            </p>
            <EventTimeline events={events} />
          </div>

        </div>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <div className="text-center text-gray-700 text-xs flex-shrink-0">
          W&amp;B Weave · OpenAI Agents SDK · Redis · CopilotKit AG-UI
        </div>

      </div>
    </div>
  );
}
