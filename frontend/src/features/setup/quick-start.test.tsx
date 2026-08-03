import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { QuickStart, DOCS_URL } from "./quick-start";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

describe("QuickStart", () => {
  it("renders the three quick-start cards", () => {
    renderWithProviders(<QuickStart startHref="/runs/new" />);
    expect(screen.getByText("Start a Run")).toBeInTheDocument();
    expect(screen.getByText("Browse Missions")).toBeInTheDocument();
    expect(screen.getByText("Read Documentation")).toBeInTheDocument();
  });

  it("points 'Start a Run' at the state-aware startHref", () => {
    renderWithProviders(<QuickStart startHref="/agents" />);
    expect(screen.getByText("Start a Run").closest("a")).toHaveAttribute("href", "/agents");
    expect(screen.getByText("Browse Missions").closest("a")).toHaveAttribute(
      "href",
      "/missions",
    );
  });

  it("opens the docs in a new tab as an external link", () => {
    renderWithProviders(<QuickStart startHref="/runs/new" />);
    const docs = screen.getByText("Read Documentation").closest("a")!;
    expect(docs).toHaveAttribute("href", DOCS_URL);
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs.getAttribute("rel") ?? "").toContain("noopener");
  });
});
