// Run: node --test Scraping_project/dashboard/tests/leave-guard.test.js
// #973: optional beforeunload while metrics are mid-error. Off by default.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    parseLeaveGuardEnabled,
    leaveGuardShouldPrompt,
    LEAVE_GUARD_RECENT,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('stored leave guard defaults off', () => {
    for (const v of [null, undefined, '', '0', 'false', 'off', 'no', false, 0, 'maybe']) {
        assert.strictEqual(parseLeaveGuardEnabled(v), false, String(v));
    }
    for (const v of [true, 1, '1', 'true', 'TRUE', 'on', ' yes ']) {
        assert.strictEqual(parseLeaveGuardEnabled(v), true, String(v));
    }
});

test('disabled guard never prompts, including during an error', () => {
    assert.strictEqual(LEAVE_GUARD_RECENT, 10);
    const danger = [{ type: 'danger', message: 'Failed to fetch metrics: HTTP 502' }];
    for (const enabled of [false, undefined, '0', 'no', null]) {
        assert.deepStrictEqual(
            leaveGuardShouldPrompt({ enabled, status: 'offline', activityLog: danger }),
            { prompt: false, reason: 'off' },
            String(enabled)
        );
    }
    assert.deepStrictEqual(leaveGuardShouldPrompt(), { prompt: false, reason: 'off' });
});

test('enabled guard prompts on an ERROR connection and on a recent danger row', () => {
    const on = { enabled: true, activityLog: [] };
    for (const status of ['never', 'offline', 'error', 'ERROR', 'ERROR (3 failed)']) {
        assert.deepStrictEqual(
            leaveGuardShouldPrompt({ ...on, status }),
            { prompt: true, reason: 'error' },
            status
        );
    }
    for (const status of ['online', 'stopped', 'OFFLINE', '', null]) {
        assert.deepStrictEqual(
            leaveGuardShouldPrompt({ ...on, status }),
            { prompt: false, reason: 'clear' },
            String(status)
        );
    }
    assert.deepStrictEqual(
        leaveGuardShouldPrompt({
            enabled: '1',
            status: 'online',
            activityLog: [
                { type: 'success', message: 'ok' },
                { type: 'danger', message: 'Failed to fetch metrics: down' },
            ],
        }),
        { prompt: true, reason: 'danger' }
    );
    assert.deepStrictEqual(
        leaveGuardShouldPrompt({
            enabled: true,
            status: 'online',
            activityLog: [{ type: 'timeout', message: 'timed out' }, { type: 'warning', message: 'slow' }],
        }),
        { prompt: false, reason: 'clear' }
    );
});

test('restored and older danger rows do not keep the prompt up', () => {
    assert.deepStrictEqual(
        leaveGuardShouldPrompt({
            enabled: true,
            status: 'online',
            activityLog: [{ type: 'danger', message: 'old outage', restored: true }],
        }),
        { prompt: false, reason: 'clear' }
    );
    const items = [];
    for (let i = 0; i < LEAVE_GUARD_RECENT; i++) items.push({ type: 'info', message: 'tick ' + i });
    items.push({ type: 'danger', message: 'too old' });
    assert.deepStrictEqual(
        leaveGuardShouldPrompt({ enabled: true, status: 'online', activityLog: items }),
        { prompt: false, reason: 'clear' }
    );
    assert.deepStrictEqual(
        leaveGuardShouldPrompt({ enabled: true, status: 'online', activityLog: items, recent: 0 }),
        { prompt: false, reason: 'clear' }
    );
    items[LEAVE_GUARD_RECENT - 1] = { type: 'danger', message: 'still recent' };
    assert.strictEqual(
        leaveGuardShouldPrompt({ enabled: true, status: 'online', activityLog: items }).prompt,
        true
    );
});

test('markup ships the toggle unchecked with an accessible name', () => {
    const input = html.match(/<input type="checkbox" id="leave-guard-toggle"[^>]*>/);
    assert.ok(input, 'leave-guard checkbox present');
    assert.ok(!/\schecked\b/.test(input[0]), 'not checked in markup');
    assert.match(html, /<label class="leave-guard-label" for="leave-guard-toggle">[\s\S]*Confirm before leaving on error/);
    assert.match(html, /id="leave-guard-hint" class="visually-hidden"/);
    assert.match(html, /aria-describedby="leave-guard-hint"/);
    assert.match(html, /\.leave-guard-label,\s*\n\s*\.activity-chip/);
});

test('app persists the toggle and prompts only when the decision says so', () => {
    assert.match(app, /STORE_LEAVE_GUARD_KEY = 'cc\.leaveGuard'/);
    assert.match(app, /parseLeaveGuardEnabled\(storeGet\(STORE_LEAVE_GUARD_KEY\)\)/);
    assert.match(app, /storeSet\(STORE_LEAVE_GUARD_KEY, leaveGuardEnabled \? '1' : '0'\)/);
    assert.match(app, /connectionStateKey = state\.key/);
    assert.match(app, /window\.addEventListener\('beforeunload', onLeaveGuard\)/);
    assert.match(app, /setupLeaveGuard\(\)/);
    const fn = app.slice(app.indexOf('function onLeaveGuard'), app.indexOf('function setupLeaveGuard'));
    assert.match(fn, /leaveGuardShouldPrompt\(/);
    assert.match(fn, /if \(!decision\.prompt\) return/);
    assert.match(fn, /event\.preventDefault\(\)/);
    assert.match(fn, /event\.returnValue = ''/);
    assert.ok(!/innerHTML/.test(fn));
});
