import { useState } from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FilterBar } from "./filter-bar";
import { EMPTY_FILTERS, type MissionFilters } from "./filter-missions";

const SPECIALTIES = ["forensics", "pwn", "vulnerability-assessment", "web"];
const PROFICIENCIES = ["novice", "competent", "expert"];
const ENVIRONMENTS = ["lab", "static"];

/** Drives FilterBar as the catalog does, and exposes the live filter state to assert on. */
function Harness({ onState }: { onState?: (f: MissionFilters) => void }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  onState?.(filters);
  return (
    <FilterBar
      filters={filters}
      onChange={setFilters}
      specialties={SPECIALTIES}
      proficiencies={PROFICIENCIES}
      types={ENVIRONMENTS}
    />
  );
}

function openFacet(name: "Specialty" | "Proficiency" | "Environment") {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${name}`) }));
}

describe("FilterBar", () => {
  it("selects several specialties at once and echoes them as removable chips", () => {
    let latest = EMPTY_FILTERS;
    render(<Harness onState={(f) => (latest = f)} />);

    openFacet("Specialty");
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pwn" }));

    expect(latest.specialties).toEqual(["web", "pwn"]);
    // Both selections are checked in the panel …
    expect(screen.getByRole("checkbox", { name: "Web" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    // … and visible outside it as removable chips.
    expect(
      screen.getByRole("button", { name: "Remove filter Web" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove filter Pwn" }),
    ).toBeInTheDocument();
  });

  it("de-selects on a second click (selecting none means all)", () => {
    let latest = EMPTY_FILTERS;
    render(<Harness onState={(f) => (latest = f)} />);

    openFacet("Specialty");
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));

    expect(latest.specialties).toEqual([]);
    expect(
      screen.queryByRole("button", { name: "Remove filter Web" }),
    ).not.toBeInTheDocument();
  });

  it("removes one selection via its chip, keeping the rest", () => {
    let latest = EMPTY_FILTERS;
    render(<Harness onState={(f) => (latest = f)} />);

    openFacet("Specialty");
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Forensics" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove filter Web" }));

    expect(latest.specialties).toEqual(["forensics"]);
  });

  it("keeps specialty and proficiency independent, and clears both in one click", () => {
    let latest = EMPTY_FILTERS;
    render(<Harness onState={(f) => (latest = f)} />);

    openFacet("Specialty");
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));
    openFacet("Proficiency");
    fireEvent.click(screen.getByRole("checkbox", { name: "Novice" }));
    expect(latest.specialties).toEqual(["web"]);
    expect(latest.proficiencies).toEqual(["novice"]);

    fireEvent.click(screen.getByRole("button", { name: /Clear filters/i }));
    expect(latest).toEqual(EMPTY_FILTERS);
  });

  it("shows the selected count on the trigger and closes on Escape", () => {
    render(<Harness />);

    openFacet("Specialty");
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));
    expect(screen.getByRole("button", { name: /^Specialty/ })).toHaveTextContent(
      "· 1",
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByRole("checkbox", { name: "Web" }),
    ).not.toBeInTheDocument();
  });

  it("filters by environment (lab/static) and echoes the selection as a chip", () => {
    let latest = EMPTY_FILTERS;
    render(<Harness onState={(f) => (latest = f)} />);

    openFacet("Environment");
    fireEvent.click(screen.getByRole("checkbox", { name: "Lab" }));

    expect(latest.types).toEqual(["lab"]);
    expect(
      screen.getByRole("button", { name: "Remove filter Lab" }),
    ).toBeInTheDocument();
  });

  it("renders no facet trigger for a facet the catalog never populates", () => {
    render(
      <FilterBar
        filters={EMPTY_FILTERS}
        onChange={() => {}}
        specialties={SPECIALTIES}
        proficiencies={[]}
        types={[]}
      />,
    );
    expect(screen.getByRole("button", { name: /^Specialty/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Proficiency/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Environment/ }),
    ).not.toBeInTheDocument();
  });
});
