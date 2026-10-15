// Run: node --test Scraping_project/dashboard/tests/
// #1028: find one stage event in the 50-item activity buffer.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { filterActivityMessages, activityFilterStatus } = require('../format-utils.js');

const ITEMS = [
    { type: 'success', message: 'Stage 2: Analyzed 3 new page(s)', timestamp: '12:01:02' },
    { type: 'warning', message: 'Stage 3: Deduplicated 1 document(s)', timestamp: '12:01:03' },
    { type: 'timeout', message: 'Metrics request timed out after 4s and was aborted', timestamp: '12:02:00' },
    { type: 'danger', message: 'Failed to fetch metrics: HTTP 502.', timestamp: '12:03:00' },
];

test('blank query returns every item and does not mutate the buffer', () => {
    for (const q of ['', '   ', null, undefined]) {
        const out = filterActivityMessages(ITEMS, q);
        assert.notStrictEqual(out, ITEMS);
        assert.deepStrictEqual(out, ITEMS);
    }
    assert.strictEqual(ITEMS.length, 4);
});

test('match is a case-insensitive substring of the message', () => {
    assert.deepStrictEqual(
        filterActivityMessages(ITEMS, 'STAGE 2').map(i => i.type),
        ['success']);
    assert.deepStrictEqual(
        filterActivityMessages(ITEMS, '  page(s) ').map(i => i.message),
        ['Stage 2: Analyzed 3 new page(s)']);
    assert.deepStrictEqual(
        filterActivityMessages(ITEMS, 'dedup').map(i => i.type),
        ['warning']);
});

test('special characters are literal, not a regular expression', () => {
    assert.deepStrictEqual(filterActivityMessages(ITEMS, '.*'), []);
    assert.deepStrictEqual(
        filterActivityMessages(ITEMS, '.').map(i => i.type),
        ['danger']);
    assert.deepStrictEqual(
        filterActivityMessages(ITEMS, 'page(s)').map(i => i.type),
        ['success']);
});

test('type and timestamp are not searched', () => {
    assert.deepStrictEqual(filterActivityMessages(ITEMS, 'timeout'), []);
    assert.deepStrictEqual(filterActivityMessages(ITEMS, '12:01'), []);
    assert.deepStrictEqual(
        filterActivityMessages(ITEMS, 'timed out').map(i => i.type),
        ['timeout']);
});

test('no match, missing messages, and a non-array input', () => {
    assert.deepStrictEqual(filterActivityMessages(ITEMS, 'no-such-event'), []);
    assert.deepStrictEqual(filterActivityMessages([{ type: 'info', message: null }, null, {}], 'x'), []);
    assert.deepStrictEqual(filterActivityMessages([{ message: 'Stage 1' }, null], 'stage'), [{ message: 'Stage 1' }]);
    assert.deepStrictEqual(filterActivityMessages(null, 'stage'), []);
    assert.deepStrictEqual(filterActivityMessages('Stage 2', 'stage'), []);
});

test('newest-first order is preserved', () => {
    const out = filterActivityMessages(ITEMS, 'stage');
    assert.deepStrictEqual(out.map(i => i.type), ['success', 'warning']);
});

test('status text is empty until a query is set', () => {
    assert.strictEqual(activityFilterStatus(4, 4, ''), '');
    assert.strictEqual(activityFilterStatus(4, 4, '   '), '');
    assert.strictEqual(activityFilterStatus(0, 50, 'stage 9'), 'No activity matches "stage 9".');
    assert.strictEqual(activityFilterStatus(2, 50, 'stage'), 'Showing 2 of 50');
    assert.strictEqual(activityFilterStatus(-1, NaN, 'x'), 'No activity matches "x".');
});

test('Activity tab markup has a labelled search and a clear control (#1028)', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const tab = html.slice(html.indexOf('id="tab-activity"'), html.indexOf('id="activity-announce"'));
    assert.match(tab, /<label[^>]*for="activity-search"[^>]*>Search activity<\/label>/);
    assert.match(tab, /<input[^>]*type="search"[^>]*id="activity-search"[^>]*aria-controls="activity-log"/);
    assert.match(tab, /id="clear-activity-search"[^>]*disabled[^>]*>Clear search<\/button>/);
    assert.match(tab, /id="activity-filter-status"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden/);
    assert.match(tab, /id="activity-log" role="log" aria-live="off"/);
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(app, /filterActivityMessages\(capped, query\)/);
    assert.match(app, /const query = container\.id === 'activity-log' \? currentActivityQuery\(\) : ''/);
    assert.match(app, /container\.id === 'activity-log' \? activityLevelFilter : null/);
    assert.match(app, /setupActivitySearch\(\)/);
    assert.match(app, /getElementById\('clear-activity-search'\)/);
    assert.match(app, /event\.key !== 'Escape'/);
    assert.match(app, /status\.textContent = text/);
    assert.match(app, /function exportActivityLog\(format\) \{\s*const rows = activityLog\.map/);
    assert.ok(!/activity-filter-status[\s\S]{0,80}innerHTML/.test(app));
});
