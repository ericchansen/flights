# Product

## Register

product

## Users

Casual, price-driven travelers deciding *where* to go next — not *how* to book a
fixed itinerary. They already have (or quickly pick) a home airport and a rough
travel window, and they want the interface to answer one question fast: "given my
budget, where can I fly for a fun cheap trip?" They are comfortable with maps and
simple filters but are not aviation power-users. They arrive curious and a little
opportunistic; the win is spotting a $30 fare to somewhere they hadn't considered.

## Product Purpose

An interactive U.S. map for exploring the low-fare dataset this repo's
crawler produces (Frontier cash fares and award miles across ~3,700 domestic
routes and a rolling ~35-day window). Pick an origin, and the map fans out
flight-path arcs to every reachable destination, priced and ranked
cheapest-first, with a drill-down into any route's day-by-day fares. Success =
a traveler lands on the view and, within seconds, sees a concrete, affordable
destination worth booking — the data turned into wanderlust. It runs fully
offline from an exported JSON snapshot: no API keys, no tile servers, no login.

## Brand Personality

Precise, calm, data-forward — the confidence of a well-made tool (Linear, Stripe,
Raycast). Voice is plain and quietly helpful, never markety or exclamatory. The
delight comes from the data itself (a shockingly cheap fare) and from crisp,
responsive interaction, not from decoration. Three words: **clear, quick, honest.**

## Anti-references

- Ad-cluttered OTA / metasearch UIs (Expedia, Kayak, CheapOair): interstitials,
  urgency banners, "only 2 left!", competing CTAs. This is the opposite.
- Cream/beige "warm SaaS" slop, gradient text, glassmorphism, decorative motion.
- Gimmicky travel sites that bury the data under hero imagery and stock photos.
- A spinning 3D globe for what is a domestic U.S. dataset — motion for wow's
  sake over legibility.

## Design Principles

- **The cheapest deal is the hero.** Every default sort, color, and layout choice
  should surface the best-value destination first, with zero configuration.
- **The data is the delight.** Let a $29 fare be the exciting thing; the chrome
  stays quiet so the numbers can shout.
- **Map and list are one instrument.** Hovering a list row lights its arc and vice
  versa; there is a single source of truth for what's selected.
- **Honest pricing.** Show real cheapest cash and award miles side by side; never
  imply a fare is available if the dataset doesn't have it.
- **Instant and offline.** Interactions are immediate (no network round-trips);
  the whole experience works from a static export.

## Accessibility & Inclusion

- Target WCAG 2.1 AA: body text ≥4.5:1, large text/UI ≥3:1, visible focus rings on
  every interactive element, full keyboard operation of the origin picker, toggle,
  slider, and deal list.
- Fare encoding is never color-alone: price is always shown as a number and rank,
  so the green→red ramp is a reinforcement, not the sole signal (color-blind safe).
- Honor `prefers-reduced-motion`: arc-draw and transitions degrade to instant.
