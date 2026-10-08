# Control Center accessibility checklist (#1054)

Use for every dashboard PR:

- [x] All controls reachable and operable by keyboard (Tab / Shift+Tab / Enter / Space) — Tabs follow the WAI-ARIA tabs pattern: roving tabindex, Arrow/Home/End (#153)
- [x] Visible focus indicator on every interactive element — `:focus-visible` rings outside forced-colors too (#343)
- [ ] Buttons/links have accessible names (text or `aria-label`)
- [ ] Toggle buttons expose `aria-pressed`; tabs expose `aria-selected`
- [ ] Text contrast ≥ 4.5:1 (3:1 for large text / UI chrome)
- [ ] Status changes announced via `role="status"` / `aria-live="polite"` (not spammy)
  - Activity items: only the new item is spoken, via the always-present `#activity-announce` region (additions only, identical repeats at most once per 5 min). The visual feeds are `aria-live="off"` because they re-render wholesale (#962).
- [ ] Charts have a text alternative (data table / caption)
- [ ] No information conveyed by color alone
- [x] Respects `prefers-reduced-motion` — hover lift, pulse, flash and loading shimmer off (#341)
- [x] Works at 360px width without horizontal page scroll — checked at 360/375/768/1024 on every tab; topbar stats wrap instead of hiding (#155)
- [x] Forced-colors / Windows High Contrast: cards, tabs, active stage and status stay distinguishable via borders and system colours (`@media (forced-colors: active)`, #982)
- [x] Landmarks (`header`, `nav`, `main`) and an h1 → h2 heading outline (#332)
- [x] Loading, stale and error states are text, not just colour or zeros (#351, #352, #362)
