# Control Center design tokens

`tokens.css` is the only place raw color, spacing, radius, and type values are defined. Dashboard CSS, `styleguide.html`, and Chart.js series reference these names. Do not add a new hex color in `index.html`.

## Color

Use the semantic name. The hex in `tokens.css` is the current value, not the API.

| Token | Role |
|---|---|
| `--color-primary` | Brand blue. URL charts. Legacy `--primary`. |
| `--color-primary-dark` | Links and the focus ring on light surfaces. Legacy `--primary-dark`. |
| `--color-accent` | Second brand color (violet). Legacy `--secondary`. |
| `--color-success` | Healthy and positive state. Legacy `--success`. |
| `--color-warning` | Caution, stale, and timeout accent. Legacy `--warning`. |
| `--color-danger` | Failure and error. Legacy `--danger`. |
| `--color-text-muted` | Secondary text on white and gray-100 (7.2:1). Not slate-400 (`#94a3b8`, 2.6:1). Legacy `--muted-text`. |

Status pairs (background and foreground):

| Pair | Use |
|---|---|
| `--color-success-bg` / `--color-success-fg` | Success badge, online |
| `--color-success-soft` | Healthy card, success activity |
| `--color-success-solid` | Styleguide success badge |
| `--color-warning-bg` / `--color-warning-fg` | Warning badge, stale, noscript, charts unavailable |
| `--color-warning-border` | Stale badge outline |
| `--color-warning-soft` | Warning activity |
| `--color-warning-solid` | Styleguide warning badge |
| `--color-timeout-bg` | Timed-out activity |
| `--color-flash` | Metric value flash |
| `--color-danger-bg` / `--color-danger-fg` | Danger badge, offline |
| `--color-danger-fg-strong` | Fetch-error text |
| `--color-danger-soft` | Unhealthy card, danger activity |
| `--color-danger-solid` / `--color-danger-border` | Production env badge, error outline |
| `--color-info-bg` / `--color-info-fg` | Info badge |
| `--color-info-solid` | Styleguide info badge |

Neutrals: `--color-surface` (cards), `--color-on-primary` (text on the brand gradient), `--color-text`, `--color-text-quiet`, `--color-ink` (skip link). `--gray-50` through `--gray-900` are legacy aliases of `--color-gray-*`.

Environment: `--color-slate-700` / `--color-slate-200` (local), `--color-staging` / `--color-staging-fg`, `--color-milestone-bg` / `--color-milestone-fg`.

Effects, not for text: `--color-shadow`, `--color-shadow-raised`, `--color-shadow-bar`, `--color-primary-glow`, `--color-scrim`, `--color-on-primary-veil`, `--color-focus` (styleguide ring), `--color-slate-900` (tab-strip fade).

Chart.js paints a canvas, so it cannot use `var()`. `chartToken()` in `app.js` reads `--color-primary`, `--color-accent`, `--color-success`, and `--color-warning`.

Forced-colors mode keeps system colors (`CanvasText`, `Highlight`, and the rest). Do not token those.

## Space

4px grid (`1rem` = 16px). Prefer these over a new magic number.

| Token | Value |
|---|---|
| `--space-1` | 0.25rem |
| `--space-2` | 0.5rem |
| `--space-3` | 0.75rem |
| `--space-4` | 1rem |
| `--space-5` | 1.25rem |
| `--space-6` | 1.5rem |
| `--space-8` | 2rem |

Sizes off this grid (0.15rem, 0.35rem, 0.8rem, 80px, 88px) stay literal until a layout pass.

## Radius

| Token | Value |
|---|---|
| `--radius-xs` | 2px |
| `--radius-sm` | 0.25rem |
| `--radius-md` | 0.375rem |
| `--radius-lg` | 0.5rem |
| `--radius-pill` | 9999px |
| `--radius-full` | 50% |

## Type

`--font-sans` is the UI stack. `--font-mono` is for metric names.

| Token | Value |
|---|---|
| `--font-size-2xs` | 0.7rem |
| `--font-size-xs` | 0.75rem |
| `--font-size-sm` | 0.875rem |
| `--font-size-md` | 1rem |
| `--font-size-lg` | 1.25rem |
| `--font-size-xl` | 1.5rem |
| `--font-size-2xl` | 1.75rem |
| `--font-weight-semibold` | 600 |
| `--font-weight-bold` | 700 |

One-off sizes (0.8rem, 0.85rem, 0.9rem, 0.95rem, 1.1rem) are not on the scale.

Chart heights (`--chart-h-sm` 160px, `--chart-h-md` 240px, `--chart-h-lg` 300px) stay in `index.html`. They are layout, not color.
