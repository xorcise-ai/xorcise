import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Slider } from "./slider";

describe("Slider", () => {
  it("renders a labelled range input at the given value", () => {
    render(
      <Slider ariaLabel="Budget" min={5} max={90} step={5} value={10} onChange={vi.fn()} />,
    );
    const el = screen.getByRole("slider", { name: "Budget" });
    expect(el).toHaveValue("10");
  });

  it("reports the new numeric value on change", () => {
    const onChange = vi.fn();
    render(
      <Slider ariaLabel="Budget" min={5} max={90} step={5} value={10} onChange={onChange} />,
    );
    fireEvent.change(screen.getByRole("slider", { name: "Budget" }), {
      target: { value: "15" },
    });
    expect(onChange).toHaveBeenCalledWith(15);
  });
});
