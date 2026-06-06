"use client";

import { A2UIProvider, basicCatalog } from "@copilotkit/a2ui-renderer";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <A2UIProvider catalog={basicCatalog}>
      {children}
    </A2UIProvider>
  );
}
