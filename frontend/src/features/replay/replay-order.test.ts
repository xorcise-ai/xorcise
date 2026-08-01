import { describe, expect, it } from "vitest";
import { mergeAgentAndInfra } from "./replay-order";

interface Agent {
  id: string;
  receipt: string | null;
}

interface Infra {
  id: string;
  ts: string | null;
  seq: number;
}

const iso = (ms: number) => new Date(ms).toISOString();

function ids(agents: Agent[], infra: Infra[]): string[] {
  return mergeAgentAndInfra(agents, infra, {
    agentReceipt: (agent) => agent.receipt,
    infraTime: (row) => row.ts,
    infraSequence: (row) => row.seq,
  }).map((entry) => (entry.kind === "agent" ? entry.agent.id : entry.infra.id));
}

describe("mergeAgentAndInfra", () => {
  it("keeps ordinary receipt-bracketed infrastructure interleaving", () => {
    expect(
      ids(
        [
          { id: "agent-before", receipt: iso(1000) },
          { id: "agent-after", receipt: iso(3000) },
        ],
        [{ id: "infra", ts: iso(2000), seq: 0 }],
      ),
    ).toEqual(["agent-before", "infra", "agent-after"]);
  });

  it("uses receipt only one-way and never inverts the fixed agent chronology", () => {
    expect(
      ids(
        [
          { id: "delayed-prompt", receipt: iso(4000) },
          { id: "response", receipt: iso(1000) },
        ],
        [{ id: "infra", ts: iso(2000), seq: 0 }],
      ),
    ).toEqual(["delayed-prompt", "response", "infra"]);
  });

  it("keeps infra first on an exact receipt tie", () => {
    expect(
      ids(
        [{ id: "agent", receipt: iso(2000) }],
        [{ id: "infra", ts: iso(2000), seq: 0 }],
      ),
    ).toEqual(["infra", "agent"]);
  });

  it("orders anchorless infra last", () => {
    expect(
      ids(
        [{ id: "agent", receipt: iso(2000) }],
        [{ id: "infra", ts: null, seq: 0 }],
      ),
    ).toEqual(["agent", "infra"]);
  });
});
