"use client";

import { A2UIRenderer, useA2UI } from "@copilotkit/a2ui-renderer";

export function TrainingVerdictCard({ sendAction: _ }: { sendAction?: unknown }) {
  const { getSurface } = useA2UI();
  if (!getSurface("training-verdict")) return null;

  return (
    <div className="rounded-xl border-2 border-yellow-500/70 bg-gray-950 p-3 shadow-lg shadow-yellow-500/10">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-yellow-400 text-sm">⚠</span>
        <p className="text-xs text-yellow-300 font-bold uppercase tracking-widest">
          Training Monitor Verdict
        </p>
      </div>
      <A2UIRenderer surfaceId="training-verdict" fallback={null} />
    </div>
  );
}
