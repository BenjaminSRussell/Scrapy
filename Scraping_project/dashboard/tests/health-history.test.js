// Run: node --test Scraping_project/dashboard/tests/
// #1030: System Health keeps the last 20 metrics-poll outcomes.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { HEALTH_HISTORY_LIMIT, pushPollOutcome, healthHistoryView } = require('../format-utils.js');

test('window is the last 20 outcomes, oldest dropped', () => {
    assert.strictEqual(HEALTH_HISTORY_LIMIT, 20);
    let history = [];
    for (let i = 0; i < 25; i++) history = pushPollOutcome(history, i % 2 === 0);
    assert.strictEqual(history.length, 20);
    // i=5..24 kept: even indexes succeed. Newest (i=24) is on the right.
    assert.strictEqual(history[history.length - 1], true);
    assert.strictEqual(history[0], false); // i=5 failed
    assert.strictEqual(history.filter(Boolean).length, 10);
});

test('only strict true counts as success', () => {
    const history = pushPollOutcome(pushPollOutcome([], 'ok'), 1);
    assert.deepStrictEqual(history, [false, false]);
    assert.deepStrictEqual(pushPollOutcome(null, true), [true]);
    assert.deepStrictEqual(pushPollOutcome(pushPollOutcome([], true), false), [true, false]);
});

test('a bad limit falls back to 20 and a smaller limit caps the window', () => {
    let history = [];
    for (let i = 0; i < 5; i++) history = pushPollOutcome(history, true, 0);
    assert.strictEqual(history.length, 5);
    history = pushPollOutcome(history, false, 3);
    assert.deepStrictEqual(history, [true, true, false]);
});

test('view pads empty slots on the left and summarises flaps', () => {
    const empty = healthHistoryView([], 4);
    assert.deepStrictEqual(empty.slots, ['empty', 'empty', 'empty', 'empty']);
    assert.strictEqual(empty.caption, 'No polls yet');
    assert.match(empty.label, /No polls yet/);
    assert.match(empty.label, /Oldest on the left/);

    const history = [true, false, true];
    const view = healthHistoryView(history, 4);
    assert.deepStrictEqual(view.slots, ['empty', 'ok', 'fail', 'ok']);
    assert.strictEqual(view.ok, 2);
    assert.strictEqual(view.fail, 1);
    assert.strictEqual(view.total, 3);
    assert.match(view.caption, /2\/3 succeeded, 1 failed/);
    assert.match(view.caption, /newest succeeded/);

    const failed = healthHistoryView([true, false], 20);
    assert.strictEqual(failed.slots.length, 20);
    assert.strictEqual(failed.slots[18], 'ok');
    assert.strictEqual(failed.slots[19], 'fail');
    assert.match(failed.caption, /newest failed/);
    assert.strictEqual(failed.slots.filter(s => s === 'empty').length, 18);
});

test('a longer history shows only the newest N outcomes', () => {
    const history = [];
    for (let i = 0; i < 30; i++) history.push(i >= 20);
    const view = healthHistoryView(history);
    assert.strictEqual(view.slots.length, 20);
    assert.strictEqual(view.slots.filter(s => s === 'empty').length, 0);
    assert.strictEqual(view.ok, 10);
    assert.strictEqual(view.fail, 10);
    assert.strictEqual(view.slots[0], 'fail');
    assert.strictEqual(view.slots[19], 'ok');
});

test('system health markup and fetch wiring record both outcomes', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(html, /id="health-history"/);
    assert.match(html, /id="health-history-caption"/);
    assert.match(html, /Poll history/);
    assert.match(html, /\.health-seg-ok\s*\{[^}]*background:\s*var\(--success\)/);
    assert.match(html, /\.health-seg-fail\s*\{[^}]*height:\s*55%/);
    assert.match(html, /\.health-seg-empty\s*\{[^}]*dashed/);
    const forced = html.slice(html.indexOf('@media (forced-colors: active)'));
    assert.match(forced, /\.health-seg-fail\s*\{[^}]*dashed/);
    assert.match(app, /notePollOutcome\(true\)/);
    assert.match(app, /notePollOutcome\(false\)/);
    assert.match(app, /pushPollOutcome\(pollHistory,\s*ok,\s*HEALTH_HISTORY_LIMIT\)/);
    assert.match(app, /renderHealthHistory\(\)/);
    const superseded = app.indexOf('error.superseded) return');
    const failNote = app.indexOf('notePollOutcome(false)');
    assert.ok(superseded > 0 && failNote > superseded, 'a superseded request is not a poll outcome');
});
