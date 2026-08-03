import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Skeleton, SkeletonRows, SkeletonCardGrid } from "./skeleton";

describe("Skeleton", () => {
  it("shimmers and applies the variant size", () => {
    const { container } = render(<Skeleton variant="row" />);
    const el = container.firstElementChild!;
    expect(el.className).toContain("skeleton-shimmer");
    expect(el.className).toContain("h-6");
    expect(el.className).toContain("w-full");
  });

  it("defaults to an unsized block so callers can size via className", () => {
    const { container } = render(<Skeleton className="h-14 w-full" />);
    expect(container.firstElementChild!.className).toContain("h-14");
  });
});

describe("SkeletonRows", () => {
  it("renders the requested number of rows", () => {
    const { container } = render(<SkeletonRows count={5} />);
    expect(container.querySelectorAll(".skeleton-shimmer")).toHaveLength(5);
  });
});

describe("SkeletonCardGrid", () => {
  it("renders count cards each with a title and rows", () => {
    const { container } = render(<SkeletonCardGrid count={2} />);
    expect(container.firstElementChild!.children).toHaveLength(2);
  });
});
