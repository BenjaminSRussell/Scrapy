// Run: node --test Scraping_project/dashboard/tests/queue-depth-viz.test.js
// #1002: queue-depth bars render from a parseMetrics map; threshold colours
// stay documented next to the component.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    parseMetrics, queueDepthView, queueDepthLevel, QUEUE_DEPTH_LEVELS, QUEUE_DEPTH_HOPS,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function bar(view, id) {
    const found = view.bars.find(b => b.id === id);
    assert.ok(found, id);
    return found;
}

test('threshold colours and lines are the documented tokens (#1002)', () => {
    assert.deepStrictEqual(
        Object.fromEntries(Object.entries(QUEUE_DEPTH_LEVELS).map(([k, v]) => [k, v.color])),
        { ok: '#10b981', warn: '#f59e0b', critical: '#ef4444', unknown: '#64748b' },
    );
    assert.deepStrictEqual(
        Object.fromEntries(Object.entries(QUEUE_DEPTH_LEVELS).map(([k, v]) => [k, v.label])),
        { ok: 'ok', warn: 'warning', critical: 'backlog', unknown: 'not reported' },
    );
    const hops = Object.fromEntries(QUEUE_DEPTH_HOPS.map(h => [h.id, h]));
    for (const id of ['stage1-stage2', 'stage1-js', 'stage2-stage3', 'stage2-stage4']) {
        assert.strictEqual(hops[id].warn, 8000);
        assert.strictEqual(hops[id].critical, 10000);
    }
    assert.strictEqual(hops['delta-write'].warn, 50);
    assert.strictEqual(hops['delta-write'].critical, 100);
    assert.strictEqual(queueDepthLevel(8000, 8000, 10000), 'ok');
    assert.strictEqual(queueDepthLevel(8001, 8000, 10000), 'warn');
    assert.strictEqual(queueDepthLevel(10000, 8000, 10000), 'warn');
    assert.strictEqual(queueDepthLevel(10001, 8000, 10000), 'critical');
    assert.strictEqual(queueDepthLevel(50, 50, 100), 'ok');
    assert.strictEqual(queueDepthLevel(100, 50, 100), 'warn');
    assert.strictEqual(queueDepthLevel(101, 50, 100), 'critical');
});

test('an empty metric map is not reported, never zeros (#1002)', () => {
    for (const metrics of [{}, null, undefined]) {
        const view = queueDepthView(metrics);
        assert.strictEqual(view.bars.length, QUEUE_DEPTH_HOPS.length);
        for (const b of view.bars) {
            assert.strictEqual(b.depth, null);
            assert.strictEqual(b.level, 'unknown');
            assert.strictEqual(b.color, '#64748b');
            assert.strictEqual(b.pct, 0);
            assert.notStrictEqual(b.depthText, '0');
            assert.strictEqual(b.levelLabel, 'not reported');
        }
        assert.strictEqual(view.summary, 'Queue depths not reported by the metrics exporter.');
    }
});

test('bars render from a metric map: max per queue, bare sum ignored (#1002)', () => {
    const metrics = parseMetrics([
        'redis_queue_length{queue="priority_queue",site_id="a",stage="exporter"} 100',
        'redis_queue_length{queue="priority_queue",site_id="b",stage="exporter"} 10001',
        'redis_queue_length{queue="stage1_discovered_urls",site_id="a"} 8001',
        'redis_queue_length{queue="custom_q",site_id="a"} 1',
        'redis_queue_length{queue="custom_q",site_id="b"} 3',
        'redis_queue_length{queue="esc\\"aped",site_id="a"} 4',
        'delta_write_queue_depth 40',
        'js_priority_queue_size{queue="js_spider:priority_queue"} 20000',
    ].join('\n'));
    assert.ok(metrics.redis_queue_length > 10001, 'bare name is the labelled sum');
    const view = queueDepthView(metrics);
    const js = bar(view, 'stage1-js');
    assert.strictEqual(js.depth, 10001);
    assert.strictEqual(js.level, 'critical');
    assert.strictEqual(js.color, '#ef4444');
    assert.strictEqual(js.pct, 100);
    const s12 = bar(view, 'stage1-stage2');
    assert.strictEqual(s12.depth, 8001);
    assert.strictEqual(s12.level, 'warn');
    assert.strictEqual(s12.color, '#f59e0b');
    assert.ok(Math.abs(s12.pct - 80.01) < 1e-9);
    assert.strictEqual(bar(view, 'stage2-stage3').depth, null);
    assert.strictEqual(bar(view, 'stage2-stage4').level, 'unknown');
    const delta = bar(view, 'delta-write');
    assert.strictEqual(delta.depth, 40);
    assert.strictEqual(delta.level, 'ok');
    assert.strictEqual(delta.color, '#10b981');
    assert.strictEqual(delta.pct, 40);
    const names = view.bars.map(b => b.label);
    assert.ok(names.indexOf('custom_q') > names.indexOf('Delta write'));
    assert.deepStrictEqual(names.filter(n => n === 'custom_q' || n === 'esc"aped'), ['custom_q', 'esc"aped']);
    assert.strictEqual(bar(view, 'redis:custom_q').depth, 3);
    assert.strictEqual(bar(view, 'redis:custom_q').level, 'ok');
    assert.match(view.summary, /^Backlog: Stage 1 → JS$/);
    assert.match(js.aria, /10001 backlog/);
    assert.match(js.aria, /Warning above 8000, backlog above 10000/);
});

test('labelled delta depth is the max, not the bare sum (#1002)', () => {
    const metrics = parseMetrics([
        'delta_write_queue_depth{worker="a"} 40',
        'delta_write_queue_depth{worker="b"} 80',
    ].join('\n'));
    assert.strictEqual(metrics.delta_write_queue_depth, 120);
    const delta = bar(queueDepthView(metrics), 'delta-write');
    assert.strictEqual(delta.depth, 80);
    assert.strictEqual(delta.level, 'warn');
    assert.strictEqual(delta.pct, 80);
    assert.match(queueDepthView(metrics).summary, /^Warning: Delta write$/);
});

test('js_priority_queue_size is used only when redis depth is absent (#1002)', () => {
    const onlyJs = queueDepthView(parseMetrics('js_priority_queue_size{queue="js_spider:priority_queue"} 9000\n'));
    const js = bar(onlyJs, 'stage1-js');
    assert.strictEqual(js.depth, 9000);
    assert.strictEqual(js.level, 'warn');
    assert.strictEqual(js.pct, 90);
    const both = queueDepthView(parseMetrics([
        'redis_queue_length{queue="priority_queue",site_id="a"} 10',
        'js_priority_queue_size{queue="js_spider:priority_queue"} 20000',
    ].join('\n')));
    assert.strictEqual(bar(both, 'stage1-js').depth, 10);
    assert.strictEqual(both.summary, 'Reported queues are under the warning line.');
});

test('overview markup and README document the same colours and lines (#1002)', () => {
    assert.match(html, /id="queue-depth-viz"/);
    assert.match(html, /id="queue-depth-viz-legend"/);
    assert.match(html, /id="queue-depth-viz-table"/);
    for (const hex of ['#10b981', '#f59e0b', '#ef4444', '#64748b']) {
        assert.ok(html.includes(hex), hex);
        assert.ok(readme.includes(hex), hex);
    }
    assert.match(html, /warn above 8000 and backlog above 10000/);
    assert.match(html, /warns above 50 and backlogs above 100/);
    assert.match(readme, /8000/);
    assert.match(readme, /10000/);
    assert.match(readme, /not a fourth SLO/);
    const fn = app.slice(app.indexOf('function renderQueueDepthViz'), app.indexOf('function setReportedValue'));
    assert.match(fn, /queueDepthView\(metrics\)/);
    assert.match(fn, /textContent = bar\.label/);
    assert.match(fn, /textContent = bar\.meta/);
    assert.ok(!/innerHTML/.test(fn));
    const forcedStart = html.indexOf('@media (forced-colors: active)');
    const forced = html.slice(forcedStart, html.indexOf('@media', forcedStart + 10));
    assert.match(forced, /\.qd-fill\.qd-critical\s*\{[^}]*dashed/);
    assert.match(forced, /\.qd-fill\.qd-warn\s*\{[^}]*dotted/);
});
