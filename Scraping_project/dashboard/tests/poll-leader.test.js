// Run: node --test Scraping_project/dashboard/tests/
// #713: one metrics poller per origin. Tabs elect a leader over a BroadcastChannel.
// Only the leader fetches, followers render its pushes, and a silent or closed
// leader is replaced within two intervals.
const test = require('node:test');
const assert = require('node:assert');
const { createPollLeader } = require('../format-utils.js');

const INTERVAL = 5000;

// Fake timers fire only inside advance(). Channel messages are queued on post and
// delivered after each timer, as a real BroadcastChannel delivers them later. With
// batch = true they are delivered only at the end, so timers can race.
function fakeClock() {
    let now = 0;
    let seq = 0;
    const timers = new Map();
    const inbox = [];
    const clock = {
        now: () => now,
        setTimeout(fn, ms) { const h = ++seq; timers.set(h, { at: now + ms, fn, every: 0 }); return h; },
        clearTimeout(h) { timers.delete(h); },
        setInterval(fn, ms) { const h = ++seq; timers.set(h, { at: now + ms, fn, every: ms }); return h; },
        clearInterval(h) { timers.delete(h); },
        deliver(channel, data) { inbox.push({ channel, data }); },
        flush() {
            while (inbox.length) {
                const { channel, data } = inbox.shift();
                if (!channel.closed) for (const fn of channel.listeners) fn({ data });
            }
        },
        advance(ms, batch = false) {
            const end = now + ms;
            clock.flush();
            for (;;) {
                let next = null;
                for (const [h, tm] of timers) {
                    if (tm.at <= end && (next === null || tm.at < timers.get(next).at)) next = h;
                }
                if (next === null) break;
                const tm = timers.get(next);
                now = tm.at;
                if (tm.every) tm.at += tm.every; else timers.delete(next);
                tm.fn();
                if (!batch) clock.flush();
            }
            now = end;
            clock.flush();
        },
    };
    return clock;
}

// Every open channel hears the others (never itself), like same-name channels in one origin.
function fakeNetwork(clock) {
    const open = new Set();
    return {
        open() {
            const channel = {
                closed: false,
                listeners: [],
                addEventListener(type, fn) { if (type === 'message') this.listeners.push(fn); },
                postMessage(data) {
                    if (this.closed) throw new Error('InvalidStateError: channel is closed');
                    for (const other of open) if (other !== this) clock.deliver(other, JSON.parse(JSON.stringify(data)));
                },
                close() { this.closed = true; open.delete(this); },
            };
            open.add(channel);
            return channel;
        },
    };
}

// One dashboard tab. It listens only once started, as a real tab does after
// setupPollLeader(). Its timers stop when it dies, as a crashed tab's would.
function tab(name, net, clock, { eligible = true, channel = true } = {}) {
    const t = { name, dead: false, polls: 0, leads: [], snaps: [], pokes: 0, poll: null, channel: null };
    const guard = (fn) => () => { if (!t.dead) fn(); };
    // The app's scheduler: a leader fetches on each tick and publishes; a follower never fetches.
    function poll() {
        if (t.dead || !t.poll || !t.poll.isLeader()) return;
        t.polls += 1;
        t.poll.publish({ text: `poll ${t.polls} from ${name}` });
    }
    t.start = () => {
        t.channel = channel ? net.open() : null;
        t.poll = createPollLeader({
            channel: t.channel,
            id: name,
            interval: INTERVAL,
            electionMs: 250,
            eligible,
            setTimeout: (fn, ms) => clock.setTimeout(guard(fn), ms),
            clearTimeout: (h) => clock.clearTimeout(h),
            setInterval: (fn, ms) => clock.setInterval(guard(fn), ms),
            clearInterval: (h) => clock.clearInterval(h),
            onRoleChange(leading) { t.leads.push([clock.now(), leading]); if (leading) poll(); },
            onSnapshot(payload) { t.snaps.push(payload); },
            onPollRequest() { t.pokes += 1; },
        });
        clock.setInterval(guard(poll), INTERVAL);
        t.poll.start();
    };
    t.kill = () => { t.dead = true; if (t.channel) t.channel.close(); }; // crash: no bye
    t.close = () => { t.poll.stop(); t.dead = true; if (t.channel) t.channel.close(); }; // pagehide
    return t;
}

const promotion = (t) => t.leads.find(([, leading]) => leading);

test('a lone tab leads once the election window passes', () => {
    const clock = fakeClock();
    const a = tab('a', fakeNetwork(clock), clock);
    a.start();
    assert.strictEqual(a.poll.isLeader(), false);
    clock.advance(250);
    assert.strictEqual(a.poll.isLeader(), true);
    assert.strictEqual(a.polls, 1, 'a new leader polls at once');
});

test('without BroadcastChannel a tab leads at once', () => {
    const clock = fakeClock();
    const a = tab('a', fakeNetwork(clock), clock, { channel: false });
    a.start();
    assert.strictEqual(a.poll.isLeader(), true);
    assert.strictEqual(a.polls, 1);
});

test('two tabs: only the leader polls, and the follower renders every push', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    const b = tab('b', net, clock);
    a.start();
    clock.advance(300); // a leads
    b.start(); // b says hello, and a answers with its heartbeat and last poll
    clock.advance(3 * INTERVAL);
    assert.strictEqual(b.poll.isLeader(), false);
    assert.strictEqual(b.polls, 0, 'the follower never fetches');
    assert.strictEqual(a.polls, 4, 'one poll per interval (the claim poll plus three ticks)');
    assert.strictEqual(b.snaps.length, 4, 'the follower gets the join snapshot and each push');
});

test('a leader that stops heartbeating is replaced within two intervals', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    const b = tab('b', net, clock);
    a.start();
    clock.advance(300);
    b.start();
    clock.advance(2 * INTERVAL + 500);
    const killedAt = clock.now();
    a.kill(); // crash: no bye, no more heartbeats
    clock.advance(3 * INTERVAL);
    const up = promotion(b);
    assert.ok(up, 'the follower was promoted');
    assert.ok(up[0] > killedAt, 'not before the leader went quiet');
    assert.ok(up[0] <= killedAt + 2 * INTERVAL, `promoted at ${up[0]}, killed at ${killedAt}`);
    assert.strictEqual(b.poll.isLeader(), true);
    assert.ok(b.polls >= 1, 'the new leader polls at once');
});

test('a clean close (bye) hands over after the election window', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    const b = tab('b', net, clock);
    a.start();
    clock.advance(300);
    b.start();
    clock.advance(INTERVAL);
    const closedAt = clock.now();
    a.close();
    clock.advance(INTERVAL);
    const up = promotion(b);
    assert.ok(up, 'the follower was promoted');
    assert.ok(up[0] - closedAt <= 250, `promoted ${up[0] - closedAt} ms after the close`);
    assert.strictEqual(b.poll.isLeader(), true);
});

test('a hidden or paused leader yields, and nobody polls while every tab is hidden', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    const b = tab('b', net, clock);
    a.start();
    clock.advance(300);
    b.start();
    clock.advance(INTERVAL);
    a.poll.setEligible(false); // a is hidden or paused: it hands over
    clock.advance(300);
    assert.strictEqual(a.poll.isLeader(), false);
    assert.strictEqual(b.poll.isLeader(), true);
    b.poll.setEligible(false); // now both are hidden
    const before = a.polls + b.polls;
    clock.advance(3 * INTERVAL);
    assert.strictEqual(a.poll.isLeader() || b.poll.isLeader(), false);
    assert.strictEqual(a.polls + b.polls, before, 'no fetch while no tab is eligible');
    a.poll.setEligible(true); // a is visible again and leads
    clock.advance(300);
    assert.strictEqual(a.poll.isLeader(), true);
});

test('two tabs that claim at the same moment settle on the lower id', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const b = tab('b', net, clock);
    const a = tab('a', net, clock);
    b.start();
    a.start();
    clock.advance(300, true); // both election timers fire before either heartbeat is delivered
    assert.strictEqual(a.poll.isLeader(), true);
    assert.strictEqual(b.poll.isLeader(), false);
    assert.deepStrictEqual(b.leads.map(([, leading]) => leading), [true, false]);
});

test('a tab that opens later gets the latest poll at once and does not poll', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    a.start();
    clock.advance(2 * INTERVAL);
    const c = tab('c', net, clock);
    c.start();
    clock.advance(10);
    assert.strictEqual(c.poll.isLeader(), false);
    assert.strictEqual(c.snaps.length, 1, 'the leader answers hello with its last poll');
    assert.strictEqual(c.polls, 0);
});

test('a follower can ask the leader to poll now', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    const b = tab('b', net, clock);
    a.start();
    clock.advance(300);
    b.start();
    clock.advance(10);
    b.poll.requestPoll();
    clock.advance(10);
    assert.strictEqual(a.pokes, 1);
    assert.strictEqual(b.pokes, 0);
});

test('a new interval set on the leader moves the followers failover window', () => {
    const clock = fakeClock();
    const net = fakeNetwork(clock);
    const a = tab('a', net, clock);
    const b = tab('b', net, clock);
    a.start();
    clock.advance(300);
    b.start();
    clock.advance(10);
    const changedAt = clock.now();
    a.poll.setPollInterval(10000); // heartbeats now carry the 10 s interval
    clock.advance(10);
    a.kill();
    clock.advance(4 * INTERVAL);
    const up = promotion(b);
    assert.ok(up, 'the follower was promoted');
    assert.strictEqual(up[0] - changedAt, 2 * 10000);
});
