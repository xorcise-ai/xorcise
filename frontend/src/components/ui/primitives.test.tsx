import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Button } from "./button";
import { Badge } from "./badge";
import { Dialog } from "./dialog";

describe("ui primitives", () => {
  it("Button applies the variant + size classes", () => {
    render(<Button variant="outline" size="sm">Go</Button>);
    const btn = screen.getByRole("button", { name: "Go" });
    expect(btn.className).toContain("border");
    expect(btn.className).toContain("h-7");
  });

  it("Badge renders its content", () => {
    render(<Badge variant="ok">solved</Badge>);
    expect(screen.getByText("solved")).toBeInTheDocument();
  });

  it("Dialog shows content when open and closes on the X", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Dialog open={false} onClose={onClose} title="Register">
        <p>body</p>
      </Dialog>,
    );
    expect(screen.queryByText("body")).not.toBeInTheDocument();

    rerender(
      <Dialog open onClose={onClose} title="Register">
        <p>body</p>
      </Dialog>,
    );
    expect(screen.getByText("body")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
