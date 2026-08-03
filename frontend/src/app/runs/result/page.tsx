import type { Metadata } from "next";
import { Suspense } from "react";
import { ResultFromQuery } from "./result-from-query";

export const metadata: Metadata = { title: "Run result" };

export default function RunResultPage() {
  return (
    <Suspense>
      <ResultFromQuery />
    </Suspense>
  );
}
