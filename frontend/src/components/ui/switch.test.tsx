import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Switch } from "./switch";

describe("Switch", () => {
  it("reflects checked state and toggles on click", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <Switch checked={false} onChange={onChange} label="Remote" />,
    );
    const sw = screen.getByRole("switch", { name: "Remote" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);

    rerender(<Switch checked onChange={onChange} label="Remote" />);
    expect(screen.getByRole("switch", { name: "Remote" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("does not fire when disabled", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} disabled label="R" />);
    fireEvent.click(screen.getByRole("switch", { name: "R" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
