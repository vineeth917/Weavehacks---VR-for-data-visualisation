"use client";

import { useAguiStream } from "./hooks/useAguiStream";
import { useWsSpectator } from "./hooks/useWsSpectator";
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
  const { events: wsEvents, status: wsStatus } = useWsSpectator(BACKEND_URL);

  // Latest voice query from the Quest
  const lastQuery = [...wsEvents].reverse().find(
    (e) => e.type === "voice_query" && e.text !== "__spectator_ping__"
  );

  return (
    <div className="h-screen flex flex-col p-4 gap-3 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">
            HoloLab — Agent Swarm
          </h1>
          <p className="text-gray-500 text-xs mt-0.5">
            Live orchestration view · WeaveHacks 4
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Live Quest query banner */}
          {lastQuery && (
            <div className="flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/30 rounded-lg px-3 py-1.5">
              <span className="text-xs">🎤</span>
              <span className="text-xs text-indigo-300 max-w-xs truncate italic">
                "{lastQuery.text}"
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

      {/* Main grid: 5 columns */}
      <div className="flex-1 grid grid-cols-5 gap-3 min-h-0">

        {/* Col 1: Swarm graph + agent status */}
        <div className="flex flex-col gap-3 min-h-0">
          <SwarmGraph events={events} />
          <AgentStatusGrid events={events} />
        </div>

        {/* Col 2: Loss chart */}
        <div className="flex flex-col min-h-0">
          <LossChart />
        </div>

        {/* Col 3: A2UI agent-generated surfaces */}
        <div className="flex flex-col min-h-0 overflow-y-auto">
          <A2UIPanel backendUrl={BACKEND_URL} />
        </div>

        {/* Col 4: Live pipeline feed (from Quest WS) */}
        <div className="flex flex-col min-h-0">
          <PipelineFeed events={wsEvents} wsStatus={wsStatus} />
        </div>

        {/* Col 5: AG-UI event stream */}
        <div className="flex flex-col rounded-xl bg-gray-900 border border-gray-700 p-3 min-h-0">
          <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-2">
            Event Stream
          </p>
          <EventTimeline events={events} />
        </div>

      </div>

      {/* Footer */}
      <div className="text-center text-gray-700 text-xs">
        W&amp;B Weave · OpenAI Agents SDK · Redis · CopilotKit AG-UI
      </div>
    </div>
  );
}
