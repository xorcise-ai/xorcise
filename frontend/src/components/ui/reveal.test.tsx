import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Reveal } from "./reveal";

describe("Reveal", () => {
  it("renders children with the fade-up entrance", () => {
    const { container } = render(<Reveal>hello</Reveal>);
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(container.firstElementChild!.className).toContain("animate-fade-up");
  });

  it("applies a capped stagger delay", () => {
    const { container } = render(<Reveal delay={1000}>x</Reveal>);
    expect((container.firstElementChild as HTMLElement).style.animationDelay).toBe(
      "400ms",
    );
  });

  it("sets no inline delay when delay is 0", () => {
    const { container } = render(<Reveal>x</Reveal>);
    expect((container.firstElementChild as HTMLElement).style.animationDelay).toBe("");
  });

  it("merges caller style with computed delay", () => {
    const { container } = render(
      <Reveal delay={80} style={{ color: "red" }}>
        x
      </Reveal>,
    );
    const element = container.firstElementChild as HTMLElement;
    expect(element.style.animationDelay).toBe("80ms");
    expect(element.style.color).toBe("red");
  });
});
