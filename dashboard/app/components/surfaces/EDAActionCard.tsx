"use client";

import { A2UIRenderer, useA2UI } from "@copilotkit/a2ui-renderer";

interface Props {
  sendAction: (name: string, surfaceId: string, ctx: Record<string, unknown>) => void;
}

export function EDAActionCard({ sendAction }: Props) {
  const { getSurface } = useA2UI();
  const surface = getSurface("eda-action");

  // Don't mount until the surface exists
  if (!surface) return null;

  return (
    <div className="rounded-xl bg-gray-900 border border-indigo-500/40 p-3">
      <p className="text-xs text-indigo-400 font-semibold uppercase tracking-wider mb-2">
        Agent suggests action
      </p>
      <A2UIRenderer
        surfaceId="eda-action"
        fallback={null}
      />
      {/* Fallback manual buttons in case A2UI Button component doesn't fire correctly */}
      <div className="mt-2 flex gap-2" id="eda-action-manual-btns">
        <button
          onClick={() =>
            sendAction("confirm_transform", "eda-action", {
              answer: "yes",
              transform: "log",
            })
          }
          className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
        >
          Yes, apply
        </button>
        <button
          onClick={() =>
            sendAction("dismiss", "eda-action", { answer: "no" })
          }
          className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >
          Skip
        </button>
      </div>
    </div>
  );
}
