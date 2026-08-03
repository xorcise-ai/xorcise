"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button, type ButtonProps } from "./button";

/**
 * Copy `text` to the clipboard, flipping to a "Copied" affirmation for ~2s.
 * Falls back to a hidden textarea + execCommand when the async Clipboard API is
 * unavailable (older/insecure contexts), so it works in any local browser.
 */
export function CopyButton({
  text,
  idleLabel = "Copy",
  copiedLabel = "Copied",
  ...buttonProps
}: {
  text: string;
  idleLabel?: string;
  copiedLabel?: string;
} & Omit<ButtonProps, "onClick" | "children">) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — leave the label unchanged */
    }
  }

  return (
    <Button type="button" onClick={copy} {...buttonProps}>
      {copied ? (
        <>
          <Check className="size-4" />
          {copiedLabel}
        </>
      ) : (
        <>
          <Copy className="size-4" />
          {idleLabel}
        </>
      )}
    </Button>
  );
}
