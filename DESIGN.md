# Design

A clean, utilitarian data tool for exploring cheap flights on a map. Light mode,
pure-white canvas, one cobalt brand color for interface actions, and a separate
perceptual fare ramp (green = cheap → red = pricey) as the core data encoding.
The reference points are Linear, Stripe, and Raycast: quiet chrome, precise
spacing, the data doing the talking.

## Theme

Light, restrained. Pure white content surface with a faintly cool secondary panel
for the sidebar. The map basemap is a soft neutral land fill with hairline borders
so the colored flight arcs and airport nodes carry all the visual energy. No
gradients, no glass, no decorative shadow — depth comes from a tight, consistent
elevation scale.

## Color

OKLCH throughout. Cobalt (hue ~238°) is the single UI brand color; a teal-green
accent marks positive / great-deal states. Fare pricing uses its own sequential
ramp so price is legible at a glance and independent of the UI accent.

```css
:root {
  /* Neutrals / surfaces */
  --bg:            oklch(1 0 0);            /* pure white content canvas */
  --surface:       oklch(0.978 0.004 240);  /* sidebar / panels (cool) */
  --surface-2:     oklch(0.955 0.006 240);  /* insets, hover rows */
  --border:        oklch(0.90 0.006 240);   /* hairlines */
  --border-strong: oklch(0.82 0.008 240);

  /* Text */
  --ink:           oklch(0.24 0.02 250);    /* body/headings, ~11:1 on white */
  --muted:         oklch(0.52 0.02 250);    /* secondary text, ~4.6:1 */
  --faint:         oklch(0.64 0.015 250);   /* tertiary / captions */

  /* Brand */
  --primary:       oklch(0.53 0.15 245);    /* cobalt — actions, selection */
  --primary-hover: oklch(0.47 0.15 245);
  --primary-weak:  oklch(0.94 0.03 245);    /* selected row tint */
  --accent:        oklch(0.70 0.13 168);    /* teal-green — great-deal marker */
  --on-primary:    oklch(0.99 0 0);         /* white text on cobalt fills */

  /* Map */
  --land:          oklch(0.955 0.004 240);
  --land-border:   oklch(0.88 0.006 240);
  --node:          oklch(0.62 0.02 250);    /* idle airport dot */

  /* Fare ramp — cheap → pricey (also exposed to JS as stops) */
  --fare-1: oklch(0.72 0.15 165);  /* teal-green  — cheapest */
  --fare-2: oklch(0.80 0.15 140);  /* green */
  --fare-3: oklch(0.83 0.14 95);   /* chartreuse */
  --fare-4: oklch(0.80 0.14 70);   /* amber */
  --fare-5: oklch(0.72 0.16 45);   /* orange */
  --fare-6: oklch(0.62 0.17 28);   /* red — priciest */

  /* Status */
  --success: oklch(0.62 0.14 160);
  --danger:  oklch(0.58 0.19 25);
}
```

Text-on-color: cobalt and every saturated fare fill carry white text (H–K effect).
Fare ramp is a reinforcement of the numeric price + rank, never the sole signal.

## Typography

One family. **Inter** (variable) for the entire UI — headings, labels, buttons,
body — with **tabular figures** (`font-feature-settings: "tnum" 1`) everywhere
prices and dates appear so numbers align in the ranked list. Monospace only for
airport codes (`ui-monospace`) to make DEN / CUN scan as codes.

Fixed rem scale (product, not fluid). Ratio ~1.2:

- `--t-xs: 0.75rem` (12px) — captions, axis labels
- `--t-sm: 0.8125rem` (13px) — secondary / table
- `--t-base: 0.9375rem` (15px) — body, controls
- `--t-md: 1.0625rem` (17px) — panel titles
- `--t-lg: 1.375rem` (22px) — the highlighted fare / section head
- `--t-xl: 1.75rem` (28px) — the single hero price in route detail

Weights: 400 body, 500 controls/labels, 600 headings and prices. Line-height 1.5
for prose, 1.2 for numeric/dense rows. Never a display font in labels or data.

## Layout

App shell, not a page. Two zones on desktop:

- **Sidebar (left, ~360px, `--surface`)**: origin picker, cash/miles toggle,
  date-window slider, and the scrollable ranked deal list.
- **Map (right, fills remaining, `--bg`)**: the North-America projection, airport
  nodes, and origin→destination arcs. A compact fare legend + route-detail popover
  float over the map bottom-corner.

Responsive is structural: below ~880px the sidebar becomes a top sheet / bottom
drawer over a full-bleed map; the deal list collapses to a swipe-up panel. No
fluid typography — breakpoints reflow structure only. Spacing scale (4px base):
4 / 8 / 12 / 16 / 24 / 32 / 48. Radii: 6px controls, 10px cards, 999px pills.

Z-index scale: base map < nodes < arcs < node-labels < legend < popover <
dropdown < tooltip.

## Components

Every interactive element ships default / hover / focus-visible / active /
disabled; async areas use skeletons, not spinners. Core set:

- **Origin picker**: searchable combobox (native `<datalist>`-grade UX,
  keyboard-navigable) listing all origins by city + code.
- **Cash/Miles segmented toggle**: two-option segmented control; switches the
  fare metric across map, list, and legend at once.
- **Date-window range slider**: dual-thumb slider over the ~35 available dates;
  recomputes cheapest-in-window live.
- **Deal list rows**: rank · city · code · price · best-date; hover lights the
  matching arc, click opens route detail. Selected row uses `--primary-weak`.
- **Airport node**: idle dot (`--node`); origin is a cobalt ring; destinations
  are fare-colored; hover grows + shows a code tooltip.
- **Arc**: great-circle path, width by rank, color by fare; de-emphasized until
  hovered/selected.
- **Route-detail popover**: hero cheapest price, cash vs miles, and a small
  day-by-day fare bar strip across the window.
- **Fare legend**: the ramp with min/max $ (or miles) labels.
- **Empty state**: before an origin is chosen, the map shows all nodes dimmed with
  a one-line prompt to pick a home airport — teaching the interface, not blank.

## Motion

150–250ms, ease-out; conveys state only. Arc draw uses a short stroke-dashoffset
reveal (staggered by rank) when an origin changes; hover highlight and popover are
crossfades. `@media (prefers-reduced-motion: reduce)` drops all of it to instant.
No page-load choreography — it loads into the task.
