// Run: node --test Scraping_project/dashboard/tests/
// #845: activity timestamps are stored as ISO-8601 UTC, shown in one fixed zone
// with its label, and ordered by instant so the order is the same in every zone.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    ACTIVITY_TIME_ZONE,
    activityTimeMs,
    compareActivityNewestFirst,
    formatActivityTime,
} = require('../format-utils.js');

const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');

test('activity rows are shown in America/New_York (#845)', () => {
    assert.strictEqual(ACTIVITY_TIME_ZONE, 'America/New_York');
});

test('only a UTC ISO-8601 stamp is accepted; local clock strings and offsets are not (#845)', () => {
    assert.strictEqual(activityTimeMs('2026-10-15T22:36:12.345Z'), Date.UTC(2026, 9, 15, 22, 36, 12, 345));
    assert.strictEqual(activityTimeMs('2026-10-15T22:36:12Z'), Date.UTC(2026, 9, 15, 22, 36, 12));
    const rejected = ['6:36:12 PM', '12:01:02', '2026-10-15T18:36:12', '2026-10-15T18:36:12-04:00',
        '2026-13-01T00:00:00Z', '', '   ', null, undefined, 5];
    for (const bad of rejected) {
        assert.strictEqual(activityTimeMs(bad), null, String(bad));
    }
});

test('a UTC stamp renders with the New York time and its zone label (#845)', () => {
    const oct = formatActivityTime('2026-10-15T22:36:12.345Z');
    assert.strictEqual(oct.text, '18:36:12 EDT');
    assert.strictEqual(oct.title, '2026-10-15 22:36:12 UTC');
    assert.strictEqual(oct.iso, '2026-10-15T22:36:12.345Z');

    const jan = formatActivityTime('2026-01-15T17:00:00Z');
    assert.strictEqual(jan.text, '12:00:00 EST');
    assert.strictEqual(jan.iso, '2026-01-15T17:00:00.000Z');
});

test('midnight is 00:xx, not 24:xx (#845)', () => {
    assert.strictEqual(formatActivityTime('2026-10-15T04:05:00.000Z').text, '00:05:00 EDT');
});

test('the repeated fall-back hour carries EDT or EST, so both 01:30 rows are unambiguous (#845)', () => {
    const first = formatActivityTime('2026-11-01T05:30:00.000Z');
    const second = formatActivityTime('2026-11-01T06:30:00.000Z');
    assert.strictEqual(first.text, '01:30:00 EDT');
    assert.strictEqual(second.text, '01:30:00 EST');
    assert.notStrictEqual(first.text, second.text);
});

test('a missing or unzoned stamp renders as a dash, not a guessed zone (#845)', () => {
    for (const bad of ['6:36:12 PM', '', null]) {
        const out = formatActivityTime(bad);
        assert.strictEqual(out.text, '—', String(bad));
        assert.strictEqual(out.title, '');
        assert.strictEqual(out.iso, '');
    }
});

test('rows sort by UTC instant, newest first, with bad stamps last (#845)', () => {
    const rows = [
        { message: 'before fall-back', timestamp: '2026-11-01T05:30:00.000Z' }, // 01:30 EDT
        { message: 'after fall-back', timestamp: '2026-11-01T06:30:00.000Z' }, // 01:30 EST
        { message: 'no zone', timestamp: '6:36:12 PM' },
        { message: 'same instant', timestamp: '2026-11-01T06:30:00.000Z' },
    ];
    const order = rows.slice().sort(compareActivityNewestFirst).map((row) => row.message);
    assert.deepStrictEqual(order, ['after fall-back', 'same instant', 'before fall-back', 'no zone']);
});

test('the sort is stable for equal instants and does not mutate its input (#845)', () => {
    const a = { message: 'a', timestamp: '2026-10-15T22:36:12.000Z' };
    const b = { message: 'b', timestamp: '2026-10-15T22:36:12.000Z' };
    const input = [a, b];
    assert.deepStrictEqual(input.slice().sort(compareActivityNewestFirst), [a, b]);
    assert.deepStrictEqual(input.slice().reverse().sort(compareActivityNewestFirst), [b, a]);
    assert.deepStrictEqual(input, [a, b]);
});

test('addActivityLogItem stores a UTC ISO stamp and keeps the log ordered by instant (#845)', () => {
    const add = app.slice(app.indexOf('function addActivityLogItem'), app.indexOf('function announceActivity'));
    assert.match(add, /const timestamp = new Date\(\)\.toISOString\(\)/);
    assert.ok(!/toLocaleTimeString/.test(add), 'no local clock string is stored');
    assert.match(add, /activityLog\.sort\(compareActivityNewestFirst\)/);
});

test('renderActivityList shows the zone label and keeps the UTC instant on <time> (#845)', () => {
    const render = app.slice(app.indexOf('function renderActivityList'), app.indexOf('function currentActivityQuery'));
    assert.match(render, /formatActivityTime\(item\.timestamp\)/);
    assert.match(render, /document\.createElement\('time'\)/);
    assert.match(render, /time\.setAttribute\('datetime', when\.iso\)/);
    assert.match(render, /time\.title = when\.title/);
    assert.ok(!/item\.timestamp \?\? ''/.test(render), 'the stored value is not rendered raw');
});

test('restore drops rows without a UTC stamp, counts the rest, and orders them (#845)', () => {
    const restore = app.slice(app.indexOf('function restorePersistedActivityLog'), app.indexOf('function clearActivityLog'));
    assert.match(restore, /activityTimeMs\(item\.timestamp\) !== null/);
    assert.match(restore, /\.sort\(compareActivityNewestFirst\)/);
    assert.match(restore, /activityStaleNote\(rows\.length\)/);
    assert.match(restore, /restored: true/);
});

test('the Activity tab help names the zone and the UTC tooltip (#845)', () => {
    const m = html.match(/id="activity-log-cap"[^>]*>([^<]+)/);
    assert.ok(m, 'activity-log-cap help text present');
    assert.match(m[1], /America\/New_York/);
    assert.match(m[1], /hover a time for UTC/);
});

test('README documents the timestamp standard (#845)', () => {
    assert.match(readme, /## Activity log timestamps \(#845\)/);
    assert.match(readme, /ISO-8601 UTC/);
    assert.match(readme, /America\/New_York/);
});
