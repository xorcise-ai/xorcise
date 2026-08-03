import type { Metadata } from "next";
import { Suspense } from "react";
import { RegisterFromQuery } from "./register-from-query";

export const metadata: Metadata = { title: "Register agent" };

export default function RegisterAgentRoute() {
  return (
    <Suspense>
      <RegisterFromQuery />
    </Suspense>
  );
}
