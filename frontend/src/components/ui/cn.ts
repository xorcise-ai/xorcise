import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/* The typographic scale (globals.css @theme) names its rungs by ROLE, not by
   t-shirt size. tailwind-merge cannot know that, so out of the box it files
   `text-label|caption|data|body|lead` under text-COLOR — and a colour class in
   the same merged string then deletes the size. That silently stripped the size
   off CardTitle, Input, Loading, TableHead and every Badge/Button variant.
   Registering the rungs in the font-size group restores size-vs-colour merging. */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      // MUST MATCH the rungs in @theme. This list had drifted from it:
      //   · `dense` and `row` were MISSING, so tailwind-merge filed them as colours and
      //     a colour class in the same merged string deleted the size outright. Live
      //     today in cross-run-context.tsx, where `cn("text-dense …", deltaCls)` ships
      //     a delta figure with no size at all.
      //   · `data` was registered as a size, but `--color-data` makes `text-data` a
      //     COLOUR — so a real colour utility was being claimed as a rung.
      // It fails invisibly: the class is not overridden, it is DROPPED, so the element
      // renders at the inherited size and stops matching `.text-<rung>` entirely.
      "font-size": [{ text: ["label", "caption", "dense", "body", "row", "lead"] }],
    },
  },
});

/** Merge conditional class names and resolve Tailwind conflicts (last wins). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
