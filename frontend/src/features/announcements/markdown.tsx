import Link from "next/link";
import type { ReactNode } from "react";

/**
 * The `xorcise-announcement-md/1` grammar — a deliberately tiny Markdown subset.
 *
 * This is the LENIENT end of a contract whose strict end is Python
 * (`xorcise_shared/announcement_markdown.py`, in the remote service). That module refuses a
 * body at publish time; this one is handed a body that module already accepted and renders
 * it. Anything it does not recognise becomes literal text — never dropped, never HTML.
 *
 * Why a hand-written parser and not a Markdown library: the banner renders inside somebody
 * else's page — this app — and there it is an image, a raw `<script>` or a `javascript:` URL
 * that matters, not a formatting quirk. Returning React nodes is what makes this safe without
 * a sanitiser: there is no `dangerouslySetInnerHTML` in this codebase and this file must not
 * introduce one. A string that reaches `renderAnnouncementMarkdown` cannot become an element
 * it did not name, because elements are chosen here, by this file, from a fixed list.
 *
 * Three implementations of this grammar exist — the Python validator, the admin console's
 * TypeScript renderer, and this one — and nothing in any type system connects them.
 * `announcement-markdown.vectors.json` is the agreement: a corpus of inputs and their exact
 * token trees, byte-identical in all three repos and pinned by SHA-256 in all three test
 * suites. Change a rule here without regenerating and re-pinning the fixture everywhere and
 * `markdown.test.tsx` fails, which is the whole point.
 *
 * Token tree (plain arrays, so it compares equal to the JSON fixture):
 *     block  := ["p", [inline, ...]] | ["ul", [[inline, ...], ...]]
 *     inline := ["t", str] | ["b", str] | ["i", str] | ["c", str]
 *             | ["a", text, url, "ext" | "int"] | ["br"]
 */

export const GRAMMAR = "xorcise-announcement-md/1";

export const MAX_BODY_CHARS = 600;
export const MAX_URL_CHARS = 300;
export const MAX_LIST_ITEMS = 5;
export const MAX_LINES = 12;

export type Inline =
  | ["t", string]
  | ["b", string]
  | ["i", string]
  | ["c", string]
  | ["a", string, string, "ext" | "int"]
  | ["br"];

export type Block = ["p", Inline[]] | ["ul", Inline[][]];

/**
 * Every code this port can raise. The fixture declares the same set and the test asserts the
 * two are equal, so a rule cannot be added on one side without a vector that exercises it.
 */
export const REASON_CODES: ReadonlySet<string> = new Set([
  "bidi_control",
  "blockquote",
  "bullet_marker",
  "code_block",
  "control_char",
  "empty",
  "entity",
  "footnote",
  "heading",
  "html",
  "image",
  "indented",
  "link_text_empty",
  "list_item_empty",
  "list_mixed",
  "list_too_long",
  "multiple_lists",
  "ordered_list",
  "reference_link",
  "rule",
  "table",
  "too_long",
  "too_many_lines",
  "url_invalid",
  "url_scheme",
  "url_too_long",
]);

/**
 * A body the grammar refuses.
 *
 * `code` is the stable, machine-readable reason (one of `REASON_CODES`) and is what the
 * vectors pin; `detail` is the offending fragment, for humans only — never assert on it.
 */
export class InvalidAnnouncementBody extends Error {
  readonly code: string;
  readonly detail: string;
  constructor(code: string, detail = "") {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "InvalidAnnouncementBody";
    this.code = code;
    this.detail = detail;
  }
}

/* --- two character classes JavaScript gets wrong, relative to Python ---------
 *
 * The rejection rules must agree with the Python module CHARACTER FOR CHARACTER, because a
 * disagreement shows up as a different reason code, and the admin API returns that code to
 * the console verbatim. Two of JS's shorthand classes do not match Python's:
 *
 *   `\s` — Python's (on `str`) is every code point where `str.isspace()` is true, which
 *          includes U+0085 NEL and excludes U+FEFF. JavaScript's is the reverse on both.
 *   `\d` — Python's is the whole Unicode Nd category, so `١. x` IS an ordered list to the
 *          validator. JavaScript's `\d` is ASCII-only, so a naive port would accept a body
 *          Python refuses. `\p{Nd}` (with the `u` flag) is the faithful equivalent.
 *
 * Spelling them out costs a `new RegExp` per pattern and buys the one property this file
 * exists to have. */
const WS = "\\t\\n\\v\\f\\r\\x1C-\\x1F\\x20\\x85\\xA0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000";
const S = `[${WS}]`;
const D = "\\p{Nd}";

const PY_STRIP = new RegExp(`^${S}+|${S}+$`, "g");
/** `str.rstrip()` — the trailing half, which is all `normalizeBody` wants. */
const PY_STRIP_END = new RegExp(`${S}+$`);

/** `str.strip()`, not `String.trim()` — see the note above on U+0085 and U+FEFF. */
function pyStrip(s: string): string {
  return s.replace(PY_STRIP, "");
}

/**
 * Code points, not UTF-16 units.
 *
 * `"🔥".length` is 2 and `[..."🔥"].length` is 1; Python's `len` gives 1. Using `.length`
 * here would let an emoji-heavy body through at half the real budget in the console and be
 * measured differently again here — the exact drift the shared fixture exists to catch.
 */
function codePoints(s: string): number {
  return [...s].length;
}

/* --- body-wide patterns ----------------------------------------------------- */
// Tabs are control characters here (\x09 is inside the range): a tab is invisible in the
// console textarea but shifts a line into `indented` territory in a renderer, so it is
// refused rather than silently normalised.
const CONTROL = /[\x00-\x09\x0B-\x1F\x7F]/;
// Bidi overrides and isolates: ALM, LRM, RLM, LRE..RLO, LRI..PDI. These can make a URL or a
// sentence read as its own reverse, which is a spoofing primitive in a banner nobody scrolls
// past.
const BIDI = /[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/;
const IMAGE = /!\[/;
const HTML = /<(?=[A-Za-z/!?])/;
const ENTITY = /&#?[A-Za-z0-9]+;/;
const FOOTNOTE = /\[\^/;
const REF_LINK = new RegExp(`\\]${S}*\\[`);

/* --- per-line patterns ------------------------------------------------------ */
const CODE_BLOCK = /^(```|~~~)/;
const HEADING = new RegExp(`^#{1,6}(${S}|$)`);
const BLOCKQUOTE = /^>/;
// A table gives itself away either by a row that starts with a pipe or by its delimiter row,
// which can be indented past one; both are the same rule, because the author's mistake
// ("I wrote a table") is the same in each case.
const TABLE = new RegExp(`^\\||\\|${S}*:?-{2,}:?${S}*\\|`);
// Covers ---, ***, ___, === , `- - -` and setext underlines in one rule. The line must be
// nothing but the repeated delimiter, so a one-item list `- Pulls` is NOT a rule: after the
// leading `-` the pattern needs another `-`, and gets `P`.
const RULE = new RegExp(`^([-*_=])(${S}*\\1){1,}${S}*$`);
const ORDERED_LIST = new RegExp(`^${D}+[.)]${S}`, "u");
const BULLET_MARKER = new RegExp(`^[*+]${S}`);
const LIST_ITEM_EMPTY = new RegExp(`^-${S}*$`);
const INDENTED = /^[ \t]/;
const REF_LINK_DEF = new RegExp(`^\\[[^\\]]+\\]:${S}`);

/* --- inline patterns -------------------------------------------------------- */
// The URL group excludes parentheses, so `[x](javascript:alert(1))` does not match at all and
// survives as literal text rather than becoming a link. That is deliberate and the fixture
// pins it: a lenient renderer that DOES match it would produce a `javascript:` href, and the
// vector is what catches that.
//
// Sticky (`y`) rather than anchored, because the scan below needs "does a link start exactly
// at index i", which is Python's `LINK.match(s, i)`. `lastIndex` is assigned immediately
// before every `exec`, so the shared instance carries no state between calls.
const LINK_AT = new RegExp(`\\[([^[\\]\\n]+)\\]\\(([^${WS}()]+)\\)`, "y");
// Python's `$` also matches just before a trailing newline and JavaScript's does not. It
// cannot bite here: a url only ever reaches these from LINK_AT's second group, which excludes
// every whitespace character including `\n`.
export const URL_HTTPS = /^https:\/\/[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+$/;
export const URL_PATH = /^\/(?!\/)[A-Za-z0-9\-._~:/?#@!$&*+,;=%]*$/;
// The scheme gate is the PREFIX only, so a URL that opens correctly but carries a bad byte
// reports `url_invalid` (a typo) rather than `url_scheme` (a refused protocol). Two different
// author mistakes, two different messages.
const URL_HTTPS_PREFIX = "https://";
const URL_PATH_PREFIX = /^\/(?!\/)/;

const LIST_MARKER = "- ";

// Rule-major, in the order the grammar spec numbers them: each rule is checked against EVERY
// line before the next rule is tried. `rule` therefore beats `list_item_empty` on `---`, and
// a body with both a blockquote and a heading reports `heading`. Checking line-major instead
// gives the same accept/reject answer and a DIFFERENT code, and the codes are API.
const LINE_RULES: ReadonlyArray<readonly [string, RegExp]> = [
  ["code_block", CODE_BLOCK],
  ["heading", HEADING],
  ["blockquote", BLOCKQUOTE],
  ["table", TABLE],
  ["rule", RULE],
  ["ordered_list", ORDERED_LIST],
  ["bullet_marker", BULLET_MARKER],
  ["list_item_empty", LIST_ITEM_EMPTY],
  ["indented", INDENTED],
];

/**
 * Put a body into the one form the grammar and the storage layer agree on.
 *
 * CRLF and bare CR become LF; trailing whitespace goes from every line; blank lines at the
 * top and bottom go; a run of blank lines collapses to one. The last of those is what makes
 * `\n\n` a reliable block separator further down: `tokenize` splits on exactly two newlines
 * and would otherwise produce empty blocks for a body a browser textarea padded out.
 */
export function normalizeBody(md: string): string {
  const text = md.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = text.split("\n").map((line) => line.replace(PY_STRIP_END, ""));

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

/**
 * Parse an ALREADY-NORMALISED body into the token tree.
 *
 * Callers holding raw author input want `tokenize(normalizeBody(md))` — this function assumes
 * the invariants `normalizeBody` establishes (LF only, no padding blank lines, no run of two
 * blank lines) and will mis-split a body that has not been through it. The vectors are
 * evaluated that way and never as `tokenize(input)`.
 */
export function tokenize(md: string): Block[] {
  rejectBody(md);
  rejectLines(md);

  const blocks: Block[] = [];
  let seenList = false;
  for (const raw of md.split("\n\n")) {
    const lines = raw.split("\n");
    const flags = lines.map((line) => line.startsWith(LIST_MARKER));
    if (!flags.every(Boolean)) {
      if (flags.some(Boolean)) throw new InvalidAnnouncementBody("list_mixed", raw);
      blocks.push(["p", paragraph(lines)]);
      continue;
    }
    if (seenList) throw new InvalidAnnouncementBody("multiple_lists", raw);
    seenList = true;
    if (lines.length > MAX_LIST_ITEMS) {
      throw new InvalidAnnouncementBody("list_too_long", String(lines.length));
    }
    blocks.push(["ul", lines.map((line) => inline(line.slice(LIST_MARKER.length)))]);
  }
  return blocks;
}

/** Rules 1-5: the size and character checks, in spec order. */
function rejectBody(md: string): void {
  if (md === "") throw new InvalidAnnouncementBody("empty");
  if (codePoints(md) > MAX_BODY_CHARS) {
    throw new InvalidAnnouncementBody("too_long", String(codePoints(md)));
  }
  const lineCount = md.split("\n").length;
  if (lineCount > MAX_LINES) {
    throw new InvalidAnnouncementBody("too_many_lines", String(lineCount));
  }
  const control = CONTROL.exec(md);
  if (control) throw new InvalidAnnouncementBody("control_char", control[0]);
  const bidi = BIDI.exec(md);
  if (bidi) throw new InvalidAnnouncementBody("bidi_control", bidi[0]);
}

/**
 * Rules 6-19: the per-line checks, then the remaining whole-body ones.
 *
 * Split out from `rejectBody` only because rules 6-14 are line-scoped and 15-19 are not; the
 * numbering in the grammar spec runs straight through both.
 */
function rejectLines(md: string): void {
  const lines = md.split("\n");
  for (const [code, pattern] of LINE_RULES) {
    for (const line of lines) {
      if (pattern.test(line)) throw new InvalidAnnouncementBody(code, line);
    }
  }

  if (IMAGE.test(md)) throw new InvalidAnnouncementBody("image", md);
  const html = HTML.exec(md);
  if (html) throw new InvalidAnnouncementBody("html", md.slice(html.index, html.index + 16));
  const entity = ENTITY.exec(md);
  if (entity) throw new InvalidAnnouncementBody("entity", entity[0]);
  if (FOOTNOTE.test(md)) throw new InvalidAnnouncementBody("footnote", md);
  if (REF_LINK.test(md)) throw new InvalidAnnouncementBody("reference_link", md);
  for (const line of lines) {
    if (REF_LINK_DEF.test(line)) throw new InvalidAnnouncementBody("reference_link", line);
  }
}

/**
 * Inline-parse each line, joined by an explicit hard break.
 *
 * A single newline inside a paragraph is a line the author typed and meant to keep, so it
 * becomes `["br"]` rather than the collapsed space CommonMark would give: in a three-line
 * banner, reflowing the author's lines changes what the banner says.
 */
function paragraph(lines: string[]): Inline[] {
  const tokens: Inline[] = [];
  lines.forEach((line, index) => {
    if (index) tokens.push(["br"]);
    tokens.push(...inline(line));
  });
  return tokens;
}

/**
 * Left-to-right scan, no backtracking, first construct at `i` wins.
 *
 * Order is code, link, strong, em, literal character. It is a scan and not a grammar on
 * purpose: the contents of code, strong, em and a link's text are LITERAL and never
 * re-parsed, so `**See [docs](/d)**` is one bold run whose text happens to contain brackets.
 * A delimiter that never closes is just a character. There are no backslash escapes — `\*` is
 * a backslash and a star.
 *
 * Indices here are UTF-16 units where Python's are code points, and that is safe rather than
 * lucky: every split point is found by searching for an ASCII delimiter, which can never land
 * inside a surrogate pair, and the literal buffer is rebuilt by concatenation. Lengths, which
 * are compared against a budget rather than used as offsets, still go through `codePoints`.
 */
function inline(s: string): Inline[] {
  const tokens: Inline[] = [];
  let buffer = "";

  const flush = () => {
    if (buffer) {
      tokens.push(["t", buffer]);
      buffer = "";
    }
  };

  let i = 0;
  const n = s.length;
  while (i < n) {
    const ch = s[i];

    if (ch === "`") {
      const j = s.indexOf("`", i + 1);
      if (j > i + 1) {
        flush();
        tokens.push(["c", s.slice(i + 1, j)]);
        i = j + 1;
        continue;
      }
    }

    if (ch === "[") {
      LINK_AT.lastIndex = i;
      const match = LINK_AT.exec(s);
      if (match) {
        flush();
        tokens.push(link(match[1], match[2]));
        i = LINK_AT.lastIndex;
        continue;
      }
    }

    if (s.startsWith("**", i)) {
      const j = s.indexOf("**", i + 2);
      if (j > i + 1) {
        const content = s.slice(i + 2, j);
        if (content && content === pyStrip(content)) {
          flush();
          tokens.push(["b", content]);
          i = j + 2;
          continue;
        }
      }
    }

    if (ch === "*") {
      const j = s.indexOf("*", i + 1);
      if (j > i) {
        const content = s.slice(i + 1, j);
        if (content && content === pyStrip(content) && !content.includes("*")) {
          flush();
          tokens.push(["i", content]);
          i = j + 1;
          continue;
        }
      }
    }

    buffer += ch;
    i += 1;
  }

  flush();
  return tokens;
}

/**
 * Validate one link and build its token; `ext` is offsite, `int` is in-app.
 *
 * The kind is not decoration: the renderer gives an `ext` link `rel="noopener noreferrer"`
 * and a new tab, and routes an `int` link through `next/link` so the `/ui` basePath is
 * applied. Deciding it here means the three implementations cannot disagree about which a
 * URL is.
 */
function link(text: string, url: string): Inline {
  const label = pyStrip(text);
  if (!label) throw new InvalidAnnouncementBody("link_text_empty", text);
  if (codePoints(url) > MAX_URL_CHARS) {
    throw new InvalidAnnouncementBody("url_too_long", String(codePoints(url)));
  }

  const external = url.startsWith(URL_HTTPS_PREFIX);
  const internal = URL_PATH_PREFIX.test(url);
  if (!external && !internal) throw new InvalidAnnouncementBody("url_scheme", url);
  if (external && !URL_HTTPS.test(url)) throw new InvalidAnnouncementBody("url_invalid", url);
  if (internal && !URL_PATH.test(url)) throw new InvalidAnnouncementBody("url_invalid", url);
  return ["a", label, url, external ? "ext" : "int"];
}

/* --- the renderer ----------------------------------------------------------- */

/**
 * Render an announcement body as React nodes.
 *
 * Lenient by contract: the strict validator already refused anything malformed at publish
 * time, so a body that fails here is not an author's mistake to report — it is an older row,
 * or a service this build does not agree with, and the reader is owed the words either way.
 * The whole body falls back to literal text, which is the grammar's stated principle for
 * unrecognised syntax applied to the whole document. It never throws and never returns an
 * error state; the banner has no error state to return one to.
 */
export function renderAnnouncementMarkdown(md: string): ReactNode {
  const normalized = normalizeBody(md);
  let blocks: Block[];
  try {
    blocks = tokenize(normalized);
  } catch (err) {
    if (err instanceof InvalidAnnouncementBody) return <p>{normalized}</p>;
    throw err;
  }
  return <>{blocks.map((block, i) => renderBlock(block, i))}</>;
}

function renderBlock(block: Block, index: number): ReactNode {
  // The gap between blocks, set here rather than by a wrapper: a wrapper would be an element
  // outside the eight this renderer is allowed to emit, and that list is asserted in the test.
  const spacing = index ? "mt-2" : undefined;
  if (block[0] === "ul") {
    return (
      <ul key={index} className={spacing ? `${spacing} list-disc pl-5` : "list-disc pl-5"}>
        {block[1].map((item, j) => (
          <li key={j}>{item.map((token, k) => renderInline(token, k))}</li>
        ))}
      </ul>
    );
  }
  return (
    <p key={index} className={spacing}>
      {block[1].map((token, j) => renderInline(token, j))}
    </p>
  );
}

function renderInline(token: Inline, key: number): ReactNode {
  switch (token[0]) {
    case "t":
      return token[1];
    case "b":
      return <strong key={key}>{token[1]}</strong>;
    case "i":
      return <em key={key}>{token[1]}</em>;
    case "c":
      return (
        <code key={key} className="rounded bg-raised/60 px-1 font-mono">
          {token[1]}
        </code>
      );
    case "br":
      return <br key={key} />;
    case "a": {
      const [, label, url, kind] = token;
      // External: a new tab, and `rel` on every one of them. `noopener` is the load-bearing
      // half — without it the opened page gets a live `window.opener` handle back into this
      // app — and `noreferrer` keeps the reader's current route out of the destination's logs.
      if (kind === "ext") {
        return (
          <a
            key={key}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2"
          >
            {label}
          </a>
        );
      }
      // Internal: next/link, not a bare <a href="/x">. The app is a static export served under
      // the `/ui` basePath, and only the router applies it — a raw anchor would escape the
      // basePath and 404.
      return (
        <Link key={key} href={url} className="underline underline-offset-2">
          {label}
        </Link>
      );
    }
  }
}
