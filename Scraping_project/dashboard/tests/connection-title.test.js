// Run: node --test Scraping_project/dashboard/tests/
// #945: the tab title (and topbar) reflect ONLINE / OFFLINE / ERROR.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { connectionState, documentTitle } = require('../format-utils.js');

const BASE = 'Pipeline Control Center - UConn Scraping';

test('metrics reachable, pipeline running or unreported -> ONLINE', () => {
    for (const running of [1, undefined, null, NaN]) {
        const s = connectionState('online', running, 0);
        assert.strictEqual(s.key, 'online');
        assert.strictEqual(documentTitle(s, BASE), '\u25CF ONLINE \u00B7 ' + BASE);
        assert.strictEqual(s.top, '\u25CF Online');
    }
});

test('metrics reachable but pipeline_running 0 -> OFFLINE (was hidden behind "Online")', () => {
    const s = connectionState('online', 0, 0);
    assert.strictEqual(s.key, 'stopped');
    assert.strictEqual(documentTitle(s, BASE), '\u25CB OFFLINE \u00B7 ' + BASE);
    assert.match(s.top, /Pipeline offline/);
});

test('fetch failure -> ERROR, with a count once failures repeat', () => {
    assert.strictEqual(documentTitle(connectionState('never', undefined, 1), BASE), '\u26A0 ERROR \u00B7 ' + BASE);
    assert.strictEqual(documentTitle(connectionState('offline', undefined, 3), BASE), '\u26A0 ERROR (3 failed) \u00B7 ' + BASE);
    assert.strictEqual(connectionState('never', undefined, 1).top, '\u25CB Not connected');
    assert.strictEqual(connectionState('offline', undefined, 1).top, '\u25CF Disconnected');
});

test('failure counts are sanitised', () => {
    for (const bad of [undefined, null, 'x', -4, 1.7]) {
        assert.strictEqual(connectionState('offline', undefined, bad).title, 'ERROR');
    }
});

test('title without a base is just the state', () => {
    assert.strictEqual(documentTitle(connectionState('online', 1, 0), ''), '\u25CF ONLINE');
    assert.strictEqual(documentTitle(connectionState('online', 1, 0), '  '), '\u25CF ONLINE');
});

test('app.js wires both fetch outcomes through setConnectionStatus', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    // the old per-update overwrite (always "OFFLINE" since nothing emits
    // pipeline_running, then clobbered by "Online") is gone
    assert.ok(!/topbar-status'\)\.textContent\s*=/.test(src));
    assert.match(src, /setConnectionStatus\('online', metrics\['pipeline_running'\]\)/);
    assert.match(src, /consecutiveFetchFailures \+= 1;\s*\n\s*setConnectionStatus\(hasEverSucceeded \? 'offline' : 'never'\)/);
    assert.match(src, /document\.title = title/);
});
