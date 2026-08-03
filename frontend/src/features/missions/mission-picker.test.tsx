import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { missionFixture } from "@/test/fixtures";
import { MissionPicker } from "./mission-picker";

describe("MissionPicker", () => {
  it("shows a loading indicator while the catalog is being fetched", () => {
    render(
      <MissionPicker
        missions={[]}
        selectedId={null}
        onSelect={vi.fn()}
        isLoading
      />,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      screen.queryByText(/No missions available/i),
    ).not.toBeInTheDocument();
  });

  it("shows the empty state once the fetch resolves with no missions", () => {
    render(
      <MissionPicker missions={[]} selectedId={null} onSelect={vi.fn()} />,
    );
    expect(screen.getByText(/No missions available/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("calls onSelect with the mission id when a row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <MissionPicker
        missions={[missionFixture({ mission_id: "sqli-login" })]}
        selectedId={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /SQLi Login Bypass/i }));
    expect(onSelect).toHaveBeenCalledWith("sqli-login");
  });

  it("populates the details panel for the selected mission", () => {
    render(
      <MissionPicker
        missions={[
          missionFixture({
            mission_id: "sqli-login",
            summary: "Bypass a login form via SQL injection.",
            proficiency: "beginner",
            specialty: "web",
          }),
        ]}
        selectedId="sqli-login"
        onSelect={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Bypass a login form via SQL injection."),
    ).toBeInTheDocument();
    expect(screen.getByText(/beginner/i)).toBeInTheDocument();
    expect(screen.getByText(/web/i)).toBeInTheDocument();
  });

  it("marks a not-installed mission as pulling on start with an explainer", () => {
    render(
      <MissionPicker
        missions={[
          missionFixture({ mission_id: "lib-one", installed: false }),
        ]}
        selectedId="lib-one"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/pulls on start/i)).toBeInTheDocument();
    expect(
      screen.getByText(/pulled automatically when the run starts/i),
    ).toBeInTheDocument();
  });

  it("shows the mission source (Your own vs Library)", () => {
    const { rerender } = render(
      <MissionPicker
        missions={[missionFixture({ mission_id: "own", source: "your_own" })]}
        selectedId="own"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Your own")).toBeInTheDocument();

    rerender(
      <MissionPicker
        missions={[missionFixture({ mission_id: "lib", source: "library" })]}
        selectedId="lib"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Library")).toBeInTheDocument();
  });
});
