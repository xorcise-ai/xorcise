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
  mission_version: 1,
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
    expect(screen.getByText("Model")).toBeInTheDocument();
    expect(screen.queryByText("Intel disclosed")).toBeNull();
  });

  it("renders nothing when no condition is meaningful", () => {
    const { container } = renderWithProviders(
      <ConditionsCard conditions={base} />,
    );
    expect(container.querySelector("*")).toBeNull();
  });
});
