// Run: node --test Scraping_project/dashboard/tests/
// #1027: Activity tab level chips filter client-side and show a count.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    ACTIVITY_LEVELS,
    activityLevelCounts,
    filterActivityItems,
    toggleActivityLevel,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

const sample = [
    { type: 'success', message: 'ok' },
    { type: 'danger', message: 'boom' },
    { type: 'warning', message: 'hmm' },
    { type: 'danger', message: 'again' },
    { type: 'timeout', message: 'slow' },
    { type: 'info', message: 'note' },
    { type: 'other', message: 'misc' },
];

test('known activity levels are the chip set', () => {
    assert.deepStrictEqual(ACTIVITY_LEVELS, ['success', 'warning', 'danger', 'timeout', 'info']);
});

test('an empty selection shows every item, including unknown levels', () => {
    assert.deepStrictEqual(filterActivityItems(sample, []), sample);
    assert.deepStrictEqual(filterActivityItems(sample, new Set()), sample);
    assert.deepStrictEqual(filterActivityItems(sample, null), sample);
});

test('danger-only view keeps danger entries and their order', () => {
    const selected = toggleActivityLevel(new Set(), 'danger');
    assert.deepStrictEqual(
        filterActivityItems(sample, selected).map(item => item.message),
        ['boom', 'again']
    );
    assert.deepStrictEqual(
        filterActivityItems(sample, 'danger').map(item => item.message),
        ['boom', 'again']
    );
});

test('several pressed chips union those levels', () => {
    let selected = toggleActivityLevel(new Set(), 'danger');
    selected = toggleActivityLevel(selected, 'warning');
    assert.deepStrictEqual(
        filterActivityItems(sample, selected).map(item => item.type),
        ['danger', 'warning', 'danger']
    );
    const cleared = toggleActivityLevel(selected, 'danger');
    assert.deepStrictEqual(Array.from(cleared), ['warning']);
    assert.strictEqual(selected.has('danger'), true, 'toggle copies the set');
});

test('chip counts cover the whole log, including zeros', () => {
    const counts = activityLevelCounts(sample);
    assert.strictEqual(counts.success, 1);
    assert.strictEqual(counts.warning, 1);
    assert.strictEqual(counts.danger, 2);
    assert.strictEqual(counts.timeout, 1);
    assert.strictEqual(counts.info, 1);
    assert.strictEqual(counts.other, undefined);
    assert.deepStrictEqual(activityLevelCounts(null), {
        success: 0, warning: 0, danger: 0, timeout: 0, info: 0,
    });
});

test('Activity tab chips are toggles with a count, and the overview feed is not filtered', () => {
    const tabStart = html.indexOf('id="tab-activity"');
    const tabEnd = html.indexOf('id="activity-announce"');
    const tab = html.slice(tabStart, tabEnd);
    assert.match(tab, /class="activity-filters"/);
    assert.match(tab, /aria-label="Filter activity by level"/);
    for (const level of ACTIVITY_LEVELS) {
        assert.match(tab, new RegExp(`data-level="${level}"[^>]*aria-pressed="false"`));
        assert.match(tab, new RegExp(`data-level-count="${level}">0</span>`));
    }
    assert.match(app, /filterActivityItems\(activityLog, levels\)/);
    assert.match(app, /activityLevelCounts\(activityLog\)/);
    assert.match(app, /toggleActivityLevel\(activityLevelFilter/);
    assert.match(app, /container\.id === 'activity-log' \? activityLevelFilter : null/);
    assert.ok(!/id="overview-activity"[\s\S]{0,200}activity-chip/.test(html));
});
