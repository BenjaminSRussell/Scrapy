// Run: node --test Scraping_project/dashboard/tests/
// #665: a Prometheus flood must not be stored, must not throw, and must not
// stack dashboard paints.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    parseMetrics,
    countLabelValues,
    CONTROL_CENTER_METRICS,
    CC_LABEL_METRICS,
    CC_METRICS_SERIES_CAP,
    createFrameCoalescer,
    METRIC_UI_MAP,
    PREV_HOUR_AVG_KEYS,
} = require('../format-utils.js');

test('allowlist drops unknown series and still sums labelled ones (#665)', () => {
    const m = parseMetrics([
        'stage1_urls_discovered_total{site_id="default"} 3',
        'stage1_urls_discovered_total{site_id="other"} 4',
        'go_goroutines 99',
        'flood_metric{id="1"} 8',
        'delta_lake_records{table="seed_urls"} 5',
        'delta_lake_records{table="stage1_discovery"} 9',
        'pipeline_running 1',
    ].join('\n'), { allow: CONTROL_CENTER_METRICS });
    assert.strictEqual(m.stage1_urls_discovered_total, 7);
    assert.strictEqual(m.pipeline_running, 1);
    assert.strictEqual(countLabelValues(m, 'delta_lake_records', 'table'), 2);
    assert.ok(!('go_goroutines' in m));
    assert.ok(!('flood_metric' in m));
    assert.ok(!('stage1_urls_discovered_total{site_id="default"}' in m));
});

test('unlabelled allowlisted series still wins over the labelled sum (#665)', () => {
    const m = parseMetrics(
        'stage2_pages_analyzed_total{domain="x"} 100\nstage2_pages_analyzed_total 7\n',
        { allow: CONTROL_CENTER_METRICS }
    );
    assert.strictEqual(m.stage2_pages_analyzed_total, 7);
});

test('label cap keeps the full sum and bounds retained keys (#665)', () => {
    const lines = [];
    for (let i = 0; i < CC_METRICS_SERIES_CAP + 40; i++) {
        lines.push(`delta_lake_records{table="t${i}"} 1`);
    }
    lines.push('pipeline_running 0');
    const m = parseMetrics(lines.join('\n'), { allow: CONTROL_CENTER_METRICS, maxSeries: CC_METRICS_SERIES_CAP });
    assert.strictEqual(m.delta_lake_records, CC_METRICS_SERIES_CAP + 40);
    assert.strictEqual(m.pipeline_running, 0);
    const labelled = Object.keys(m).filter(k => k.startsWith('delta_lake_records{'));
    assert.strictEqual(labelled.length, CC_METRICS_SERIES_CAP);
    assert.strictEqual(countLabelValues(m, 'delta_lake_records', 'table'), CC_METRICS_SERIES_CAP);
});

test('unknown and malformed input never throws (#665)', () => {
    const boom = { toString() { throw new Error('nope'); } };
    const samples = [
        null, undefined, 0, {}, [], boom,
        '!!!\n{broken\n',
        'not_a_metric',
        'foo{bar="unterminated 1',
        'stage1_urls_discovered_total{domain="x"} notanumber',
        '123abc 1',
        ':just_colon 1',
        'pipeline_running +Inf',
        'pipeline_running NaN',
        '   # comment\n\nstage1_urls_queued_total 2\r\n',
    ];
    for (const sample of samples) {
        assert.doesNotThrow(() => parseMetrics(sample, { allow: CONTROL_CENTER_METRICS }));
        assert.doesNotThrow(() => parseMetrics(sample));
    }
    const m = parseMetrics('   # comment\n\nstage1_urls_queued_total 2\r\n{oops} 3\n', {
        allow: CONTROL_CENTER_METRICS,
    });
    assert.strictEqual(m.stage1_urls_queued_total, 2);
    assert.deepStrictEqual(Object.keys(m), ['stage1_urls_queued_total']);
});

test('10k-series flood stays small and finishes quickly (#665)', () => {
    const lines = ['stage1_urls_discovered_total 42', 'pipeline_running 1'];
    for (let i = 0; i < 10000; i++) {
        lines.push(`flood_metric{id="${i}",host="h${i % 50}"} ${i}`);
    }
    lines.push('not a series {{{');
    lines.push('delta_lake_records{table="seed_urls"} 5');
    const text = lines.join('\n');
    const t0 = process.hrtime.bigint();
    const m = parseMetrics(text, { allow: CONTROL_CENTER_METRICS, maxSeries: CC_METRICS_SERIES_CAP });
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    assert.ok(ms < 250, `parse took ${ms.toFixed(1)}ms`);
    assert.strictEqual(m.stage1_urls_discovered_total, 42);
    assert.strictEqual(m.pipeline_running, 1);
    assert.strictEqual(m.delta_lake_records, 5);
    assert.ok(!('flood_metric' in m));
    assert.ok(Object.keys(m).length < 20);
});

test('10k labelled samples of one allowlisted name still sum (#665)', () => {
    const lines = [];
    for (let i = 0; i < 10000; i++) lines.push(`stage1_urls_discovered_total{domain="d${i}"} 1`);
    lines.push('go_memstats_alloc_bytes 5');
    const t0 = process.hrtime.bigint();
    const m = parseMetrics(lines.join('\n'), { allow: CONTROL_CENTER_METRICS });
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    assert.ok(ms < 500, `labelled flood took ${ms.toFixed(1)}ms`);
    assert.strictEqual(m.stage1_urls_discovered_total, 10000);
    assert.strictEqual(Object.keys(m).length, 1);
});

test('README documents every allowlisted metric (#665)', () => {
    const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
    assert.match(readme, /CONTROL_CENTER_METRICS/);
    assert.match(readme, /CC_METRICS_SERIES_CAP/);
    for (const name of CONTROL_CENTER_METRICS) {
        assert.ok(readme.includes('`' + name + '`'), name);
    }
    for (const name of CC_LABEL_METRICS) {
        assert.ok(CONTROL_CENTER_METRICS.includes(name));
        assert.ok(readme.includes(name));
    }
    for (const row of METRIC_UI_MAP) {
        if (!/^[a-zA-Z_:][a-zA-Z0-9_:]*$/.test(row.metric)) continue;
        assert.ok(CONTROL_CENTER_METRICS.includes(row.metric), row.metric);
    }
    for (const name of Object.values(PREV_HOUR_AVG_KEYS)) {
        assert.ok(CONTROL_CENTER_METRICS.includes(name), name);
    }
});

test('app.js parses with the allowlist and paints on one frame (#665)', () => {
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(app, /parseMetrics\(text,\s*\{[^}]*allow:\s*CONTROL_CENTER_METRICS/s);
    assert.match(app, /maxSeries:\s*CC_METRICS_SERIES_CAP/);
    assert.match(app, /scheduleDashboardFrame\(\(\) => \{[\s\S]*?updateDashboard\(metrics\)/);
    assert.match(app, /notePollOutcome\(true\)/);
    assert.match(app, /scheduleDashboardFrame\.cancel\(\)/);
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    assert.match(html, /CONTROL_CENTER_METRICS/);
});

test('createFrameCoalescer keeps only the latest callback per frame (#665)', () => {
    const queued = [];
    const schedule = createFrameCoalescer((fn) => { queued.push(fn); });
    const ran = [];
    schedule(() => ran.push('a'));
    schedule(() => ran.push('b'));
    assert.strictEqual(queued.length, 1);
    assert.deepStrictEqual(ran, []);
    queued[0]();
    assert.deepStrictEqual(ran, ['b']);
    schedule(() => ran.push('c'));
    assert.strictEqual(queued.length, 2);
    queued[1]();
    assert.deepStrictEqual(ran, ['b', 'c']);
});

test('createFrameCoalescer cancel drops a pending paint (#665)', () => {
    const queued = [];
    const schedule = createFrameCoalescer((fn) => { queued.push(fn); });
    let ran = false;
    schedule(() => { ran = true; });
    schedule.cancel();
    queued[0]();
    assert.strictEqual(ran, false);
    schedule(() => { ran = 'later'; });
    queued[1]();
    assert.strictEqual(ran, 'later');
});

test('createFrameCoalescer falls back to a 16ms timer (#665)', () => {
    let delay = null;
    let fn = null;
    const schedule = createFrameCoalescer(null, (cb, ms) => { fn = cb; delay = ms; });
    let ran = false;
    schedule(() => { ran = true; });
    schedule(() => { ran = 'second'; });
    assert.strictEqual(delay, 16);
    assert.strictEqual(ran, false);
    fn();
    assert.strictEqual(ran, 'second');
});
