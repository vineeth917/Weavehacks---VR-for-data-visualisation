"use client";

import { A2UIRenderer } from "@copilotkit/a2ui-renderer";

interface Props {
  className?: string;
}

export function EDAFindingsCard({ className }: Props) {
  return (
    <div className={`rounded-xl border-2 border-emerald-500/70 bg-gray-950 p-3 shadow-lg shadow-emerald-500/10 ${className ?? ""}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className="w-2 h-2 rounded-full bg-emerald-400" />
        <p className="text-xs text-emerald-300 font-bold uppercase tracking-widest">
          EDA Findings
        </p>
      </div>
      <A2UIRenderer
        surfaceId="eda-findings"
        fallback={
          <p className="text-gray-500 text-xs italic">
            Waiting for EDA agent to profile dataset…
          </p>
        }
      />
    </div>
  );
}
