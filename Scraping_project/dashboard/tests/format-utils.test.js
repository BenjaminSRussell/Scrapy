// Run: node --test Scraping_project/dashboard/tests/
const test = require('node:test');
const assert = require('node:assert');
const { parseMetrics, formatNumber, formatBytes } = require('../format-utils.js');

test('parseMetrics skips comments and parses values', () => {
    const m = parseMetrics('# HELP x\nfoo_total 12\nbar{a="b"} 3.5\n\nbad line');
    assert.strictEqual(m.foo_total, 12);
    assert.strictEqual(m['bar{a="b"}'], 3.5);
    assert.ok(!('bad' in m));
});

test('formatNumber thresholds', () => {
    assert.strictEqual(formatNumber(999), '999');
    assert.strictEqual(formatNumber(1500), '1.5K');
    assert.strictEqual(formatNumber(2500000), '2.5M');
});

test('formatBytes thresholds', () => {
    assert.strictEqual(formatBytes(512), '512 B');
    assert.strictEqual(formatBytes(2048), '2.00 KB');
    assert.strictEqual(formatBytes(3 * 1048576), '3.00 MB');
    assert.strictEqual(formatBytes(1073741824), '1.00 GB');
});

const { formatEpochTime, countLabelValues } = require('../format-utils.js');

test('formatEpochTime shows a dash for missing, zero or bad timestamps (#364)', () => {
    for (const v of [undefined, null, 0, -5, NaN, 'abc']) {
        assert.strictEqual(formatEpochTime(v), '\u2014');
    }
    assert.notStrictEqual(formatEpochTime(1700000000), '\u2014');
    assert.ok(!/Invalid/.test(formatEpochTime(1700000000)));
});

test('countLabelValues counts distinct label values, null when unreported (#389)', () => {
    const m = parseMetrics([
        'delta_lake_records{table="seed_urls"} 5',
        'delta_lake_records{table="stage1_discovery"} 9',
        'delta_lake_records_total{table="seed_urls"} 5',
        'other{table="x"} 1',
    ].join('\n'));
    assert.strictEqual(countLabelValues(m, 'delta_lake_records', 'table'), 2);
    assert.strictEqual(countLabelValues({}, 'delta_lake_records', 'table'), null);
});

const { ratePerMinute, resolveMetricsUrl } = require('../format-utils.js');

test('parseMetrics sums labelled series under the bare name (#365)', () => {
    const m = parseMetrics([
        '# TYPE errors_total counter',
        'errors_total{stage="stage1",error_type="timeout"} 3',
        'errors_total{stage="stage2",error_type="dns"} 4',
        'queue_length{queue="a b, c"} 2',
        'queue_length{queue="esc\\"aped"} 5',
    ].join('\n'));
    assert.strictEqual(m.errors_total, 7);
    assert.strictEqual(m['errors_total{stage="stage1",error_type="timeout"}'], 3);
    assert.strictEqual(m.queue_length, 7);  // spaces/escaped quotes inside label values
    assert.strictEqual(m['queue_length{queue="a b, c"}'], 2);
});

test('parseMetrics: an unlabelled series wins over the labelled sum', () => {
    const m = parseMetrics('jobs_total 10\njobs_total{kind="x"} 3\njobs_total{kind="y"} 4');
    assert.strictEqual(m.jobs_total, 10);
    assert.strictEqual(m['jobs_total{kind="x"}'], 3);
});

test('parseMetrics ignores the optional timestamp column', () => {
    const m = parseMetrics('pages_total 42 1700000000000\nlabelled{a="b"} 1.5 1700000000000');
    assert.strictEqual(m.pages_total, 42);
    assert.strictEqual(m.labelled, 1.5);
});

test('parseMetrics keeps unlabelled behaviour and skips junk', () => {
    const m = parseMetrics('a 1\nb NaN\n  c   2.5  \n{oops} 3\n');
    assert.deepStrictEqual(Object.keys(m).sort(), ['a', 'c']);
    assert.strictEqual(m.c, 2.5);
});

test('ratePerMinute normalises to items per minute from real elapsed time (#141)', () => {
    assert.strictEqual(ratePerMinute(110, 100, 5000), 120);    // 10 in 5 s -> 120/min
    assert.strictEqual(ratePerMinute(110, 100, 60000), 10);
    assert.strictEqual(ratePerMinute(110, 100, 10000), 60);    // late refresh halves the rate
    assert.strictEqual(ratePerMinute(5, 100, 5000), 0);        // counter reset
    assert.strictEqual(ratePerMinute(undefined, 100, 5000), 0);
    assert.strictEqual(ratePerMinute(110, 100, 0), 0);
});

test('every stage rate uses the same unit: pages and URLs agree for equal deltas (#141)', () => {
    const urls = ratePerMinute(1060, 1000, 5000);
    const pages = ratePerMinute(60, 0, 5000);
    assert.strictEqual(urls, pages);
});

test('resolveMetricsUrl: same-origin proxy when served over http(s) (#141/#400)', () => {
    assert.strictEqual(resolveMetricsUrl({ protocol: 'http:', hostname: '10.0.0.7', search: '' }), '/api/metrics');
    assert.strictEqual(resolveMetricsUrl({ protocol: 'https:', hostname: 'cc.example', search: '' }), '/api/metrics');
    assert.strictEqual(resolveMetricsUrl({ protocol: 'file:', hostname: '', search: '' }),
        'http://localhost:9090/metrics');
    assert.strictEqual(resolveMetricsUrl(null), 'http://localhost:9090/metrics');
});

test('resolveMetricsUrl precedence: override, then http(s) ?metrics=', () => {
    const loc = { protocol: 'http:', hostname: 'h', search: '?metrics=http%3A%2F%2Fexporter%3A9100%2Fmetrics' };
    assert.strictEqual(resolveMetricsUrl(loc), 'http://exporter:9100/metrics');
    assert.strictEqual(resolveMetricsUrl(loc, '/proxy/metrics'), '/proxy/metrics');
    const bad = { protocol: 'http:', hostname: 'h', search: '?metrics=javascript:alert(1)' };
    assert.strictEqual(resolveMetricsUrl(bad), '/api/metrics');
});

// #906 / #910
const { splitLinks, metricsUrlProblem } = require('../format-utils.js');

test('splitLinks: script tags stay plain text, http(s) only become links', () => {
    const segs = splitLinks('<script>alert(1)</script> see https://example.edu/a?b=1. and javascript:alert(2)');
    assert.deepStrictEqual(segs, [
        { text: '<script>alert(1)</script> see ' },
        { text: 'https://example.edu/a?b=1', href: 'https://example.edu/a?b=1' },
        { text: '. and javascript:alert(2)' },
    ]);
    assert.ok(segs.every(s => !s.href || /^https?:\/\//.test(s.href)));
});

test('splitLinks: quotes and angle brackets end a URL; empty/null safe', () => {
    assert.deepStrictEqual(splitLinks('x http://a.edu/"onmouseover=1'), [
        { text: 'x ' }, { text: 'http://a.edu/', href: 'http://a.edu/' }, { text: '"onmouseover=1' },
    ]);
    assert.deepStrictEqual(splitLinks(''), []);
    assert.deepStrictEqual(splitLinks(null), []);
    assert.deepStrictEqual(splitLinks('(http://b.edu/x)'), [
        { text: '(' }, { text: 'http://b.edu/x', href: 'http://b.edu/x' }, { text: ')' },
    ]);
});

test('metricsUrlProblem: reports a rejected ?metrics=, silent otherwise', () => {
    assert.strictEqual(metricsUrlProblem({ search: '' }), null);
    assert.strictEqual(metricsUrlProblem(null), null);
    assert.strictEqual(metricsUrlProblem({ search: '?metrics=https://x.edu:9090/metrics' }), null);
    const msg = metricsUrlProblem({ search: '?metrics=javascript:alert(1)' });
    assert.match(msg, /only http\(s\)/);
    assert.match(msg, /javascript:alert\(1\)/);
    assert.match(metricsUrlProblem({ search: '?metrics=' }), /Ignored/);
});

test('queueDepthRows: Redis down is an error, not zeros (#401)', () => {
    const { queueDepthRows } = require('../format-utils.js');
    const down = queueDepthRows({ ok: false, error: 'redis unavailable: ConnectionError' });
    assert.deepStrictEqual(down.rows, []);
    assert.match(down.error, /redis unavailable/);
    assert.match(queueDepthRows(null).error, /unavailable/);
    const up = queueDepthRows({ ok: true, queues: [{ key: 'js_spider:priority_queue', type: 'zset', depth: 7 },
        { key: 'q2', type: 'none', depth: 0 }, { nope: 1 }] });
    assert.strictEqual(up.error, null);
    assert.deepStrictEqual(up.rows, [{ key: 'js_spider:priority_queue', type: 'zset', depth: 7 },
        { key: 'q2', type: 'none', depth: 0 }]);
});
