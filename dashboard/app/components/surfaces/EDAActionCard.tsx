"use client";

import { A2UIRenderer, useA2UI } from "@copilotkit/a2ui-renderer";

export function EDAActionCard({ sendAction: _ }: { sendAction?: unknown }) {
  const { getSurface } = useA2UI();
  if (!getSurface("eda-action")) return null;

  return (
    <div className="rounded-xl border-2 border-indigo-500/70 bg-gray-950 p-3 shadow-lg shadow-indigo-500/10">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
        <p className="text-xs text-indigo-300 font-bold uppercase tracking-widest">
          Agent Suggestion
        </p>
      </div>
      <A2UIRenderer surfaceId="eda-action" fallback={null} />
    </div>
  );
}
