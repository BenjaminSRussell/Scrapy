'use strict';
// #1031 #1032 #963 #1015 #1010 #952 #1034 #953
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    createMetricsFetcher, createRefreshScheduler, parseRefreshInterval,
    REFRESH_INTERVAL_CHOICES, shortcutAction, pickInitialTab,
} = require('../format-utils.js');

function fakeTimers() {
    let id = 0;
    const pending = new Map();
    return {
        setTimeout(fn, ms) { pending.set(++id, { fn, ms }); return id; },
        clearTimeout(i) { pending.delete(i); },
        fireAll() { const fns = [...pending.values()]; pending.clear(); fns.forEach(t => t.fn()); },
        count: () => pending.size,
        last: () => [...pending.values()].pop(),
    };
}

function abortableFetch() {
    const calls = [];
    const fetch = (url, init) => new Promise((resolve, reject) => {
        const call = { url, init, resolve, reject };
        calls.push(call);
        if (init && init.signal) init.signal.addEventListener('abort', () => reject(new Error('aborted')));
    });
    return { fetch, calls };
}

test('fetcher returns body text and clears its timeout', async () => {
    const t = fakeTimers();
    const f = createMetricsFetcher({ fetch: async () => ({ ok: true, text: async () => 'up 1' }), ...t });
    assert.strictEqual(await f.fetchText('/m'), 'up 1');
    assert.strictEqual(t.count(), 0);
    assert.strictEqual(f.inFlight(), false);
});

test('fetcher passes an AbortSignal and rejects on HTTP errors', async () => {
    let seen;
    const f = createMetricsFetcher({
        fetch: async (u, init) => { seen = init; return { ok: false, status: 503, statusText: 'Unavailable' }; },
        ...fakeTimers(),
    });
    await assert.rejects(f.fetchText('/m'), /HTTP 503: Unavailable/);
    assert.ok(seen.signal, 'signal passed to fetch');
});

test('fetcher times out and aborts the request (#1032)', async () => {
    const t = fakeTimers();
    const { fetch, calls } = abortableFetch();
    const f = createMetricsFetcher({ fetch, timeoutMs: 4000, ...t });
    const p = f.fetchText('/m');
    await Promise.resolve(); await Promise.resolve();
    assert.strictEqual(t.last().ms, 4000);
    t.fireAll();
    await assert.rejects(p, (e) => e.timedOut === true && /Timed out after 4s/.test(e.message));
    assert.strictEqual(calls[0].init.signal.aborted, true);
});

test('a new request aborts the previous one, which rejects as superseded (#1031)', async () => {
    const t = fakeTimers();
    const { fetch, calls } = abortableFetch();
    const f = createMetricsFetcher({ fetch, ...t });
    const first = f.fetchText('/m');
    await Promise.resolve(); await Promise.resolve();
    const second = f.fetchText('/m');
    await assert.rejects(first, (e) => e.superseded === true);
    await Promise.resolve(); await Promise.resolve();
    calls[1].resolve({ ok: true, text: async () => 'fresh' });
    assert.strictEqual(await second, 'fresh');
});

test('scheduler.setInterval reschedules a pending wait (#1015)', async () => {
    let now = 0;
    const t = fakeTimers();
    const s = createRefreshScheduler({ interval: 5000, fetch: async () => {}, now: () => now, ...t });
    await s.start();
    assert.strictEqual(s.secondsRemaining(), 5);
    s.setInterval(30000);
    assert.strictEqual(s.getInterval(), 30000);
    assert.strictEqual(s.secondsRemaining(), 30);
    assert.strictEqual(t.count(), 1, 'old timer replaced, not duplicated');
    s.setInterval('nope');
    assert.strictEqual(s.getInterval(), 30000);
});

test('scheduler paused (hidden tab, #963) schedules nothing until resumed', async () => {
    const t = fakeTimers();
    const s = createRefreshScheduler({ interval: 5000, fetch: async () => {}, now: () => 0, ...t });
    await s.start();
    s.setPaused(true);
    assert.strictEqual(t.count(), 0);
    assert.strictEqual(s.secondsRemaining(), null);
    s.setPaused(false);
    assert.strictEqual(t.count(), 1);
});

test('parseRefreshInterval accepts only the offered choices', () => {
    assert.deepStrictEqual(REFRESH_INTERVAL_CHOICES, [5000, 10000, 30000]);
    assert.strictEqual(parseRefreshInterval('10000'), 10000);
    assert.strictEqual(parseRefreshInterval(30000), 30000);
    assert.strictEqual(parseRefreshInterval('1'), 5000);
    assert.strictEqual(parseRefreshInterval(null), 5000);
    assert.strictEqual(parseRefreshInterval('x', 10000), 10000);
});

const TABS = ['overview', 'pipeline', 'performance', 'system', 'activity'];
const key = (k, extra = {}) => ({ key: k, target: { tagName: 'BODY' }, ...extra });

test('digit shortcuts pick the Nth visible tab (#952)', () => {
    assert.deepStrictEqual(shortcutAction(key('1'), TABS), { type: 'tab', tab: 'overview' });
    assert.deepStrictEqual(shortcutAction(key('5'), TABS), { type: 'tab', tab: 'activity' });
    assert.strictEqual(shortcutAction(key('6'), TABS), null);
    assert.strictEqual(shortcutAction(key('0'), TABS), null);
});

test('R refreshes (#1010)', () => {
    assert.deepStrictEqual(shortcutAction(key('r'), TABS), { type: 'refresh' });
    assert.deepStrictEqual(shortcutAction(key('R', { shiftKey: true }), TABS), { type: 'refresh' });
});

test('shortcuts ignore modifiers, form fields, IME and handled events', () => {
    assert.strictEqual(shortcutAction(key('r', { ctrlKey: true }), TABS), null); // Ctrl+R reloads
    assert.strictEqual(shortcutAction(key('1', { metaKey: true }), TABS), null);
    assert.strictEqual(shortcutAction(key('1', { altKey: true }), TABS), null);
    for (const tagName of ['INPUT', 'TEXTAREA', 'SELECT', 'input']) {
        assert.strictEqual(shortcutAction({ key: '1', target: { tagName } }, TABS), null);
    }
    assert.strictEqual(shortcutAction({ key: 'r', target: { tagName: 'DIV', isContentEditable: true } }, TABS), null);
    assert.strictEqual(shortcutAction(key('1', { isComposing: true }), TABS), null);
    assert.strictEqual(shortcutAction(key('1', { defaultPrevented: true }), TABS), null);
    assert.strictEqual(shortcutAction(key('x'), TABS), null);
});

test('initial tab: deep link wins, then remembered, invalid ignored (#1034)', () => {
    assert.strictEqual(pickInitialTab('system', 'activity', TABS), 'system');
    assert.strictEqual(pickInitialTab(null, 'activity', TABS), 'activity');
    assert.strictEqual(pickInitialTab('bogus', 'activity', TABS), 'activity');
    assert.strictEqual(pickInitialTab(null, 'jobs', TABS), null); // hidden/removed tab
    assert.strictEqual(pickInitialTab(null, null, TABS), null);
});

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('refresh bar offers the interval picker with the allowed choices (#1015)', () => {
    const m = html.match(/<select id="refresh-interval"[^>]*>([\s\S]*?)<\/select>/);
    assert.ok(m, 'select#refresh-interval present');
    const values = [...m[1].matchAll(/value="(\d+)"/g)].map(x => Number(x[1]));
    assert.deepStrictEqual(values, REFRESH_INTERVAL_CHOICES);
    assert.match(html, /<label[^>]*for="refresh-interval"/);
});

test('touch targets are at least 44px on coarse pointers and phones (#953)', () => {
    assert.match(html, /@media \(pointer: coarse\)\s*{[\s\S]*?\.tab-button[\s\S]*?min-height: 44px/);
    assert.match(html, /\.tab-button\s*{[^}]*min-height: 44px/);
});

test('app wires timeout fetcher, visibility pause, shortcuts and remembered tab', () => {
    assert.match(app, /createMetricsFetcher\(\{[^}]*timeoutMs: METRICS_TIMEOUT_MS/);
    assert.match(app, /addEventListener\('visibilitychange'/);
    assert.match(app, /shortcutAction\(event, visibleTabNames\(\)\)/);
    assert.match(app, /pickInitialTab\(tabFromLocation\(\), storeGet\(STORE_TAB_KEY\)/);
    assert.match(app, /if \(error && error\.superseded\) return;/);
});
