import { Check, Plug, Search } from "lucide-react";
import { ComingSoonPanel } from "@/components/ui/coming-soon";

/**
 * "Other providers" — a capability XORCISE has committed to and has not shipped.
 *
 * It used to be a dashed box with a centred icon and two sentences, which was the same
 * shape as `NoResults` in catalog.tsx: "your filters matched nothing" is a dead end the
 * user clears, this is a promise about the roadmap, and the two read as one state. The
 * house treatment for the second kind already existed (`ComingSoonPanel`, behind the
 * Ingest dialog and the Settings distributed card); a lone card in an otherwise empty
 * tab still read as content that had failed to load.
 *
 * So the tab is a field, not a box: the message sits at the centre of a provider
 * constellation that occupies the whole canvas.
 */
export function OtherProviders() {
  return (
    // Bleeds past the page's p-4 so the field runs to the pane edges — this tab has no
    // other content to align to, and a canvas that stops short of the frame reads as a
    // very large card. Height is the viewport minus the header, stats strip and filter
    // bar, floored so a short window still gets a field rather than a letterbox.
    // `flex-1` — the canvas takes exactly the height the catalog's flex column has left,
    // so the tab is one screen with nothing to scroll. It was a `calc(100vh - 23rem)`
    // guess, which is the wrong quantity twice over: it does not know this pane's chrome,
    // and it overshot by enough to put a scrollbar on a page that has nothing below the
    // fold. Default min-height:auto keeps the card from ever being clipped on a short
    // window. No -mx bleed either — the pane's scroller reports overflow on the cross
    // axis, so 16px of bleed bought a full horizontal scrollbar.
    //
    // Centred on a desktop pane; on a phone the field is invisible anyway (the mask and
    // the card's width leave nothing of it on screen), so centring only pushed the card
    // down the page and it sits at the top instead.
    // py-6, not py-10: on a 1280x720 window the leftover height is barely more than the
    // card, and the extra 32px was enough to grow this box past its flex track and put a
    // scrollbar back on the pane. The card is centred in the track anyway — the padding
    // only has to keep it off the edges.
    <div className="relative flex flex-1 items-start justify-center overflow-hidden py-6 sm:items-center">
      <ProviderField />
      <ComingSoonPanel
        align="center"
        // Translucent + blurred: the field behind stays sharp everywhere else and goes
        // out of focus only under the card, so the card reads as a lens over the network
        // rather than a frosted panel dropped on top of it.
        // No entrance of its own: TabsContent already fades the panel up on tab change,
        // and a second one on the same element just doubles the same 250ms.
        className="relative w-full max-w-[42rem] border-primary/25 bg-deepest/45 shadow-2xl shadow-black/50 backdrop-blur-md"
        headline="One catalog, many providers."
        steps={[
          { icon: <Plug />, label: "Connect a provider" },
          { icon: <Search />, label: "Browse its missions" },
          { icon: <Check />, label: "Pull and run" },
        ]}
      >
        Third-party providers will register here as sources of their own — searched,
        filtered and pulled with the same controls you already use for Your Own and
        XORCISE Remote.
      </ComingSoonPanel>
    </div>
  );
}

/** Centre of the field, in the SVG's own units. Every node hangs off this. */
const CX = 600;
const CY = 300;

/**
 * The two providers that exist today — Your Own and XORCISE Remote.
 *
 * Held near the horizontal at r≈420 for one reason: the viewBox scales with the pane
 * while the card does not, so a node's distance from centre in CSS pixels shrinks as the
 * window narrows while the card stays 672px wide. At r=300 both nodes disappeared behind
 * the card at 1440px — the exact width most of this console is used at. 420 clears it
 * from ~1200px up, and below that the card is nearly the full pane anyway, where a node
 * glowing through the blur is the effect rather than a loss.
 */
const LIVE = [
  { x: 184, y: 322 },
  { x: 1018, y: 281 },
];

/**
 * The slots a third-party provider would land in. Six, spread over the outer orbits and
 * deliberately unlabelled: naming them would be inventing roadmap the product has not
 * committed to. Placed outside the card's footprint at every width — beyond it
 * horizontally, or above and below its band.
 */
const OPEN = [
  { x: 128, y: 120 },
  { x: 1074, y: 96 },
  { x: 128, y: 452 },
  { x: 1112, y: 470 },
  { x: 392, y: 84 },
  { x: 812, y: 540 },
];

/** Orbit radii, innermost first. The outer two crop on a wide pane — intended. */
const ORBITS = [
  { r: 420, opacity: 0.1, dashed: false },
  { r: 530, opacity: 0.075, dashed: true },
  { r: 660, opacity: 0.05, dashed: false },
  { r: 810, opacity: 0.04, dashed: true },
];

/**
 * The provider constellation — the catalog at the centre, two live spokes, six open ones.
 *
 * Drawn in the terrain map's vocabulary rather than a new one: amber for a live node,
 * `--color-muted-foreground` for one that is not there yet (the map's "unknown"), a
 * flowing dash on an active edge, an expanding halo on a live node. Reusing its two
 * motion classes also inherits their reduced-motion rule from globals.css, where guide
 * §11/§12 already bans glow and flicker loops in the workspace — a hand-rolled keyframe
 * here would have needed its own media query and would eventually have drifted from it.
 *
 * Purely atmospheric, and `aria-hidden`: it encodes no value a reader needs, and every
 * fact on this tab is carried by the card.
 */
function ProviderField() {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {/* The catalog's own glow, behind the card it sits under. */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(232,184,75,0.13),transparent_58%)]" />

      <svg
        // Two things at once. The mask fades the field into the pane edges, held out to
        // 62% because it is measured in CSS pixels against the pane's HALF-HEIGHT — a
        // node only a fifth of the way down the canvas is already a third through the
        // ramp. The blur throws the whole field a step out of focus so the card reads as
        // the plane in focus; 2px is enough to soften the dashes without erasing them.
        className="absolute inset-0 size-full blur-[2px] [mask-image:radial-gradient(ellipse_at_center,#000_62%,transparent_100%)]"
        viewBox="0 0 1200 600"
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          <radialGradient id="op-live-glow">
            <stop offset="0%" stopColor="var(--color-primary)" stopOpacity="0.5" />
            <stop offset="100%" stopColor="var(--color-primary)" stopOpacity="0" />
          </radialGradient>
        </defs>

        {ORBITS.map((o) => (
          <circle
            key={o.r}
            cx={CX}
            cy={CY}
            r={o.r}
            fill="none"
            stroke="var(--color-primary)"
            strokeOpacity={o.opacity}
            strokeDasharray={o.dashed ? "2 12" : undefined}
          />
        ))}

        {/* Open spokes: dashed and still, because nothing travels them yet. */}
        {OPEN.map((n) => (
          <line
            key={`edge-${n.x}-${n.y}`}
            x1={CX}
            y1={CY}
            x2={n.x}
            y2={n.y}
            stroke="var(--color-muted-foreground)"
            strokeOpacity="0.22"
            strokeDasharray="3 10"
          />
        ))}

        {/* Live spokes: the map's flowing dash, slowed from its 1s working speed to
            something ambient — this is a backdrop, not a run in progress. */}
        {LIVE.map((n) => (
          <line
            key={`edge-${n.x}-${n.y}`}
            x1={CX}
            y1={CY}
            x2={n.x}
            y2={n.y}
            stroke="var(--color-primary)"
            strokeOpacity="0.38"
            strokeDasharray="4 9"
            className="tm-flow"
            style={{ animationDuration: "7s" }}
          />
        ))}

        {OPEN.map((n) => (
          <circle
            key={`open-${n.x}-${n.y}`}
            cx={n.x}
            cy={n.y}
            r="5.5"
            fill="none"
            stroke="var(--color-muted-foreground)"
            strokeOpacity="0.5"
            strokeDasharray="2 3"
          />
        ))}

        {LIVE.map((n) => (
          <g key={`live-${n.x}-${n.y}`}>
            <circle cx={n.x} cy={n.y} r="46" fill="url(#op-live-glow)" />
            {/* r and opacity are driven by the keyframe; the attributes are the
                resting state reduced-motion users see. */}
            <circle
              cx={n.x}
              cy={n.y}
              r="16"
              fill="none"
              stroke="var(--color-primary)"
              strokeOpacity="0.45"
              className="tm-ring-amber"
            />
            <circle cx={n.x} cy={n.y} r="6" fill="var(--color-primary)" />
          </g>
        ))}
      </svg>
    </div>
  );
}
