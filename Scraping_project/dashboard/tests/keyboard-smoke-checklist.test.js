// Run: node --test Scraping_project/dashboard/tests/
// #794: the keyboard-only smoke checklist is a manual procedure, so no node test
// drives the page. This only keeps the checklist stored with the dashboard docs,
// marked manual, linked from the README, and still covering the areas the issue lists.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dir = path.join(__dirname, '..');
const checklist = fs.readFileSync(path.join(dir, 'KEYBOARD_SMOKE.md'), 'utf8');
const readme = fs.readFileSync(path.join(dir, 'README.md'), 'utf8');

test('keyboard smoke checklist is stored with the dashboard docs and marked manual (#794)', () => {
    assert.match(checklist, /^# Dashboard keyboard-only smoke checklist \(manual\)/m);
    assert.match(checklist, /^Status: manual\b/m);
    assert.match(readme, /\[`KEYBOARD_SMOKE\.md`\]\(KEYBOARD_SMOKE\.md\)/);
});

test('keyboard smoke checklist covers the areas the issue lists (#794)', () => {
    const areas = [
        /^## 1\. Tab order and skip\/navigation/m,
        /^## 2\. Visible focus/m,
        /^## 4\. Dialogs/m,
        /^## 5\. Forms/m,
        /^## 6\. Tables/m,
        /^## 7\. Notifications/m,
        /^## Run record/m,
    ];
    for (const heading of areas) {
        assert.match(checklist, heading);
    }
});
