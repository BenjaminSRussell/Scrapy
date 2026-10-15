# Dashboard keyboard-only smoke checklist (manual)

Status: manual. Run by hand, not by `node --test`. The node test in `tests/keyboard-smoke-checklist.test.js` only checks that this file stays here, is marked manual, is linked from the README, and still covers the areas below. Tracks issue #794.

Run it before a dashboard release, and after any change to tabs, dialogs, the refresh bar, forms, tables, or the activity log. Record the date, browser, and build in the Run record at the end.

## Setup

- Start the dashboard with `python Scraping_project/dashboard/serve.py` and open the printed URL (default `http://127.0.0.1:8080`). Do not start a scrape, Redis, Kafka, Postgres, or the compose stack.
- Run once with no metrics upstream. The fetch-error banner shows, and its Retry button is in the Tab order. If an exporter is available, run again with live data.
- Clear this site's storage first (remembered tab, metrics URL, activity filter), or note what was left in place.
- Use the keyboard only: Tab, Shift+Tab, Enter, Space, arrow keys, Home, End, Escape. Do not click to move focus.
- Run in desktop Chrome, Firefox, and Safari. Check the layout once at 360px wide.

## 1. Tab order and skip/navigation (every primary view)

Open each view with `?tab=overview`, `?tab=pipeline`, `?tab=performance`, `?tab=system`, or `?tab=activity`, then press Tab from the top of the page. Compare the stops with the lists in the Run record.

- [ ] The first stop is "Skip to main content". Enter moves the next Tab into the page content.
- [ ] Only the active tab is in the Tab order. Right and Left arrows move to the next or previous tab and select it. Home and End go to the first and last tab. Right on the last tab wraps to the first. Tab from the tab row enters the panel.
- [ ] Keys 1 to 5 switch tabs and move focus to the tab button. R refreshes. Neither fires while focus is in a text field, select, or checkbox.

## 2. Visible focus (primary views)

- [ ] Every stop shows a focus ring you can see by eye: the Connection summary on the top bar, the tab row, the refresh bar, and the stage dialog.
- [ ] The ring stays visible in light and dark colour schemes, and under forced colours (Windows High Contrast) where you can run it.
- [ ] No focused control is hidden under the sticky refresh bar or clipped by its container.

## 3. Controls operate from the keyboard

- [ ] Enter or Space on Pause/Resume toggles it. The label, state, and aria-pressed change together.
- [ ] Enter or Space on each activity filter chip (Success, Warning, Danger, Timeout, Info) toggles it, and aria-pressed changes.
- [ ] Space on the danger-sound checkbox toggles it.
- [ ] Enter on Refresh now, Compare to previous hour, Reset accent color, and Reset chart history runs it. Focus stays on the control.
- [ ] Enter on Export JSON, Export CSV, and Clear activity log each works once. They download a file or clear the local log.
- [ ] Arrow keys on the auto-refresh interval select change between 5s, 10s, and 30s (see F3).
- [ ] The accent colour picker opens from the keyboard. Note what each browser does.
- [ ] Enter on "Open Grafana analytics" opens a new tab, and the link says it opens in a new tab.

## 4. Dialogs and disclosures: Escape and focus return

- [ ] Enter or Space on a stage card opens the stage details dialog, and focus moves to its Close button.
- [ ] Tab and Shift+Tab stay inside the dialog and wrap at both ends.
- [ ] Escape closes the dialog, and focus returns to the stage card that opened it.
- [ ] The "coming soon" links in the dialog do nothing when activated and are announced as unavailable.
- [ ] The Connection disclosure opens with Enter. Escape is not required for this non-modal disclosure; note what happens.
- [ ] With a remembered metrics URL (open `/?metrics=http://127.0.0.1:9090/metrics` once), Enter on "Forget remembered URL" clears it and keeps focus in the Connection area (see F1).

## 5. Forms and filters

- [ ] Typing in the activity search does not trigger the 1 to 5 or R shortcuts.
- [ ] Escape in the activity search clears it and keeps focus in the field.
- [ ] Clear search is enabled once there is text (it is disabled, and skipped by Tab, while the field is empty). Reach it with Tab, press Enter, and check that the field clears and focus returns to it.
- [ ] After typing, the match count appears as status text.
- [ ] Each focused control has a name that the browser's accessibility inspector shows.

## 6. Tables and scrollable regions

- [ ] Enter on a "View data table" summary opens it, and its table is reachable with Tab.
- [ ] A table wrapper takes focus and scrolls with arrow keys when the table is wider than its box (narrow viewport).
- [ ] The activity log and the Overview activity feed scroll with arrow keys when they hold more entries than fit (see F5).

## 7. Notifications

- [ ] The fetch-error banner (role=alert) is reachable with Tab, and Enter on its Retry button refetches. Focus stays on Retry.
- [ ] Status messages (refresh status, "Chart history reset", the activity match count) are text, not colour alone.
- [ ] Screen-reader announcements are out of scope here. Cover them in a separate screen-reader pass.

## Run record

### First run, 2026-10-15

- Build: `origin/main` at 1c8ec90, with this checklist added.
- Method: an agent drove headless Chromium through Playwright at 1280×900, sending real Tab, arrow, Enter, Space, Escape, digit, and R key presses to `serve.py` on `127.0.0.1` with no metrics upstream. It walked every primary view, then tested the controls in sections 1 to 7.
- Not covered: Firefox, Safari, screen readers, 360px width, forced colours, and a live metrics upstream. Those rows still need a human pass.

Tab order after "Skip to main content", first cycle:

- Overview: Connection, Retry, Overview tab, Overview panel, "View data table, Pipeline throughput", [Overview activity feed, only while it holds entries], then the refresh bar.
- Pipeline Stages: Connection, Retry, Pipeline tab, panel, four stage cards (URL Discovery, Page Analysis, Summarization, Large Documents), "View data table, Stage progression", "View data table, Traffic routing", then the refresh bar.
- Performance: Connection, Retry, Performance tab, panel, Reset chart history, three data-table summaries (URLs discovered, Pages analyzed, Summaries produced), then the refresh bar.
- System Health: Connection, Retry, System Health tab, panel, "Prometheus metric name map", "Open Grafana analytics", then the refresh bar.
- Activity Log: Connection, Retry, Activity tab, panel, danger-sound checkbox, Export JSON, Export CSV, Clear activity log, activity search, Success, Warning, Danger, Timeout, and Info chips, [activity log, only while it holds entries], then the refresh bar.

The refresh bar is the same on every view: Auto-refresh interval, Accent color, Reset accent color, Compare to previous hour, Pause auto-refresh, Refresh metrics now.

Results:

| Check | Result |
|---|---|
| Tab order and visible focus, five views | Pass. Every visible, enabled control was reached. Every stop had a computed focus ring (`:focus-visible` plus an outline or box-shadow). A visual check is still needed. |
| Collapsed data tables | Pass. The wrapper and table become Tab stops once the summary is opened (checked on Pipeline, "Stage progression"). |
| Skip link | Pass, with a note (F2). |
| Tab-row arrows, Home, End, wrap | Pass. |
| Keys 1 to 5 and R | Pass. Keys 1 to 5 switch tabs. R sends one `/api/metrics` request, also while paused. Digits typed into the search stay there. |
| Stage details dialog | Pass. Enter and Space open it, focus starts on Close, Tab and Shift+Tab stay inside, and Escape closes it and returns focus to the card. |
| Connection disclosure | Enter opens it. Escape does not close it (F4, expected). |
| Forget remembered URL | Fail (F1). |
| Filter chips, danger checkbox, Pause/Resume, Compare, Refresh now, Reset chart history | Pass. Pause updates its label, state, and aria-pressed together. Reset chart history announces "Chart history reset". |
| Export JSON | Pass. Enter downloads `activity-log.json`. Export CSV and Clear activity log were reached with Tab but not activated. |
| Clear search | Pass. Enabled after typing and reached with Tab. Enter clears it and returns focus to the field. |
| Retry | Pass for focus. Enter leaves focus on Retry, and the banner stays because no upstream was running. The refetch itself was not counted. |
| Auto-refresh interval select | Inconclusive (F3). |
| Accent colour picker, Grafana link | Reached with Tab, not activated. |

Findings:

- **F1, moderate (focus lost).** Activating "Forget remembered URL" hides the button, closes the Connection disclosure, and leaves focus on the document body. The next Tab starts again at "Skip to main content". Expected: focus stays in the Connection area, for example on its summary. Repro: open `/?metrics=http://127.0.0.1:9090/metrics`, Tab to Connection, press Enter, Tab, then Enter on Forget.
- **F2, minor.** The skip link works through sequential navigation: the next Tab reaches the first control in the content. Focus does not move to `#main` itself (`document.activeElement` is body after Enter). Adding `tabindex="-1"` to `#main` would move focus there. Not changed here.
- **F3, unconfirmed.** In headless Chromium, arrow keys reach the refresh interval `<select>` (the keydown is not cancelled), but its value does not change, even after Enter opens the list. This is probably a headless limitation rather than a dashboard bug. Confirm in a headed browser before filing it.
- **F4, note.** Escape does not close the Connection disclosure. It is a native, non-modal `<details>`, so this matches the disclosure pattern.
- **F5, note.** Chromium made the activity feeds Tab stops only while they held entries. Chromium treats a scrollable box as focusable on its own, so Firefox and Safari may behave differently. Verify there.
- **F6, note.** The "coming soon" links in the stage dialog are focusable with `aria-disabled`, and Enter does nothing. This is the intended placeholder behaviour.
