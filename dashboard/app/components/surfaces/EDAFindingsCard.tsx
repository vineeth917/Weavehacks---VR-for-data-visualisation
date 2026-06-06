"use client";

import { A2UIRenderer } from "@copilotkit/a2ui-renderer";

interface Props {
  className?: string;
}

export function EDAFindingsCard({ className }: Props) {
  return (
    <div className={`rounded-xl bg-gray-900 border border-emerald-500/30 p-3 ${className ?? ""}`}>
      <p className="text-xs text-emerald-400 font-semibold uppercase tracking-wider mb-2">
        EDA Findings
      </p>
      <A2UIRenderer
        surfaceId="eda-findings"
        fallback={
          <p className="text-gray-600 text-xs italic">
            Waiting for EDA agent to profile dataset…
          </p>
        }
      />
    </div>
  );
}
