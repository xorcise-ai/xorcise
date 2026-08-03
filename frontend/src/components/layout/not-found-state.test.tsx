import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { NotFoundState } from "./not-found-state";

describe("NotFoundState", () => {
  it("renders a clean default not-found with a link home", () => {
    render(<NotFoundState />);
    expect(screen.getByText("404")).toBeInTheDocument();
    const back = screen.getByRole("link");
    expect(back).toHaveAttribute("href", "/");
  });

  it("supports a custom message and back target", () => {
    render(
      <NotFoundState
        message="Run not found."
        backHref="/runs"
        backLabel="Back to runs"
      />,
    );
    expect(screen.getByText("Run not found.")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/runs");
    expect(screen.getByText("Back to runs")).toBeInTheDocument();
  });
});
