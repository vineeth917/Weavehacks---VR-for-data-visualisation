"use client";

import { A2UIRenderer, useA2UI } from "@copilotkit/a2ui-renderer";

interface Props {
  sendAction: (name: string, surfaceId: string, ctx: Record<string, unknown>) => void;
  runId?: string;
}

export function TrainingVerdictCard({ sendAction, runId = "hololab-demo-run-001" }: Props) {
  const { getSurface } = useA2UI();
  const surface = getSurface("training-verdict");

  if (!surface) return null;

  return (
    <div className="rounded-xl bg-gray-900 border border-yellow-500/40 p-3">
      <p className="text-xs text-yellow-400 font-semibold uppercase tracking-wider mb-2">
        Training Monitor verdict
      </p>
      <A2UIRenderer
        surfaceId="training-verdict"
        fallback={null}
      />
      <div className="mt-2 flex gap-2">
        <button
          onClick={() =>
            sendAction("stop_training", "training-verdict", { run_id: runId })
          }
          className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white transition-colors"
        >
          Stop training
        </button>
        <button
          onClick={() =>
            sendAction("keep_training", "training-verdict", { run_id: runId })
          }
          className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >
          Keep going
        </button>
      </div>
    </div>
  );
}
