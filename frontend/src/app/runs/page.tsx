import type { Metadata } from "next";
import { RunList } from "@/features/runs/run-list";

export const metadata: Metadata = { title: "Run history" };

export default function RunsPage() {
  return <RunList />;
}
