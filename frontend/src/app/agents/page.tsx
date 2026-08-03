import type { Metadata } from "next";
import { AgentList } from "@/features/agents/agent-list";

export const metadata: Metadata = { title: "Agents" };

export default function AgentsPage() {
  return <AgentList />;
}
