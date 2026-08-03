import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { highlightShell, highlightJson, renderAnsi, hasAnsi } from "./syntax";
import type { ReactNode } from "react";

const ESC = String.fromCharCode(27);
const textOf = (node: ReactNode): string => {
  const { container } = render(<pre>{node}</pre>);
  return container.textContent ?? "";
};
const spans = (node: ReactNode): HTMLElement[] => {
  const { container } = render(<pre>{node}</pre>);
  return [...container.querySelectorAll("span")] as HTMLElement[];
};

describe("highlightShell", () => {
  it("is presentational-only: preserves EVERY character of the input", () => {
    const code =
      'export BASE="http://x"\n# a comment\nfor i in 1 2; do echo $i; done\npython3 - <<' +
      "'PY'\nimport sys\nprint(1)\nPY";
    expect(textOf(highlightShell(code))).toBe(code);
  });

  it("colours comments, strings, variables and the command from the theme palette", () => {
    const nodes = highlightShell('# hi\necho "x" $VAR');
    const s = spans(nodes);
    const byText = (t: string) => s.find((el) => el.textContent === t);
    expect(byText("# hi")?.className).toContain("--syntax-comment");
    expect(byText('"x"')?.className).toContain("--syntax-string");
    expect(byText("$VAR")?.className).toContain("--syntax-variable");
    expect(byText("echo")?.className).toContain("--syntax-command"); // command-position word
  });

  it("colours a --flag and a number", () => {
    const s = spans(highlightShell("grep -n 42 file"));
    const byText = (t: string) => s.find((el) => el.textContent === t);
    expect(byText("-n")?.className).toContain("--syntax-flag");
    expect(byText("42")?.className).toContain("--syntax-number");
  });

  it("leaves a very large body as plain text (no tokenising blow-up)", () => {
    const big = "echo hi\n".repeat(10_000); // > 60k chars
    const out = highlightShell(big);
    expect(typeof out).toBe("string");
    expect(out).toBe(big);
  });

  it("never throws on unterminated quotes / lone $", () => {
    expect(() => highlightShell('echo "unterminated\n$ ${')).not.toThrow();
    expect(textOf(highlightShell('echo "unterminated\n$ ${'))).toBe('echo "unterminated\n$ ${');
  });
});

describe("highlightJson", () => {
  it("is presentational-only: preserves every character", () => {
    const json = '{\n  "command": "create",\n  "n": 42,\n  "ok": true,\n  "x": null\n}';
    expect(textOf(highlightJson(json))).toBe(json);
  });

  it("colours keys, string values, numbers and booleans distinctly", () => {
    const s = spans(highlightJson('{"command": "create", "n": 42, "ok": true}'));
    const byText = (t: string) => s.find((el) => el.textContent === t);
    expect(byText('"command"')?.className).toContain("--syntax-key"); // key
    expect(byText('"create"')?.className).toContain("--syntax-string"); // value
    expect(byText("42")?.className).toContain("--syntax-number");
    expect(byText("true")?.className).toContain("--syntax-boolean");
  });

  it("leaves a very large body as plain text", () => {
    const big = '{"k":"v"}\n'.repeat(8_000);
    expect(highlightJson(big)).toBe(big);
  });
});

describe("renderAnsi", () => {
  it("strips escapes and keeps exactly the visible text", () => {
    const t = `${ESC}[32mgreen${ESC}[0m ${ESC}[1;31mbold-red${ESC}[0m plain`;
    expect(textOf(renderAnsi(t))).toBe("green bold-red plain");
  });

  it("colours an SGR run with its terminal colour", () => {
    const s = spans(renderAnsi(`${ESC}[31mred${ESC}[0m`));
    const red = s.find((el) => el.textContent === "red");
    expect(red?.style.color).toBeTruthy();
  });

  it("returns plain text unchanged when there is no ANSI", () => {
    expect(textOf(renderAnsi("just program output\nline 2"))).toBe("just program output\nline 2");
  });

  it("strips a non-colour escape (e.g. bracketed paste) but keeps the text", () => {
    expect(textOf(renderAnsi(`${ESC}[?2004hhello${ESC}[?2004l`))).toBe("hello");
  });

  it("hasAnsi detects escape sequences", () => {
    expect(hasAnsi(`${ESC}[0m`)).toBe(true);
    expect(hasAnsi("nope")).toBe(false);
  });
});
