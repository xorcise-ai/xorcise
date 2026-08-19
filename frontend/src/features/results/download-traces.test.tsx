import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { DownloadTraces } from "./download-traces";

describe("DownloadTraces", () => {
  it("links straight at the server's raw OTLP export endpoint", () => {
    render(<DownloadTraces runId="run-1" />);
    const link = screen.getByRole("link", { name: /OTLP Traces \(\.jsonl\)/ });
    // Plain anchor — no blob plumbing; Content-Disposition drives the filename.
    expect(link).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/run-1/otlp.jsonl`,
    );
    expect(link).toHaveAttribute("download");
  });

  it("encodes a run id that needs escaping", () => {
    render(<DownloadTraces runId="run/1 2" />);
    expect(
      screen.getByRole("link", { name: /OTLP Traces \(\.jsonl\)/ }),
    ).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/run%2F1%202/otlp.jsonl`,
    );
  });
});
