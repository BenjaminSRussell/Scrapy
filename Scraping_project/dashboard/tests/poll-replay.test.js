// Run: node --test Scraping_project/dashboard/tests/
// #713: a follower tab renders the leader's poll. It makes no metrics fetch of its
// own and writes no activity lines of its own, so the leader's log is not copied
// into every open tab.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const SAMPLE = 'pipeline_running 1\nstage1_urls_discovered_total 5\n';

// Same sandbox shape as chart-downsample.test.js: app.js is evaluated, initialize() never runs.
function sandbox() {
    const fetches = [];
    const document = {
        readyState: 'loading',
        hidden: false,
        title: '',
        addEventListener() {},
        getElementById() { return null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    const ctx = {
        document,
        window: { addEventListener() {}, location: { protocol: 'file:', search: '', href: 'file:///' } },
        console: { log() {}, warn() {}, error() {} },
        fetch: (url) => {
            fetches.push(url);
            return Promise.resolve({ ok: true, status: 200, statusText: 'OK', text: async () => SAMPLE });
        },
        ...utils,
    };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, fetches };
}

test('a follower tab does not fetch METRICS_URL', async () => {
    const { ctx, fetches } = sandbox();
    vm.runInContext('pollLeader = { isLeader: () => false, publish() {} };', ctx);
    await vm.runInContext('fetchMetrics()', ctx);
    assert.deepStrictEqual(fetches, []);
});

test('the leader tab fetches once and publishes the poll it rendered', async () => {
    const { ctx, fetches } = sandbox();
    vm.runInContext('published = []; pollLeader = { isLeader: () => true, publish(p) { published.push(p); } };', ctx);
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(fetches.length, 1);
    assert.strictEqual(vm.runInContext('published.length', ctx), 1);
    assert.strictEqual(typeof vm.runInContext('published[0].text', ctx), 'string');
});

test('a replayed failure writes no activity line; the leader still writes its own', () => {
    const { ctx } = sandbox();
    vm.runInContext(`
        pollLeader = { isLeader: () => false, publish() {} };
        replayPollSnapshot({ error: 'Failed to fetch metrics: HTTP 502', type: 'danger' });
    `, ctx);
    assert.strictEqual(vm.runInContext('activityLog.length', ctx), 0);
    vm.runInContext(`
        pollLeader = { isLeader: () => true, publish() {} };
        addActivityLogItem('danger', 'Failed to fetch metrics: HTTP 502');
    `, ctx);
    assert.strictEqual(vm.runInContext('activityLog.length', ctx), 1);
});

test('replaying a good poll after an outage restores the connection without a log line', () => {
    const { ctx } = sandbox();
    vm.runInContext(`
        pollLeader = { isLeader: () => false, publish() {} };
        metricsWasDown = true;
        replayPollSnapshot({ text: ${JSON.stringify(SAMPLE)} });
    `, ctx);
    assert.strictEqual(vm.runInContext('activityLog.length', ctx), 0);
    assert.strictEqual(vm.runInContext('metricsWasDown', ctx), false, 'the outage flag is consumed silently');
    assert.notStrictEqual(vm.runInContext('lastMetricsAt', ctx), null, 'the follower still renders the poll');
});

test('a paused or hidden follower skips passive pushes until the user asks for a poll', () => {
    const { ctx } = sandbox();
    vm.runInContext('pollLeader = { isLeader: () => false, publish() {} }; refreshPaused = true;', ctx);
    vm.runInContext(`replayPollSnapshot({ text: ${JSON.stringify(SAMPLE)} });`, ctx);
    assert.strictEqual(vm.runInContext('lastMetricsAt', ctx), null, 'paused: passive push skipped');
    vm.runInContext('refreshPaused = false; document.hidden = true;', ctx);
    vm.runInContext(`replayPollSnapshot({ text: ${JSON.stringify(SAMPLE)} });`, ctx);
    assert.strictEqual(vm.runInContext('lastMetricsAt', ctx), null, 'hidden: passive push skipped');
    vm.runInContext(`pollReplayNext = true; replayPollSnapshot({ text: ${JSON.stringify(SAMPLE)} });`, ctx);
    assert.notStrictEqual(vm.runInContext('lastMetricsAt', ctx), null, 'a requested poll is shown');
});

test('Refresh now in a follower asks the leader to poll and fetches nothing itself', () => {
    const { ctx, fetches } = sandbox();
    vm.runInContext(`
        refreshCalls = 0; pokes = 0;
        refreshScheduler = { isFetching: () => false, isPaused: () => false, refreshNow() { refreshCalls += 1; } };
        pollLeader = { isLeader: () => false, publish() {}, requestPoll() { pokes += 1; } };
        requestRefresh();
    `, ctx);
    assert.strictEqual(vm.runInContext('pokes', ctx), 1);
    assert.strictEqual(vm.runInContext('refreshCalls', ctx), 0);
    assert.strictEqual(vm.runInContext('pollReplayNext', ctx), true);
    assert.deepStrictEqual(fetches, []);
});
