// Run: node --test Scraping_project/dashboard/tests/
// #656: optional SSE for same-origin metrics, with a poll fallback.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { metricsStreamUrl, createLiveMetrics } = require('../format-utils.js');

test('metricsStreamUrl is only the same-origin proxy, and ?sse=0 opts out', () => {
    const loc = { search: '' };
    assert.strictEqual(metricsStreamUrl('/api/metrics', loc, true), '/api/metrics/stream');
    assert.strictEqual(metricsStreamUrl('/api/metrics', { search: '?tab=system' }, true), '/api/metrics/stream');
    assert.strictEqual(metricsStreamUrl('/api/metrics', { search: '?sse=0' }, true), null);
    assert.strictEqual(metricsStreamUrl('/api/metrics', { search: '?sse=off' }, true), null);
    assert.strictEqual(metricsStreamUrl('/api/metrics', { search: '?SSE=false' }, true), null);
    assert.strictEqual(metricsStreamUrl('/api/metrics', loc, false), null);
    assert.strictEqual(metricsStreamUrl('http://localhost:9090/metrics', loc, true), null);
    assert.strictEqual(metricsStreamUrl('http://exporter:9100/metrics', { search: '?metrics=http://exporter:9100/metrics' }, true), null);
    assert.strictEqual(metricsStreamUrl('/api/metrics', null, true), '/api/metrics/stream');
});

function FakeES(url) {
    this.url = url;
    this.readyState = 0;
    this.closed = false;
    this.listeners = {};
    FakeES.instances.push(this);
}
FakeES.instances = [];
FakeES.prototype.addEventListener = function (type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
};
FakeES.prototype.emit = function (type, data) {
    const ev = { type, data };
    (this.listeners[type] || []).forEach((fn) => fn(ev));
};
FakeES.prototype.close = function () {
    this.readyState = 2;
    this.closed = true;
};

function harness(url) {
    FakeES.instances = [];
    const samples = [];
    const upstream = [];
    const fallback = [];
    const live = createLiveMetrics({
        streamUrl: url,
        EventSource: url ? FakeES : null,
        onMetrics: (m) => samples.push(m),
        onUpstreamError: (e) => upstream.push(e),
        onFallback: (r) => fallback.push(r),
    });
    return { live, samples, upstream, fallback };
}

test('a metrics event is parsed and does not fall back', () => {
    const { live, samples, fallback } = harness('/api/metrics/stream?interval=5');
    live.start();
    const es = FakeES.instances[0];
    assert.strictEqual(es.url, '/api/metrics/stream?interval=5');
    es.emit('metrics', '{"pipeline_running":1,"errors_total":7}');
    assert.deepStrictEqual(samples, [{ pipeline_running: 1, errors_total: 7 }]);
    assert.strictEqual(live.usingPush(), true);
    assert.deepStrictEqual(fallback, []);
});

test('an upstream event stays on the stream', () => {
    const { live, upstream, fallback } = harness('/api/metrics/stream');
    live.start();
    FakeES.instances[0].emit('upstream', '{"ok":false,"error":"URLError"}');
    assert.strictEqual(upstream.length, 1);
    assert.strictEqual(upstream[0].message, 'URLError');
    assert.strictEqual(upstream[0].upstream, true);
    assert.strictEqual(live.usingPush(), true);
    assert.deepStrictEqual(fallback, []);
});

test('invalid JSON is an upstream error, not a transport fallback', () => {
    const { live, upstream, fallback } = harness('/api/metrics/stream');
    live.start();
    FakeES.instances[0].emit('metrics', '{');
    assert.strictEqual(upstream.length, 1);
    assert.strictEqual(live.usingPush(), true);
    assert.deepStrictEqual(fallback, []);
});

test('error before the first sample falls back once and closes', () => {
    const { live, fallback } = harness('/api/metrics/stream');
    live.start();
    const es = FakeES.instances[0];
    es.readyState = 2;
    es.onerror();
    assert.deepStrictEqual(fallback, ['sse-failed']);
    assert.strictEqual(es.closed, true);
    assert.strictEqual(live.usingPush(), false);
    assert.strictEqual(live.gaveUp(), true);
    es.onerror();
    assert.strictEqual(fallback.length, 1);
});

test('a reconnect after a sample stays; a terminal close falls back', () => {
    const { live, fallback } = harness('/api/metrics/stream');
    live.start();
    const es = FakeES.instances[0];
    es.emit('metrics', '{"pipeline_running":1}');
    es.readyState = 0;
    es.onerror();
    assert.deepStrictEqual(fallback, []);
    assert.strictEqual(live.usingPush(), true);
    es.readyState = 2;
    es.onerror();
    assert.deepStrictEqual(fallback, ['sse-closed']);
    assert.strictEqual(live.usingPush(), false);
});

test('stop does not count as a fallback; no EventSource polls immediately', () => {
    const stopped = harness('/api/metrics/stream');
    stopped.live.start();
    stopped.live.stop();
    assert.deepStrictEqual(stopped.fallback, []);
    assert.strictEqual(stopped.live.usingPush(), false);

    const missing = harness(null);
    missing.live.start();
    assert.deepStrictEqual(missing.fallback, ['unavailable']);
});

test('app.js uses the stream for proxied metrics and keeps the poll', () => {
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(app, /createLiveMetrics\(/);
    assert.match(app, /acceptMetricsSample\(metrics\)/);
    assert.match(app, /if \(!ssePrefer\)/);
    assert.match(app, /Live metrics stream unavailable; using refresh polling/);
    assert.match(app, /metricsStreamUrl\(/);
    assert.match(app, /__CC_FEATURES__\.sse === false/);
});
