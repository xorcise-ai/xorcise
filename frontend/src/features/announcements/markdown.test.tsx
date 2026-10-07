import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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

import {
  GRAMMAR,
  InvalidAnnouncementBody,
  REASON_CODES,
  URL_HTTPS,
  URL_PATH,
  normalizeBody,
  renderAnnouncementMarkdown,
  tokenize,
  type Block,
  type Inline,
} from "./markdown";

/**
 * Conformance tests for the `xorcise-announcement-md/1` grammar.
 *
 * An announcement body is parsed THREE times by three separate implementations: the Python
 * tokenizer in the remote service (the strict validator, run before a row is written) and two
 * lenient TypeScript renderers — the admin console's and this one. Nothing in any type system
 * connects them, so the only thing keeping the three honest is a shared corpus of vectors.
 *
 * `announcement-markdown.vectors.json` is that corpus, and a BYTE-IDENTICAL copy of it is
 * meant to live in the other two repos.
 *
 * What the pin below actually proves, which is narrower than it reads: editing THIS copy —
 * even reformatting it — changes the digest and fails THIS suite, so the corpus cannot drift
 * from the digest written beside it. It proves nothing about the other two copies, which this
 * repo cannot see; `fixture_version` is the only cross-repo signal, and keeping the three in
 * step is the procedure in the fixture's own `_comment`, not a check. To change the grammar
 * deliberately: bump `fixture_version`, regenerate, update `ANNOUNCEMENT_VECTORS_SHA256` in
 * all three suites and copy the file across in one change.
 *
 * Note the calling convention the vectors assume: `input` is RAW (it may contain CRLF or be
 * blank), so a vector is parsed as `tokenize(normalizeBody(input))`, never `tokenize(input)`.
 *
 * The file is pure ASCII on purpose. The one bidi control character it contains (the U+202E
 * in the `bidi` vector) is written as the six-character JSON escape `‮`, because a raw
 * U+202E makes GitHub flag the file as bidirectional text and a reviewer should not have to
 * work out whether the fixture is hostile. `JSON.parse` decodes it to the same string Python
 * sees, so nothing about the vector changes — but any regeneration must repeat that step or
 * the digest moves.
 */

const VECTORS_PATH = join(__dirname, "announcement-markdown.vectors.json");

// The pin, over the copy in this repo. The other two suites are expected to assert the same
// digest over the same bytes; version 2 adds the `url_userinfo` vector and has not reached
// them yet.
const ANNOUNCEMENT_VECTORS_SHA256 =
  "33d9e76eca219b95de3e9a81fbeed9ace6626a2b091189348c6a781f932abc63";

interface Vector {
  id: string;
  input: string;
  /** Multiplier applied to `input` — how a 601-character body fits in a readable fixture. */
  repeat?: number;
  expect: "ok" | "reject";
  tokens?: Block[];
  code?: string;
}

const FIXTURE = JSON.parse(readFileSync(VECTORS_PATH, "utf8")) as {
  fixture_version: number;
  grammar: string;
  reason_codes: string[];
  vectors: Vector[];
};

/** A vector's raw input, with the optional `repeat` multiplier applied. */
const text = (v: Vector) => v.input.repeat(v.repeat ?? 1);

const okVectors = FIXTURE.vectors.filter((v) => v.expect === "ok");
const rejectVectors = FIXTURE.vectors.filter((v) => v.expect === "reject");

describe("the shared vector fixture", () => {
  it("matches the digest pinned beside it", () => {
    expect(FIXTURE.fixture_version).toBe(2);
    expect(FIXTURE.grammar).toBe(GRAMMAR);
    const digest = createHash("sha256").update(readFileSync(VECTORS_PATH)).digest("hex");
    expect(
      digest,
      "the shared vector file changed; update ANNOUNCEMENT_VECTORS_SHA256 in ALL THREE " +
        "suites and copy the file to the other repos in the same change",
    ).toBe(ANNOUNCEMENT_VECTORS_SHA256);
  });

  it("declares exactly the reason codes this module can raise", () => {
    expect(FIXTURE.reason_codes, "reason_codes must be sorted").toEqual(
      [...FIXTURE.reason_codes].sort(),
    );
    expect(new Set(FIXTURE.reason_codes)).toEqual(REASON_CODES);
  });

  it("exercises every reason code with at least one vector", () => {
    expect(new Set(rejectVectors.map((v) => v.code))).toEqual(REASON_CODES);
  });
});

describe("tokenize", () => {
  it.each(okVectors.map((v) => [v.id, v] as const))(
    "%s tokenizes to the pinned tree",
    (_id, vector) => {
      expect(tokenize(normalizeBody(text(vector)))).toEqual(vector.tokens);
    },
  );

  it.each(rejectVectors.map((v) => [v.id, v] as const))(
    "%s is refused with the pinned code",
    (_id, vector) => {
      let thrown: unknown;
      try {
        tokenize(normalizeBody(text(vector)));
      } catch (err) {
        thrown = err;
      }
      expect(thrown).toBeInstanceOf(InvalidAnnouncementBody);
      expect((thrown as InvalidAnnouncementBody).code).toBe(vector.code);
    },
  );

  it("produces a tree that survives a JSON round trip", () => {
    // Plain arrays, never objects or class instances: the tree is compared against the JSON
    // fixture on all three sides, and only a JSON-shaped tree can be.
    for (const vector of okVectors) {
      const tree = tokenize(normalizeBody(text(vector)));
      expect(JSON.parse(JSON.stringify(tree))).toEqual(tree);
    }
  });
});

/* --- the four worked traces the grammar spec calls out -----------------------
 *
 * Asserted directly, not only through the fixture, because they are the cases where a
 * plausible-looking reimplementation diverges: the emphasis rules are resolved by a
 * left-to-right scan with no backtracking, and the contents of `b`/`i`/`c`/link-text are
 * literal. */

describe("the emphasis scan", () => {
  it("resolves ** then * left to right without backtracking", () => {
    // strong fails (no closing `**`); em at i=0 fails (empty content) so `*` is literal;
    // em at i=1 closes at i=3 with content "a".
    expect(tokenize("**a*")).toEqual([["p", [["t", "*"], ["i", "a"]]]]);
  });

  it("leaves arithmetic stars literal", () => {
    expect(tokenize("5 * 3 = 15 and 2*x")).toEqual([
      ["p", [["t", "5 * 3 = 15 and 2*x"]]],
    ]);
  });

  it("treats strong content as literal, not as nested markup", () => {
    expect(tokenize("**See [docs](/d)**")).toEqual([["p", [["b", "See [docs](/d)"]]]]);
  });

  it("tries code before strong", () => {
    expect(tokenize("`**not bold**`")).toEqual([["p", [["c", "**not bold**"]]]]);
  });
});

/* --- the anchor safety invariant ---------------------------------------------
 *
 * The per-vector tests pin WHAT each input does. This pins WHY the set of them is sufficient,
 * and it is the defence in depth this renderer owes the app it renders inside:
 *
 *   an `["a", ...]` token can only ever carry a url matching URL_HTTPS or URL_PATH, and its
 *   kind is "ext" exactly when the url is https.
 *
 * A hostile url reaches that guarantee by one of two routes and the invariant does not care
 * which: either the url matches the link regex and is REFUSED on the scheme, or it never
 * matches the link regex at all and survives as literal text — `javascript:alert(1)` takes the
 * second route, because the url group excludes parentheses. Either way no anchor is produced,
 * which is the only thing the renderer below can rely on. */

const SAFE_LINK_PROBES = [
  "[x](https://ok.example/a)",
  "[y](/settings)",
  "[z](/)",
];

const HOSTILE_LINK_PROBES = [
  "[x](javascript:alert(1))",
  "[x](https://xorcise.ai@evil.example/)",
  "[x](javascript:alert)",
  "[x](JavaScript:alert)",
  "[x](data:text/html;base64,PHNjcmlwdD4=)",
  "[x](vbscript:msgbox)",
  "[x](file:///etc/passwd)",
  "[x](//evil.example)",
  "[x](http://example.com)",
  "[x](HTTPS://Evil.example)",
  "[a](https://x.y/%22onmouseover=alert(1))",
  "[b](  https://x.y  )",
];

/** Every `["a", ...]` token anywhere in a token tree. */
function anchors(tree: Block[]): Inline[] {
  const found: Inline[] = [];
  for (const [kind, payload] of tree) {
    const runs: Inline[][] = kind === "p" ? [payload as Inline[]] : (payload as Inline[][]);
    for (const run of runs) found.push(...run.filter((token) => token[0] === "a"));
  }
  return found;
}

describe("the anchor safety invariant", () => {
  it.each([...SAFE_LINK_PROBES, ...HOSTILE_LINK_PROBES])(
    "%s can only ever produce an https:// or /path anchor",
    (probe) => {
      let tree: Block[];
      try {
        tree = tokenize(normalizeBody(probe));
      } catch (err) {
        expect(err).toBeInstanceOf(InvalidAnnouncementBody);
        return; // refused outright is the strongest possible pass
      }
      for (const token of anchors(tree)) {
        const [, , url, kind] = token as ["a", string, string, "ext" | "int"];
        expect(
          URL_HTTPS.test(url) || URL_PATH.test(url),
          `unsafe url survived: ${JSON.stringify(url)}`,
        ).toBe(true);
        expect(kind === "ext", `kind ${kind} disagrees with the url shape for ${url}`).toBe(
          URL_HTTPS.test(url),
        );
      }
    },
  );

  it("really does produce anchors for the safe probes", () => {
    // Non-vacuity guard: without this, a tokenizer that emitted NO anchors at all would
    // satisfy the invariant above perfectly.
    for (const probe of SAFE_LINK_PROBES) {
      expect(anchors(tokenize(normalizeBody(probe))), probe).toHaveLength(1);
    }
    const kinds = SAFE_LINK_PROBES.map((p) => anchors(tokenize(normalizeBody(p)))[0][3]);
    expect(kinds).toEqual(["ext", "int", "int"]);
  });
});

/* --- the renderer ------------------------------------------------------------ */

/** The complete set of elements this renderer is allowed to emit. */
const ALLOWED_ELEMENTS = ["P", "UL", "LI", "STRONG", "EM", "CODE", "A", "BR"];

describe("renderAnnouncementMarkdown", () => {
  it("never renders an element outside the eight the grammar names", () => {
    // The security property, checked over the whole corpus rather than case by case. The
    // renderer chooses elements from a fixed list and there is no dangerouslySetInnerHTML
    // anywhere in this codebase — this is what asserts that stayed true.
    const seen = new Set<string>();
    for (const vector of okVectors) {
      const { container, unmount } = render(
        <div>{renderAnnouncementMarkdown(text(vector))}</div>,
      );
      container.querySelectorAll("*").forEach((el) => {
        if (el !== container.firstChild) seen.add(el.tagName);
      });
      unmount();
    }
    expect([...seen].sort()).toEqual(
      [...seen].filter((tag) => ALLOWED_ELEMENTS.includes(tag)).sort(),
    );
    // …and the corpus really does exercise the interesting ones, so the assertion above is
    // not passing over an empty set.
    expect(seen).toContain("A");
    expect(seen).toContain("CODE");
    expect(seen).toContain("UL");
  });

  it("opens an https link in a new tab with a full rel, and routes a /path link", () => {
    const { container } = render(
      <div>{renderAnnouncementMarkdown("See [docs](https://x.y/a) or [Settings](/settings).")}</div>,
    );
    const [external, internal] = [...container.querySelectorAll("a")];
    expect(external).toHaveAttribute("href", "https://x.y/a");
    expect(external).toHaveAttribute("target", "_blank");
    expect(external).toHaveAttribute("rel", "noopener noreferrer");
    // The internal one goes through next/link (mocked to a bare <a> here) precisely so the
    // real router applies the /ui basePath; it must NOT carry target="_blank".
    expect(internal).toHaveAttribute("href", "/settings");
    expect(internal).not.toHaveAttribute("target");
  });

  it("renders syntax the grammar refuses as literal text, never as markup", () => {
    const { container } = render(
      <div>{renderAnnouncementMarkdown("Click <b>here</b> ![x](https://a.b/c.png)")}</div>,
    );
    // The body is refused wholesale, so it falls back to literal text. The point is what is
    // absent: no <b>, no <img>, and the source visible verbatim.
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("Click <b>here</b> ![x](https://a.b/c.png)");
  });

  it("wraps a 300-character unbroken token instead of forcing horizontal overflow", () => {
    // jsdom has no layout engine, so this pins the MECHANISM rather than the pixels: the
    // container the banner puts this in declares `break-words` (overflow-wrap: anywhere) and
    // `min-w-0` (so it may shrink below its content inside the banner's flex row), and the
    // renderer emits nothing — no <pre>, no whitespace-nowrap — that would defeat either. The
    // e2e spec is where a real browser measures it.
    const token = "A".repeat(300);
    const { container } = render(
      <div className="min-w-0 break-words prose-block text-body">
        {renderAnnouncementMarkdown(token)}
      </div>,
    );
    expect(container.firstElementChild).toHaveClass("break-words", "min-w-0");
    expect(container.querySelector("p")?.textContent).toBe(token);
    expect(container.querySelector("pre")).toBeNull();
    for (const el of container.querySelectorAll("*")) {
      expect(el.className).not.toMatch(/whitespace-(pre|nowrap)|\bwhitespace-pre\b/);
    }
  });

  it("keeps the author's lines when it falls back to literal text", () => {
    // The fallback is the whole body as text, and a banner is two or three lines of prose. A
    // bare <p> collapses the newlines, so an outage notice arrived as one run-on sentence —
    // in the fallback of all places, where the reader already has the least help.
    const { container } = render(
      <div>{renderAnnouncementMarkdown("Status: <b>down</b>\nETA: 15:00")}</div>,
    );
    const p = container.querySelector("p");
    expect(container.querySelector("b")).toBeNull();
    expect(p?.innerHTML).toBe("Status: &lt;b&gt;down&lt;/b&gt;<br>ETA: 15:00");
    expect(p?.querySelectorAll("br")).toHaveLength(1);
  });

  it("keeps an author's line break rather than reflowing the banner", () => {
    const { container } = render(
      <div>{renderAnnouncementMarkdown("Line one\nLine two")}</div>,
    );
    expect(container.querySelectorAll("br")).toHaveLength(1);
    expect(container.querySelectorAll("p")).toHaveLength(1);
  });
});

/* --- userinfo: a url that says one host and goes to another --------------------- */

describe("userinfo in an https url", () => {
  it("is refused, because the host is what the reader cannot see", () => {
    // In `https://xorcise.ai@evil.example/` everything before the `@` is USERINFO, not a
    // host: the anchor reads as xorcise.ai and navigates to evil.example. An announcement is
    // published centrally and read by people who trust the app it appears in, so a link that
    // lies about where it goes is the one url bug that matters in this grammar.
    const probe = "[click here](https://xorcise.ai@evil.example/)";
    const { container } = render(<div>{renderAnnouncementMarkdown(probe)}</div>);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe(probe);
    expect(URL_HTTPS.test("https://xorcise.ai@evil.example/")).toBe(false);
    expect(URL_HTTPS.test("https://xorcise.ai@evil.example")).toBe(false);
  });

  it("is still allowed once the path has started, where it cannot be a host", () => {
    // Non-vacuity: the rule is about the AUTHORITY, not about the character. `@` is ordinary
    // in a path or a query and refusing it everywhere would break real links.
    expect(URL_HTTPS.test("https://xorcise.ai/u/@guru")).toBe(true);
    expect(URL_HTTPS.test("https://xorcise.ai/?to=a@b")).toBe(true);
    expect(URL_HTTPS.test("https://xorcise.ai#a@b")).toBe(true);
    expect(URL_HTTPS.test("https://docs.xorcise.ai/guide")).toBe(true);
    expect(URL_PATH.test("/u/@guru")).toBe(true);
  });
});

/* --- the renderer defends itself ------------------------------------------------
 *
 * This file's header presents the renderer as the safety boundary, so it has to hold on its
 * own input rather than on the publisher's promise about it. */

// The pre-change rstrip, kept verbatim as an ORACLE: the replacement has to agree with it on
// every character, because the shared fixture pins token trees and a strip that differed by
// one code point would move them.
const ORACLE_WS =
  "\\t\\n\\v\\f\\r\\x1C-\\x1F\\x20\\x85\\xA0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000";
const ORACLE_STRIP_END = new RegExp(`[${ORACLE_WS}]+$`);

function oracleNormalizeBody(md: string): string {
  const text = md.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = text.split("\n").map((line) => line.replace(ORACLE_STRIP_END, ""));
  let start = 0;
  let end = lines.length;
  while (start < end && !lines[start]) start += 1;
  while (end > start && !lines[end - 1]) end -= 1;
  const collapsed: string[] = [];
  for (const line of lines.slice(start, end)) {
    if (!line && collapsed.length && !collapsed[collapsed.length - 1]) continue;
    collapsed.push(line);
  }
  return collapsed.join("\n");
}

const WS_CHARS = [
  "\t", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x1f", " ", "\x85", "\xa0",
  " ", " ", " ", " ", " ", " ", " ", " ", "　",
];
// Neither of these is whitespace to Python's `str.strip()`, and both are to JavaScript's
// `trim()` or `\s` — they are the reason the character class is spelled out at all.
const NOT_WS_CHARS = ["﻿", "​"];

describe("normalizeBody", () => {
  it("strips exactly what the regex form stripped", () => {
    const corpus: string[] = ["", "\n", "   ", "a", "a b"];
    for (const ws of [...WS_CHARS, ...NOT_WS_CHARS]) {
      corpus.push(`a${ws}`, `${ws}a`, `a${ws}${ws}b${ws}`, `${ws}`, `${ws}${ws}`);
      corpus.push(`one${ws}\ntwo${ws}\n\n\nthree${ws}`);
      corpus.push(`\r\n${ws}a${ws}\r\rb\r\n`);
    }
    for (const input of corpus) {
      expect(normalizeBody(input), JSON.stringify(input)).toBe(oracleNormalizeBody(input));
    }
  });

  it("normalises a pathological whitespace run in linear time", () => {
    // `[\s]+$` applied per line backtracks across the whole run at every start position:
    // "a" + " ".repeat(n) + "b" measured 46 ms at n=10 000, 4.3 s at 100 000 and 37 s at
    // 300 000. The 600-character cap makes that unreachable from a published announcement,
    // but the cap is checked INSIDE tokenize, which normalizeBody runs before — so on this
    // path the renderer was relying on a promise made somewhere else.
    for (const filler of [" ", "\xa0"]) {
      const hostile = `a${filler.repeat(100_000)}b`;
      const started = performance.now();
      normalizeBody(hostile);
      const elapsed = performance.now() - started;
      expect(elapsed, `${JSON.stringify(filler)} took ${elapsed.toFixed(0)} ms`).toBeLessThan(
        1000,
      );
    }
  });

  it("lets renderAnnouncementMarkdown refuse the same body promptly", () => {
    // The whole public path, not just the helper: over the cap, so the answer is the literal
    // fallback — the point is that it ARRIVES.
    const started = performance.now();
    renderAnnouncementMarkdown(`a${" ".repeat(100_000)}b`);
    expect(performance.now() - started).toBeLessThan(1000);
  });
});
