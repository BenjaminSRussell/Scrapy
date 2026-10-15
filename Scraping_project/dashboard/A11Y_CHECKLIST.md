# Control Center accessibility checklist (#1054)

Use for every dashboard PR:

- [x] All controls reachable and operable by keyboard (Tab / Shift+Tab / Enter / Space) — Tabs follow the WAI-ARIA tabs pattern: roving tabindex, Arrow/Home/End (#153). `?` opens the shortcut map when focus is not in a field; Esc or Close dismisses it (#1008)
- [x] Visible focus indicator on every interactive element — `:focus-visible` rings outside forced-colors too (#343)
- [ ] Buttons/links have accessible names (text or `aria-label`)
- [ ] Toggle buttons expose `aria-pressed`; tabs expose `aria-selected`
  - Activity level chips are toggle buttons (`aria-pressed`). The count is in the button name, and a pressed chip uses a thicker border as well as colour (#1027).
- [ ] Text contrast ≥ 4.5:1 (3:1 for large text / UI chrome)
- [ ] Status changes announced via `role="status"` / `aria-live="polite"` (not spammy)
  - Activity items: only the new item is spoken, via the always-present `#activity-announce` region (additions only, identical repeats at most once per 5 min). The visual feeds are `aria-live="off"` because they re-render wholesale (#962).
  - Activity search (#1028): `#activity-filter-status` (polite) announces the match count only. The log itself stays `aria-live="off"`. Clear search is a button, and Escape in the field also clears it.
- [ ] Charts have a text alternative (data table / caption)
- [ ] No information conveyed by color alone
- [x] Respects `prefers-reduced-motion` — hover lift, pulse, flash, loading shimmer, the shared `.skeleton` block (static gray, no pulse), and the job-started check off (#341, #1025, #1022). The optional danger chime stays silent under reduced motion and under a mute preference, and is off until opted in (#1037).
- [x] Works at 360px width without horizontal page scroll — checked at 360/375/768/1024 on every tab; topbar stats wrap instead of hiding (#155)
- [x] Forced-colors / Windows High Contrast: cards, tabs, active stage and status stay distinguishable via borders and system colours (`@media (forced-colors: active)`, #982)
- [x] Landmarks (`header`, `nav`, `main`) and an h1 → h2 heading outline (#332)
- [x] Decorative icons are inline SVG with `aria-hidden`; tabs, health tiles, and the title mark do not use emoji glyphs (#1049)
- [x] Loading, stale and error states are text, not just colour or zeros (#351, #352, #362)
