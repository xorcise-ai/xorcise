import type { Metadata } from "next";
import { Suspense } from "react";
import { NewRunFromQuery } from "./new-run-from-query";

export const metadata: Metadata = { title: "New run" };

export default function NewRunPage() {
  return (
    <Suspense>
      <NewRunFromQuery />
    </Suspense>
  );
}
