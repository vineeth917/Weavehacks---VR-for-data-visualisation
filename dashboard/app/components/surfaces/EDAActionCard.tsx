"use client";

import { A2UIRenderer, useA2UI } from "@copilotkit/a2ui-renderer";

export function EDAActionCard({ sendAction: _ }: { sendAction: unknown }) {
  const { getSurface } = useA2UI();
  if (!getSurface("eda-action")) return null;

  return (
    <div className="rounded-xl bg-gray-900 border border-indigo-500/40 p-3">
      <p className="text-xs text-indigo-400 font-semibold uppercase tracking-wider mb-2">
        Agent suggests action
      </p>
      <A2UIRenderer surfaceId="eda-action" fallback={null} />
    </div>
  );
}
