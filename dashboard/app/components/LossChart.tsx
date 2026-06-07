"use client";

import { useEffect, useState, useRef } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  Legend,
} from "recharts";

interface HistoryPoint {
  step: number;
  epoch: number;
  train_loss: number;
  val_loss: number;
  train_acc: number;
  val_acc: number;
  [key: string]: unknown; // unknown future metrics must not crash
}

interface RunSummary {
  final_train_loss: number;
  final_val_loss: number;
  best_val_loss: number;
  best_val_loss_step: number;
  best_val_loss_epoch: number;
}

interface RunEntry {
  run_id?: string;
  config: {
    model: string;
    dataset: string;
    lr: number;
    batch_size: number;
    epochs: number;
    steps_per_epoch: number;
    mode_label?: string;
  };
  metrics: HistoryPoint[];
  summary: RunSummary;
}

// File is { "<run_id>": RunEntry, ... }
type ReplayFile = Record<string, RunEntry>;

// Custom tooltip so it looks sharp on the projector
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-xs">
      <p className="text-gray-400 mb-1">step {label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} style={{ color: p.color }}>
          {p.name}: {p.value.toFixed(4)}
        </p>
      ))}
    </div>
  );
}

interface LossChartProps {
  onStopRef?: React.MutableRefObject<(() => void) | null>;
}

export function LossChart({ onStopRef }: LossChartProps = {}) {
  const [allData, setAllData] = useState<HistoryPoint[]>([]);
  const [visibleCount, setVisibleCount] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [showVerdict, setShowVerdict] = useState(false);
  const [runId, setRunId] = useState<string>("");
  const [entry, setEntry] = useState<RunEntry | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetch("/replay_run_history.json")
      .then((r) => r.json())
      .then((data: ReplayFile) => {
        // Pick the overfit run for the demo; fall back to first run
        const id = Object.keys(data).find((k) => k.includes("overfit")) ?? Object.keys(data)[0];
        const run = data[id];
        setRunId(id);
        setEntry(run);
        setAllData(run.metrics);
      });
  }, []);

  const visibleData = allData.slice(0, visibleCount);
  const overfitStep = entry?.summary.best_val_loss_step ?? 90;
  const overfitVisible =
    visibleData.length > 0 && visibleData[visibleData.length - 1].step >= overfitStep;

  function play() {
    if (isPlaying) return;
    setVisibleCount(0);
    setShowVerdict(false);
    setIsPlaying(true);

    let i = 0;
    timerRef.current = setInterval(() => {
      i++;
      setVisibleCount(i);
      if (i >= allData.length) {
        clearInterval(timerRef.current!);
        setIsPlaying(false);
        setTimeout(() => setShowVerdict(true), 600);
      }
    }, 140);
  }

  function stopTraining() {
    if (timerRef.current) clearInterval(timerRef.current);
    setIsPlaying(false);
    setStopped(true);
    setShowVerdict(true);
  }

  function reset() {
    if (timerRef.current) clearInterval(timerRef.current);
    setIsPlaying(false);
    setStopped(false);
    setVisibleCount(0);
    setShowVerdict(false);
  }

  // Expose stopTraining to parent via ref
  useEffect(() => {
    if (onStopRef) onStopRef.current = stopTraining;
  });

  // Auto-play on data load
  useEffect(() => {
    if (allData.length > 0) {
      const t = setTimeout(() => play(), 1200);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allData.length]);

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-700 p-4 flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider">
            Training Monitor
          </p>
          {entry && (
            <p className="text-gray-400 text-xs mt-0.5">
              {runId} · {entry.config.model} · {entry.config.dataset}
              {entry.config.mode_label && (
                <span className="ml-1 text-yellow-500">({entry.config.mode_label})</span>
              )}
            </p>
          )}
        </div>
        <div className="flex gap-2 items-center">
          {stopped && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-red-500/20 border border-red-500/40 text-red-400">
              ■ stopped
            </span>
          )}
          <button
            onClick={play}
            disabled={isPlaying || stopped}
            className="text-xs px-3 py-1.5 rounded-lg bg-yellow-500/10 border border-yellow-500/40 text-yellow-400 hover:bg-yellow-500/20 disabled:opacity-40 transition-colors"
          >
            {isPlaying ? "streaming..." : "▶ replay"}
          </button>
          <button
            onClick={reset}
            className="text-xs px-3 py-1.5 rounded-lg bg-gray-800 border border-gray-600 text-gray-400 hover:bg-gray-700 transition-colors"
          >
            ↺
          </button>
        </div>
      </div>

      {/* Chart */}
      <div style={{ height: 200 }}>
        {visibleData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-gray-600 text-sm">
            Waiting for training data...
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={visibleData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis
                dataKey="step"
                tick={{ fill: "#6b7280", fontSize: 10 }}
                tickLine={false}
                label={{ value: "step", position: "insideBottomRight", offset: 0, fill: "#4b5563", fontSize: 10 }}
              />
              <YAxis
                tick={{ fill: "#6b7280", fontSize: 10 }}
                tickLine={false}
                domain={["auto", "auto"]}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: 11, color: "#9ca3af" }}
                iconType="circle"
                iconSize={8}
              />

              {/* Overfitting reference line */}
              {overfitVisible && (
                <ReferenceLine
                  x={overfitStep}
                  stroke="#f59e0b"
                  strokeDasharray="4 2"
                  label={{
                    value: "overfit →",
                    position: "top",
                    fill: "#f59e0b",
                    fontSize: 10,
                  }}
                />
              )}

              <Line
                type="monotone"
                dataKey="train_loss"
                name="train loss"
                stroke="#6366f1"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="val_loss"
                name="val loss"
                stroke="#f43f5e"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Verdict banner */}
      {showVerdict && entry?.summary && (
        <div className="rounded-lg bg-yellow-500/10 border border-yellow-500/30 px-3 py-2 flex items-start gap-2">
          <span className="text-yellow-400 text-sm mt-0.5">⚠</span>
          <div>
            <p className="text-yellow-300 text-xs font-semibold">
              Training Monitor verdict
            </p>
            <p className="text-yellow-200/80 text-xs mt-0.5">
              val_loss bottoms at {entry.summary.best_val_loss.toFixed(4)} @ step {entry.summary.best_val_loss_step} (epoch {entry.summary.best_val_loss_epoch}),
              then rises to {entry.summary.final_val_loss.toFixed(4)} — overfitting detected.
            </p>
          </div>
        </div>
      )}

      {/* Live step indicator */}
      {isPlaying && visibleData.length > 0 && (
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span
            className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse"
          />
          step {visibleData[visibleData.length - 1].step} ·{" "}
          train {visibleData[visibleData.length - 1].train_loss.toFixed(4)} ·{" "}
          val {visibleData[visibleData.length - 1].val_loss.toFixed(4)}
        </div>
      )}
    </div>
  );
}
