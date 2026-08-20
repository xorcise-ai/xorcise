"use client";

/**
 * Dependency-free syntax colouring for the Trace's terminal blocks — a VSCode-Dark+-ish palette so
 * shell input and program output read in colour instead of flat white. Two entry points:
 *
 *   • `highlightShell(code)` — tokenises a shell command (and loosely, heredoc code) into coloured
 *     spans: comments, strings, `$variables`, keywords, numbers, `--flags`, and the command name in
 *     command position. Presentational ONLY and TOTAL: every character of the input is preserved
 *     (the concatenation of the emitted text equals the input), and it never throws.
 *   • `renderAnsi(text)` — parses ANSI SGR colour codes in program output into coloured spans (the
 *     REAL terminal colours), stripping non-colour escapes. Falls back to plain text when there's
 *     nothing to colour.
 *
 * Both cap out to plain text on very large bodies so a huge dump can't spawn tens of thousands of
 * spans. The trace panel is always dark, so fixed hex colours are fine.
 */
import { type ReactNode } from "react";

/** Token → colour class. The colours are CSS variables (`--syntax-*`, defined in globals.css) so a
 *  future light theme overrides them in one place; today they're VSCode Dark+. Tailwind emits each
 *  arbitrary class from these string literals. */
const C = {
  comment: "text-[var(--syntax-comment)]",
  string: "text-[var(--syntax-string)]",
  variable: "text-[var(--syntax-variable)]",
  keyword: "text-[var(--syntax-keyword)]",
  number: "text-[var(--syntax-number)]",
  flag: "text-[var(--syntax-flag)]",
  command: "text-[var(--syntax-command)]",
  operator: "text-[var(--syntax-operator)]",
  key: "text-[var(--syntax-key)]",
  boolean: "text-[var(--syntax-boolean)]",
} as const;

/** Above this size we don't tokenise — a multi-megabyte dump would make far too many spans. */
const MAX_HIGHLIGHT = 60_000;

const SHELL_KEYWORDS = new Set([
  // shell control + builtins
  "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "in",
  "function", "select", "time", "coproc", "return", "export", "local", "readonly", "declare",
  "set", "unset", "shift", "eval", "exec", "trap", "source", "alias", "break", "continue",
  // python-ish (heredocs embed it) — harmless as shell, helpful in a `python3 <<PY` block
  "import", "from", "def", "class", "try", "except", "finally", "with", "as", "lambda", "pass",
  "raise", "yield", "and", "or", "not", "is", "None", "True", "False", "print", "assert",
]);

// One master regex that tokenises the WHOLE string (no gaps): the last two branches (\n and .)
// catch every remaining character, so nothing is ever dropped. Ordered by priority.
const SHELL_RE = new RegExp(
  [
    "(#[^\\n]*)", // 1 comment
    '("(?:[^"\\\\]|\\\\.)*"?)', // 2 double-quoted string (tolerate unterminated at EOF)
    "('(?:[^'\\\\]|\\\\.)*'?)", // 3 single-quoted string
    "(\\$\\{[^}]*\\}|\\$\\([^)]*\\)|\\$[\\w@*#?!$-]+|\\$)", // 4 variable / command-substitution
    "(--?[A-Za-z][\\w-]*)", // 5 flag
    "(0x[0-9a-fA-F]+|\\b\\d+(?:\\.\\d+)?\\b)", // 6 number
    "([A-Za-z_][\\w./+-]*)", // 7 word (command / argument / keyword)
    "(&&|\\|\\||>>|<<|[|;&()<>=])", // 8 operator / separator
    "(\\n)", // 9 newline
    "([\\s\\S])", // 10 any other single char (incl. spaces) — the catch-all
  ].join("|"),
  "g",
);

interface Tok {
  text: string;
  /** 1-based capture-group index that matched (see SHELL_RE). */
  g: number;
}

/** Does a separator token open a new command position (so the next word is a command name)? */
function opensCommand(sep: string): boolean {
  return sep === "|" || sep === "||" || sep === "&&" || sep === ";" || sep === "&" || sep === "(";
}

export function highlightShell(code: string): ReactNode {
  if (!code) return code;
  if (code.length > MAX_HIGHLIGHT) return code; // too big — leave as plain text
  // Pass 1: tokenise.
  const toks: Tok[] = [];
  for (const m of code.matchAll(SHELL_RE)) {
    let g = 0;
    for (let i = 1; i <= 10; i++) {
      if (m[i] !== undefined) {
        g = i;
        break;
      }
    }
    toks.push({ text: m[0], g });
  }
  // Pass 2: colour, tracking command position (start of line / after a pipe or separator) and
  // treating `word=` as a variable assignment.
  const out: ReactNode[] = [];
  let cmdPos = true;
  const nextSignificant = (from: number): Tok | undefined => {
    for (let j = from; j < toks.length; j++) {
      const t = toks[j];
      if (t.g === 10 && t.text.trim() === "") continue; // skip whitespace
      return t;
    }
    return undefined;
  };
  toks.forEach((t, i) => {
    const push = (cls: string | null) =>
      out.push(
        cls ? (
          <span key={i} className={cls}>
            {t.text}
          </span>
        ) : (
          t.text
        ),
      );
    switch (t.g) {
      case 1:
        push(C.comment);
        break;
      case 2:
      case 3:
        push(C.string);
        cmdPos = false;
        break;
      case 4:
        push(C.variable);
        cmdPos = false;
        break;
      case 5:
        push(C.flag);
        cmdPos = false;
        break;
      case 6:
        push(C.number);
        cmdPos = false;
        break;
      case 7: {
        if (SHELL_KEYWORDS.has(t.text)) {
          push(C.keyword);
          cmdPos = true; // e.g. `then <cmd>` — the next word is a command
        } else if (nextSignificant(i + 1)?.text === "=") {
          push(C.variable); // FOO=bar assignment target
          cmdPos = false;
        } else if (cmdPos) {
          push(C.command);
          cmdPos = false;
        } else {
          push(null); // an argument — plain
        }
        break;
      }
      case 8:
        push(C.operator);
        cmdPos = opensCommand(t.text);
        break;
      case 9:
        push(null); // newline
        cmdPos = true;
        break;
      default:
        push(null); // whitespace / other
    }
  });
  return out;
}

// ── JSON (tool args / mcp payloads) ──────────────────────────────────────────────────────────────

// Tokenise pretty-printed JSON: a KEY string (one followed by `:`) vs a VALUE string, numbers, and
// true/false/null. The last two branches (\n and .) catch every other char (punctuation, spaces),
// so nothing is dropped. Punctuation stays the default foreground (matches editors).
const JSON_RE = new RegExp(
  [
    '("(?:[^"\\\\]|\\\\.)*")(?=\\s*:)', // 1 key (string followed by a colon)
    '("(?:[^"\\\\]|\\\\.)*")', // 2 string value
    "(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)", // 3 number
    "(\\btrue\\b|\\bfalse\\b|\\bnull\\b)", // 4 boolean / null
    "(\\n)", // 5 newline
    "([\\s\\S])", // 6 any other char
  ].join("|"),
  "g",
);

/** Colour a pretty-printed JSON string (keys blue, strings salmon, numbers green, bool/null blue).
 *  Presentational + TOTAL (preserves every character); plain-through above the size cap. */
export function highlightJson(code: string): ReactNode {
  if (!code) return code;
  if (code.length > MAX_HIGHLIGHT) return code;
  const out: ReactNode[] = [];
  let i = 0;
  for (const m of code.matchAll(JSON_RE)) {
    const key = i++;
    const span = (cls: string) =>
      out.push(
        <span key={key} className={cls}>
          {m[0]}
        </span>,
      );
    if (m[1] !== undefined) span(C.key);
    else if (m[2] !== undefined) span(C.string);
    else if (m[3] !== undefined) span(C.number);
    else if (m[4] !== undefined) span(C.boolean);
    else out.push(m[0]); // punctuation / whitespace / newline — plain
  }
  return out;
}

// ── ANSI (program output) ──────────────────────────────────────────────────────────────────────

/* SGR code -> palette entry. The values live in globals.css beside --syntax-*, so a
   terminal block and the JSON block under it are one palette and a future light theme
   flips both in one place. Emitted into an inline style, which resolves var() fine. */
const ANSI_FG: Record<number, string> = {
  30: "var(--ansi-30)", 31: "var(--ansi-31)", 32: "var(--ansi-32)", 33: "var(--ansi-33)",
  34: "var(--ansi-34)", 35: "var(--ansi-35)", 36: "var(--ansi-36)", 37: "var(--ansi-37)",
  90: "var(--ansi-90)", 91: "var(--ansi-91)", 92: "var(--ansi-92)", 93: "var(--ansi-93)",
  94: "var(--ansi-94)", 95: "var(--ansi-95)", 96: "var(--ansi-96)", 97: "var(--ansi-97)",
};

// Matches an SGR colour sequence (…m) OR any other CSI/escape (to strip it).
const ANSI_RE = new RegExp(`${String.fromCharCode(27)}\\[([0-9;]*)m|${String.fromCharCode(27)}\\[[0-9;?]*[ -/]*[@-~]`, "g");

/** True if `text` contains any ANSI escape — lets the caller pick renderAnsi vs plain. */
export function hasAnsi(text: string): boolean {
  return text.includes(String.fromCharCode(27) + "[");
}

export function renderAnsi(text: string): ReactNode {
  if (!text) return text;
  if (text.length > MAX_HIGHLIGHT || !hasAnsi(text)) {
    // Nothing to colour (or too big) — strip any stray escapes and show plain.
    return text.replace(ANSI_RE, "");
  }
  const out: ReactNode[] = [];
  let color: string | null = null;
  let bold = false;
  let last = 0;
  let key = 0;
  const flush = (upto: number) => {
    if (upto <= last) return;
    const chunk = text.slice(last, upto);
    const c = color ?? (bold ? "var(--ansi-bold-fg)" : null);
    out.push(
      c ? (
        <span key={key++} style={{ color: c, fontWeight: bold ? 600 : undefined }}>
          {chunk}
        </span>
      ) : (
        <span key={key++}>{chunk}</span>
      ),
    );
    last = upto;
  };
  for (const m of text.matchAll(ANSI_RE)) {
    const at = m.index ?? 0;
    flush(at); // emit the text run before this escape with the CURRENT style
    last = at + m[0].length; // skip the escape itself
    if (m[1] === undefined) continue; // a non-colour escape → just stripped
    // an SGR (…m) sequence — update the colour/bold state
    const params = m[1] === "" ? [0] : m[1].split(";").map((n) => parseInt(n, 10));
    for (const p of params) {
      if (p === 0) {
        color = null;
        bold = false;
      } else if (p === 1) {
        bold = true;
      } else if (p === 22) {
        bold = false;
      } else if (p === 39) {
        color = null;
      } else if (ANSI_FG[p]) {
        color = ANSI_FG[bold && p >= 30 && p <= 37 ? p + 60 : p];
      }
    }
  }
  flush(text.length);
  return out;
}
