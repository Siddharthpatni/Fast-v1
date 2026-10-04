# FastMile design system (master)

Derived with the `ui-ux-pro-max` skill, then checked for fit as that skill requires.
Query: `"public transit journey planner mobility map" --design-system --density 7 --variance 4 --motion 3`.

## Accepted

| Area | Decision | Source |
|---|---|---|
| Style | Flat: no shadows or gradients, clean lines, colour/opacity hover only | `--design-system` style "Flat Design" |
| Colour | Transit blue + line colours (tokens below) | `--design-system` colours, note "Transit blue + line colors" |
| Type | Fira Sans everywhere, tabular figures for times | `--domain typography` "Dashboard Data" (Fira family); mono half not used |
| Icons | Phosphor (`@phosphor-icons/react`), never emoji or text glyphs | `--domain icons`; checklist "No emojis as icons" |
| Motion | 150–200 ms colour/border transitions; none under `prefers-reduced-motion` | style "Key effects" |
| Touch | ≥44 px targets on touch screens, ≥24 px on desktop, ≥8 px gaps | `--domain ux` Touch Target Size / Touch Spacing |
| Feedback | Skeleton rows while planning; disabled + label while submitting | `--domain ux` Loading States / Loading Buttons |
| Meaning | Never colour alone: status, severity and reliability always have text/icon | `--domain ux` Color Only |
| Focus | Visible focus ring on every control | `--domain ux` Focus States |
| Breakpoints | 375, 768, 1024, 1440 | pre-delivery checklist |

## Tokens

| Token | Value | Use |
|---|---|---|
| `--primary` | `#2563EB` (hover `#1D4ED8`) | Actions, selection, focus ring |
| `--secondary` | `#0891B2` | Secondary emphasis |
| `--accent` | `#EA580C` (text on it: `#000000`) | Disruptions only |
| `--danger` | `#DC2626` | Errors, high severity |
| `--success` | `#15803D` | Service OK |
| `--paper` | `#F8FAFC` | Page background |
| `--surface` | `#FFFFFF` | Panels |
| `--ink` / `--ink-2` / `--ink-3` | `#0F172A` / `#475569` / `#64748B` | Text (all ≥4.5:1 on white) |
| `--rule` / `--rule-soft` | `#E4ECFC` / `#F1F5FD` | Borders, muted fills |

Line colours come from the GTFS feed; modes without one use the fallbacks in `frontend/src/modes.ts`.

## Rejected (and why)

- **Pattern "Product Demo + Features"** (hero, video, CTA): a marketing landing page. FastMile is an app.
- **Typography Lexend Mega / Public Sans**: the data tags it "neubrutalist, loud, quirky, Gen Z"; wrong for commuters reading departure times.
- **GSAP scroll-reveal motion**: marketing-page motion and a new dependency; an app panel needs instant, stable content.

## Refinement pass (taste-skill + redesign-skill)

Design read: redesign (preserve) of a trip-planner product UI. Dials: variance 3, motion 3, density 5.
taste-skill's landing-page rules (hero, bento, imagery, marquees) do not apply to this app.

Accepted:
- Dark mode follows the system setting. Dark tokens live in `App.css`.
- The map uses basemap.de vector tiles through MapLibre (lazy-loaded): the official grey style `bm_web_gry` in light mode and the official dark style `bm_web_drk` in dark mode. Without WebGL it falls back to the grey raster tiles, inverted in dark mode.
- `--primary-text` (#2563EB light, #8AB4FF dark) is used where the primary colour is small text. It keeps AA contrast on dark surfaces.
- `--on-accent` is #1C0F05, an off-black tinted to the accent (5.3:1 on #EA580C). No pure #000.
- z-index tokens: `--z-hint` 900, `--z-notice` 1000, `--z-popover` 1100, `--z-skip` 1200. All sit above Leaflet's panes.
- Pressed state: `translateY(1px)`. `text-wrap: pretty` on running text, `balance` on headings.
- Skip link to the planner. Map layers are themed by class, not by hard-coded colours.
- The reliability bar is hidden on direct trips with no risk, because "Direct" already says it.

Rejected:
- Grain or noise texture: the map is the texture.
- Hyphens in time ranges: "17:20 – 17:48" keeps the en dash, as in German timetables.
- Geist or Satoshi: Fira Sans is kept, since it has tabular figures and suits signage.
