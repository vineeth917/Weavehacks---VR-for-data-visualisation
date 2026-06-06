"use client";

import { AguiEvent, agentColor } from "../hooks/useAguiStream";

interface Props {
  events: AguiEvent[];
}

const AGENTS = ["router", "eda", "training_monitor", "narrator"];
const POSITIONS: Record<string, { x: number; y: number }> = {
  router:           { x: 50,  y: 20 },
  eda:              { x: 20,  y: 65 },
  training_monitor: { x: 50,  y: 65 },
  narrator:         { x: 80,  y: 65 },
};

export function SwarmGraph({ events }: Props) {
  const handoffs = events.filter((e) => e.type === "HANDOFF" && e.to);
  const activeAgents = new Set(
    events
      .filter((e) => ["TOOL_CALL_START", "TEXT_MESSAGE_CONTENT"].includes(e.type))
      .map((e) => e.agent)
  );

  return (
    <div className="rounded-xl bg-gray-800/50 border border-gray-700 p-3">
      <p className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">
        Swarm Graph
      </p>
      <svg viewBox="0 0 100 90" className="w-full" style={{ height: 160 }}>
        {/* Static edges */}
        {[["router", "eda"], ["router", "training_monitor"], ["router", "narrator"]].map(
          ([from, to]) => {
            const f = POSITIONS[from];
            const t = POSITIONS[to];
            const wasHandedOff = handoffs.some((e) => e.agent === from && e.to === to);
            return (
              <line
                key={`${from}-${to}`}
                x1={f.x} y1={f.y} x2={t.x} y2={t.y}
                stroke={wasHandedOff ? agentColor(from) : "#374151"}
                strokeWidth={wasHandedOff ? 0.8 : 0.4}
                strokeDasharray={wasHandedOff ? "none" : "2,2"}
                opacity={wasHandedOff ? 0.9 : 0.4}
              />
            );
          }
        )}

        {/* Agent nodes */}
        {AGENTS.map((agent) => {
          const pos = POSITIONS[agent];
          const color = agentColor(agent);
          const isActive = activeAgents.has(agent);
          return (
            <g key={agent}>
              {isActive && (
                <circle
                  cx={pos.x} cy={pos.y} r={6}
                  fill={color} opacity={0.15}
                  className="animate-ping"
                />
              )}
              <circle
                cx={pos.x} cy={pos.y} r={4}
                fill={color + "33"}
                stroke={color}
                strokeWidth={0.8}
              />
              <text
                x={pos.x} y={pos.y + 9}
                textAnchor="middle"
                fontSize={4}
                fill={color}
                opacity={0.9}
              >
                {agent === "training_monitor" ? "train" : agent}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
