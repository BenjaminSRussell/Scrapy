// Run: node --test Scraping_project/dashboard/tests/
// Single refresh scheduler: the countdown is derived from nextFetchAt, so it
// hits 0 when a fetch starts and never drifts from the poll (#986).
const test = require('node:test');
const assert = require('node:assert');
const { createRefreshScheduler } = require('../format-utils.js');

// Deterministic fake clock + timer queue.
function fakeTime() {
    let t = 0;
    let seq = 0;
    const timers = new Map();
    return {
        now: () => t,
        setTimeout: (fn, ms) => { const id = ++seq; timers.set(id, { at: t + ms, fn }); return id; },
        clearTimeout: (id) => { timers.delete(id); },
        pending: () => timers.size,
        async advance(ms) {
            const end = t + ms;
            for (;;) {
                let next = null;
                for (const [id, x] of timers) if (x.at <= end && (!next || x.at < next[1].at)) next = [id, x];
                if (!next) break;
                timers.delete(next[0]);
                t = next[1].at;
                next[1].fn();
                await new Promise((r) => setImmediate(r));
            }
            t = end;
            await new Promise((r) => setImmediate(r));
        },
    };
}

function make(clock, fetchImpl) {
    const starts = [];
    const s = createRefreshScheduler({
        interval: 5000,
        now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
        fetch: () => { starts.push(clock.now()); return fetchImpl ? fetchImpl() : undefined; },
    });
    return { s, starts };
}

test('countdown reaches 0 exactly when each fetch starts', async () => {
    const clock = fakeTime();
    const { s, starts } = make(clock);
    await s.start();
    assert.deepStrictEqual(starts, [0]);
    const seen = [];
    for (let i = 0; i < 15; i++) {
        seen.push(s.secondsRemaining());
        await clock.advance(1000);
    }
    // 5,4,3,2,1 then the fetch fires at the 0 boundary and the cycle restarts.
    assert.deepStrictEqual(seen, [5, 4, 3, 2, 1, 5, 4, 3, 2, 1, 5, 4, 3, 2, 1]);
    assert.deepStrictEqual(starts, [0, 5000, 10000, 15000]);
});

test('slow fetch: countdown shows 0 while fetching and restarts after', async () => {
    const clock = fakeTime();
    let release;
    const { s, starts } = make(clock, () => new Promise((r) => { release = r; }));
    const first = s.start();
    assert.strictEqual(s.isFetching(), true);
    assert.strictEqual(s.secondsRemaining(), 0);
    await clock.advance(3000);               // still waiting on the network
    assert.strictEqual(s.secondsRemaining(), 0);
    assert.strictEqual(clock.pending(), 0);  // no second timer racing the fetch
    release();
    await first;
    assert.strictEqual(s.nextFetchAt(), 3000 + 5000);
    assert.strictEqual(s.secondsRemaining(), 5);
    assert.deepStrictEqual(starts, [0]);
});

test('exactly one timer exists at a time (no independent interval)', async () => {
    const clock = fakeTime();
    const { s } = make(clock);
    await s.start();
    for (let i = 0; i < 12; i++) {
        assert.ok(clock.pending() <= 1, `pending timers: ${clock.pending()}`);
        await clock.advance(700);
    }
});

test('manual refresh resets the cadence instead of adding a second poll', async () => {
    const clock = fakeTime();
    const { s, starts } = make(clock);
    await s.start();
    await clock.advance(2000);
    await s.refreshNow();
    assert.strictEqual(clock.pending(), 1);
    assert.strictEqual(s.secondsRemaining(), 5);
    await clock.advance(5000);
    assert.deepStrictEqual(starts, [0, 2000, 7000]);
});

test('pause stops polling and reports null; resume schedules a full interval', async () => {
    const clock = fakeTime();
    const { s, starts } = make(clock);
    await s.start();
    await clock.advance(1000);
    s.setPaused(true);
    assert.strictEqual(s.secondsRemaining(), null);
    assert.strictEqual(clock.pending(), 0);
    await clock.advance(20000);
    assert.deepStrictEqual(starts, [0]);
    s.setPaused(false);
    assert.strictEqual(s.secondsRemaining(), 5);
    await clock.advance(5000);
    assert.deepStrictEqual(starts, [0, 26000]);
});

test('a failing fetch still schedules the next poll', async () => {
    const clock = fakeTime();
    const { s, starts } = make(clock, () => Promise.reject(new Error('down')));
    await s.start();
    assert.strictEqual(s.secondsRemaining(), 5);
    await clock.advance(5000);
    assert.deepStrictEqual(starts, [0, 5000]);
});
