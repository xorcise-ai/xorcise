import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CopyButton } from "./copy-button";

describe("CopyButton", () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("writes the text to the clipboard and flips to Copied", async () => {
    render(<CopyButton text="paste me" idleLabel="Copy prompt" />);
    const btn = screen.getByRole("button", { name: /copy prompt/i });
    fireEvent.click(btn);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("paste me");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument(),
    );
  });
});
