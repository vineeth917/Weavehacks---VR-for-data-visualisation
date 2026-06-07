"use client";

import { A2UIRenderer, useA2UI } from "@copilotkit/a2ui-renderer";

export function TrainingVerdictCard({ sendAction: _ }: { sendAction?: unknown }) {
  const { getSurface } = useA2UI();
  if (!getSurface("training-verdict")) return null;

  return (
    <div className="rounded-xl bg-gray-900 border border-yellow-500/40 p-3">
      <p className="text-xs text-yellow-400 font-semibold uppercase tracking-wider mb-2">
        Training Monitor verdict
      </p>
      <A2UIRenderer surfaceId="training-verdict" fallback={null} />
    </div>
  );
}
