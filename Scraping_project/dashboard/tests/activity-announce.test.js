// Run: node --test Scraping_project/dashboard/tests/
// #962: new activity items are announced (once) without moving focus.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createActivityAnnouncer } = require('../format-utils.js');

function clock() { let t = 0; return { now: () => t, tick: (ms) => { t += ms; } }; }

test('announces type label + message', () => {
    const a = createActivityAnnouncer({ now: () => 0 });
    assert.strictEqual(a.text('danger', 'Failed to fetch metrics: HTTP 502'), 'Error: Failed to fetch metrics: HTTP 502');
    assert.strictEqual(a.text('success', 'Milestone: 1K URLs'), 'Success: Milestone: 1K URLs');
    assert.strictEqual(a.text('warning', 'Queue deep'), 'Warning: Queue deep');
    assert.strictEqual(a.text('bogus', 'x'), 'Info: x');
});

test('identical consecutive messages are spoken once per window', () => {
    const c = clock();
    const a = createActivityAnnouncer({ now: c.now, repeatMs: 300000 });
    assert.ok(a.text('danger', 'Failed to fetch metrics'));
    for (let i = 0; i < 20; i++) { c.tick(5000); assert.strictEqual(a.text('danger', 'Failed to fetch metrics'), null); }
    c.tick(300000);
    assert.ok(a.text('danger', 'Failed to fetch metrics'));
});

test('a different message (or type) is always spoken', () => {
    const a = createActivityAnnouncer({ now: () => 0 });
    assert.ok(a.text('danger', 'down'));
    assert.ok(a.text('success', 'Metrics connection restored'));
    assert.ok(a.text('danger', 'down'));
    assert.ok(a.text('warning', 'down'));
});

test('whitespace is normalised; empty messages are silent', () => {
    const a = createActivityAnnouncer({ now: () => 0 });
    assert.strictEqual(a.text('info', '  a \n  b  '), 'Info: a b');
    assert.strictEqual(a.text('info', '   '), null);
    assert.strictEqual(a.text('info', null), null);
});

test('markup: one polite additions-only region; visual feeds are not live', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const region = html.match(/<div id="activity-announce"[^>]*>/);
    assert.ok(region, 'activity-announce region present');
    for (const attr of ['class="visually-hidden"', 'aria-live="polite"', 'aria-relevant="additions"', 'aria-atomic="false"']) {
        assert.ok(region[0].includes(attr), attr);
    }
    // the announcer must not sit inside a tab panel (hidden tabs are not announced)
    const before = html.slice(0, region.index);
    assert.ok(before.lastIndexOf('class="refresh-bar"') > before.lastIndexOf('tab-content'), 'region lives in the always-visible refresh bar');
    assert.match(html, /id="activity-log" role="log" aria-live="off"/);
    assert.match(html, /id="overview-activity" aria-live="off"/);
});

test('app.js announces via textContent from addActivityLogItem only', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const add = src.slice(src.indexOf('function addActivityLogItem'), src.indexOf('function announceActivity'));
    assert.match(add, /announceActivity\(type, message\)/);
    const fn = src.slice(src.indexOf('function announceActivity'), src.indexOf('let activityPinned'));
    assert.match(fn, /node\.textContent = text/);
    assert.ok(!/innerHTML/.test(fn));
    assert.ok(!src.includes("metrics-reconnect-announce"), 'reconnect is spoken once, via the announcer');
});
