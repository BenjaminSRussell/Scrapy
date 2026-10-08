# Control Center accessibility checklist (#1054)

Use for every dashboard PR:

- [ ] All controls reachable and operable by keyboard (Tab / Shift+Tab / Enter / Space)
- [ ] Visible focus indicator on every interactive element
- [ ] Buttons/links have accessible names (text or `aria-label`)
- [ ] Toggle buttons expose `aria-pressed`; tabs expose `aria-selected`
- [ ] Text contrast ≥ 4.5:1 (3:1 for large text / UI chrome)
- [ ] Status changes announced via `role="status"` / `aria-live="polite"` (not spammy)
- [ ] Charts have a text alternative (data table / caption)
- [ ] No information conveyed by color alone
- [ ] Respects `prefers-reduced-motion`
- [ ] Works at 360px width without horizontal page scroll
- [x] Forced-colors / Windows High Contrast: cards, tabs, active stage and status stay distinguishable via borders and system colours (`@media (forced-colors: active)`, #982)
