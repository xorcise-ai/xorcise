import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { DownloadReport } from "./download-report";

describe("DownloadReport", () => {
  it("links straight at the server's report endpoint for both formats", () => {
    render(<DownloadReport runId="run-1" />);
    const md = screen.getByRole("link", { name: /Report \(\.md\)/ });
    const html = screen.getByRole("link", { name: /Report \(\.html\)/ });
    // Plain anchors — no blob plumbing; Content-Disposition drives the filename.
    expect(md).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/run-1/report?format=md`,
    );
    expect(html).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/run-1/report?format=html`,
    );
    expect(md).toHaveAttribute("download");
    expect(html).toHaveAttribute("download");
  });

  it("encodes a run id that needs escaping", () => {
    render(<DownloadReport runId="run/1 2" />);
    expect(screen.getByRole("link", { name: /Report \(\.md\)/ })).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/run%2F1%202/report?format=md`,
    );
  });

  it("renders nothing while the run is still grading", () => {
    render(<DownloadReport runId="run-1" disabled />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
