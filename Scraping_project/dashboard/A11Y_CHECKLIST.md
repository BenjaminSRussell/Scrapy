# Control Center accessibility checklist

Check these on every dashboard change: at least on an iPhone-sized viewport with
VoiceOver, and on desktop with the keyboard.

- [ ] **Keyboard.** Every link and control is reachable with Tab and works with
  Enter or Space.
  - Segmented controls are radio groups and take the arrow keys.
  - Charts take Left/Right/Home/End to read values, and Escape to stop.
  - Search fields clear on Escape.
- [ ] **Focus.** Focus is visible on everything (`:focus-visible`). After every
  push, pop or tab switch, focus moves to the new layer's `h1`, and
  `document.title` names the layer.
- [ ] **Names.** Every control has a name. Icon-only buttons have an `aria-label`,
  the back button says "Back to <layer>", and the pause button exposes
  `aria-pressed`.
- [ ] **Not color alone.**
  - Every status dot sits next to its text (Normal, Degraded, Down, Unknown, Active,
    Idle, Stale, No data).
  - The connection pill always has its word (Live, Demo, Reconnecting, Offline).
  - Error rows have a warning glyph and the word "Error".
- [ ] **Contrast.** Text is at least 4.5:1, and glyphs and large text at least 3:1,
  in light and dark.
  - Colored text uses the `--*-text` variants. The bright system colors are for
    fills, dots and big numbers only.
  - Check `prefers-contrast: more` as well.
- [ ] **Charts and diagrams.**
  - The flow diagram's SVG is `aria-hidden`. Each stage card is a link whose name
    reads the full count, rate or freshness, and errors.
  - Charts have a screen-reader summary (now, peak, average), and the scrub value
    is announced politely.
  - Bars show their numbers as text.
  - The treemap is a pointer shortcut and is hidden from assistive tech; the list
    under it is the accessible path.
- [ ] **Live updates are not chatty.**
  - Activity announces at most one summary every 10 s ("12 new events").
  - Lists and counts update silently.
  - Alerts (`role="alert"`) are only for real problems: offline, poll errors,
    unreadable tables, failed loads.
- [ ] **Motion.** With `prefers-reduced-motion: reduce`, nothing animates: no
  particles, no view transitions, no pulse, no shimmer, no count bump.
- [ ] **Text size.** On iOS, sizes follow Dynamic Type (rem on
  `-apple-system-body`). Layouts reflow at the largest accessibility sizes, with no
  clipped controls.
- [ ] **Touch.** Targets are at least 44×44 pt. There is no horizontal page scroll
  at 320 px, and safe-area insets are respected on notched phones in both
  orientations.
- [ ] **Structure.**
  - Landmarks: `nav` (tabs), `main`, and `section`s labelled by their headings.
  - One `h1` per layer, then `h2` section titles.
  - Lists are `ul`/`ol`.
- [ ] **Forced colors.** Cells, pills, segments and the current tab stay
  distinguishable with borders and system colors.
- [ ] **Honest states.** Loading, empty, stale, unreadable and not-found states are
  written out in words, never shown as zeros.
