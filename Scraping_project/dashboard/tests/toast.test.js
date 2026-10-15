// Run: node --test Scraping_project/dashboard/tests/
// #994: toast stack (max 3, auto-dismiss, pause on hover) and a polite live region.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');
const { createToastStack } = require('../format-utils.js');

function fakeClock() {
    let t = 0;
    let seq = 0;
    const timers = new Map();
    return {
        now: () => t,
        setTimeout(fn, ms) {
            const id = ++seq;
            timers.set(id, { id, fn, at: t + Math.max(0, Number(ms) || 0) });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        tick(ms) {
            t += ms;
            for (let guard = 0; guard < 50; guard++) {
                const ready = [...timers.values()]
                    .filter(tm => tm.at <= t)
                    .sort((a, b) => a.at - b.at || a.id - b.id);
                if (!ready.length) return;
                const tm = ready[0];
                timers.delete(tm.id);
                tm.fn();
            }
            throw new Error('timer loop did not settle');
        },
    };
}

function stack(clock, extra) {
    return createToastStack(Object.assign({
        now: clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        dismissMs: 5000,
        limit: 3,
    }, extra || {}));
}

test('showToast stack keeps at most 3 and drops the oldest', () => {
    const s = stack(fakeClock());
    s.show('info', 'one');
    s.show('success', 'two');
    s.show('warning', 'three');
    const fourth = s.show('danger', 'four');
    assert.deepStrictEqual(fourth.removed.length, 1);
    assert.deepStrictEqual(s.items().map(it => it.message), ['two', 'three', 'four']);
    assert.strictEqual(fourth.announced, true);
    assert.strictEqual(fourth.text, 'Error: four');
});

test('empty messages are ignored; error is danger', () => {
    const s = stack(fakeClock());
    assert.strictEqual(s.show('info', '   '), null);
    assert.strictEqual(s.show('info', null), null);
    const r = s.show('error', 'nope');
    assert.strictEqual(s.items()[0].type, 'danger');
    assert.strictEqual(s.items()[0].label, 'Error');
    assert.strictEqual(r.items.length, 1);
});

test('an identical visible toast refreshes the timer and is not announced again', () => {
    const clock = fakeClock();
    const s = stack(clock);
    s.show('danger', 'Failed to fetch metrics');
    clock.tick(4000);
    const again = s.show('danger', 'Failed   to fetch metrics');
    assert.strictEqual(again.announced, false);
    assert.strictEqual(s.items().length, 1);
    clock.tick(4999);
    assert.strictEqual(s.items().length, 1, 'timer restarted from the repeat');
    clock.tick(1);
    assert.strictEqual(s.items().length, 0);
});

test('auto-dismiss fires after dismissMs', () => {
    const clock = fakeClock();
    const gone = [];
    const s = stack(clock, { onChange: (_items, ev) => gone.push(ev.id) });
    const r = s.show('success', 'Metrics refreshed');
    clock.tick(4999);
    assert.strictEqual(s.items().length, 1);
    clock.tick(1);
    assert.strictEqual(s.items().length, 0);
    assert.deepStrictEqual(gone, [r.id]);
});

test('pause holds auto-dismiss until resume (hover)', () => {
    const clock = fakeClock();
    const s = stack(clock);
    const r = s.show('info', 'Job start requested');
    clock.tick(4000);
    assert.strictEqual(s.pause(r.id), true);
    clock.tick(20000);
    assert.strictEqual(s.items().length, 1, 'paused toast stays');
    assert.strictEqual(s.pause(r.id), false, 'already paused');
    assert.strictEqual(s.resume(r.id), true);
    clock.tick(999);
    assert.strictEqual(s.items().length, 1);
    clock.tick(1);
    assert.strictEqual(s.items().length, 0);
});

test('dismissTag removes only that group', () => {
    const clock = fakeClock();
    const s = stack(clock);
    s.show('danger', 'down', { tag: 'fetch' });
    s.show('success', 'Job started', { tag: 'job' });
    assert.strictEqual(s.dismissTag('fetch'), 1);
    assert.strictEqual(s.dismissTag('fetch'), 0);
    assert.deepStrictEqual(s.items().map(it => it.message), ['Job started']);
    clock.tick(4999);
    assert.strictEqual(s.items().length, 1);
    clock.tick(1);
    assert.strictEqual(s.items().length, 0);
});

test('markup: polite additions-only toast stack outside the tab panels', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const region = html.match(/<div id="toast-stack"[^>]*>/);
    assert.ok(region, 'toast-stack present');
    for (const attr of ['role="status"', 'aria-live="polite"', 'aria-relevant="additions"', 'aria-atomic="false"', 'aria-label="Notifications"']) {
        assert.ok(region[0].includes(attr), attr);
    }
    const before = html.slice(0, region.index);
    assert.ok(before.lastIndexOf('</main>') > before.lastIndexOf('tab-content'), 'stack is not inside a tab panel');
    assert.match(html, /\.toast-stack\s*\{[^}]*position:\s*fixed/);
    assert.match(html, /\.toast-danger\s*\{[^}]*color:\s*#7f1d1d/);
});

test('app.js exposes showToast and uses it for fetch and manual refresh', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(src, /function showToast\(type, msg/);
    const fn = src.slice(src.indexOf('function renderToasts'), src.indexOf('function dismissFetchToasts'));
    assert.match(fn, /msgEl\.textContent = it\.message/);
    assert.match(fn, /mouseenter', \(\) => toastStack\.pause/);
    assert.match(fn, /mouseleave', \(\) => toastStack\.resume/);
    assert.match(fn, /focusin', \(\) => toastStack\.pause/);
    assert.ok(!/innerHTML/.test(fn));
    assert.match(src, /showToast\(failure\.type, failure\.message, \{ tag: 'fetch' \}\)/);
    assert.match(src, /showToast\('success', 'Metrics refreshed'\)/);
    assert.match(src, /showToast\('success', 'Metrics connection restored'\)/);
    assert.match(src, /TOAST_LIMIT = 3/);
});
