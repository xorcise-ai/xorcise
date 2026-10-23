import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { ResultConditions } from "@/lib/api/types";
import { ConditionsCard } from "./conditions-card";

// Full ResultConditions with everything empty/neutral; each test overrides just the field it asserts.
const base: ResultConditions = {
  model: null,
  judge_model: null,
  budget_seconds: 0,
  sandbox_ref: null,
  agent_version: 1,
  install_revision: 1,
  intel_disclosed: 0,
};

describe("ConditionsCard", () => {
  it("surfaces the disclosed-intel count when intel was disclosed", () => {
    renderWithProviders(
      <ConditionsCard conditions={{ ...base, model: "gpt-x", intel_disclosed: 3 }} />,
    );
    expect(screen.getByText("Intel disclosed")).toBeInTheDocument();
    expect(screen.getByText("3 intel")).toBeInTheDocument();
  });

  it("reads the same for exactly one disclosed intel (intel is a mass noun)", () => {
    renderWithProviders(
      <ConditionsCard conditions={{ ...base, model: "gpt-x", intel_disclosed: 1 }} />,
    );
    expect(screen.getByText("1 intel")).toBeInTheDocument();
  });

  it("omits the intel row when none were disclosed", () => {
    renderWithProviders(
      <ConditionsCard conditions={{ ...base, model: "gpt-x", intel_disclosed: 0 }} />,
    );
    // The card still renders (model is set) but shows no intel row — unassisted runs stay clean.
    expect(screen.getByText("Agent model")).toBeInTheDocument();
    expect(screen.queryByText("Intel disclosed")).toBeNull();
  });

  it("renders nothing when no condition is meaningful", () => {
    const { container } = renderWithProviders(
      <ConditionsCard conditions={base} />,
    );
    expect(container.querySelector("*")).toBeNull();
  });
});

it("surfaces the executed platform when the run recorded one (§43-UX8)", () => {
  renderWithProviders(<ConditionsCard conditions={{ ...base, platform: "linux/amd64" }} />);
  expect(screen.getByText("Platform")).toBeInTheDocument();
  expect(screen.getByText("linux/amd64")).toBeInTheDocument();
});

describe("ConditionsCard agent model (#146)", () => {
  it("shows the server's Agent-model cell, so a model the harness reported is not hidden", () => {
    // Nobody declared a model, but the telemetry named one: the page used to show no model at all.
    renderWithProviders(
      <ConditionsCard conditions={base} agentModel="gpt-x (reported by the harness)" />,
    );
    expect(screen.getByText("Agent model")).toBeInTheDocument();
    // The qualifier travels with the name: a self-report must not read as an attestation.
    expect(screen.getByText("gpt-x (reported by the harness)")).toBeInTheDocument();
  });

  it("renders the server's words as they are, never its own version of them", () => {
    renderWithProviders(
      <ConditionsCard
        conditions={{ ...base, model: "m" }}
        agentModel="m (disclosed); telemetry named m-1"
      />,
    );
    expect(screen.getByText("m (disclosed); telemetry named m-1")).toBeInTheDocument();
    expect(screen.queryByText("m")).toBeNull();
  });

  it("leaves the row out when the server has no model to name", () => {
    renderWithProviders(<ConditionsCard conditions={{ ...base, judge_model: "j" }} agentModel={null} />);
    expect(screen.queryByText("Agent model")).toBeNull();
    expect(screen.queryByText("not disclosed")).toBeNull();
  });

  it("falls back to the declared model when the server does not send the cell", () => {
    renderWithProviders(<ConditionsCard conditions={{ ...base, model: "gpt-x" }} />);
    expect(screen.getByText("gpt-x")).toBeInTheDocument();
  });
});
