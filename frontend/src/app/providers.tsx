"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { makeQueryClient } from "@/lib/api/query-client";

export function Providers({ children }: { children: ReactNode }) {
  // One client per browser session (lazy init so it isn't recreated on render).
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
