// Run: node --test Scraping_project/dashboard/tests/
// #1052: scrape-target host summary. Empty when the metric is missing.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { parseMetrics, topScrapeTargets, SCRAPE_TARGET_EMPTY } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('missing metric is an empty state, not an invented host list', () => {
    for (const input of [undefined, null, {}, { ok: false, error: 'down' }, { ok: true, targets: [] }, { hosts: [] }]) {
        const summary = topScrapeTargets(input);
        assert.strictEqual(summary.missing, true, JSON.stringify(input));
        assert.deepStrictEqual(summary.rows, []);
        assert.strictEqual(summary.empty, SCRAPE_TARGET_EMPTY);
    }
    const unrelated = parseMetrics([
        'urls_discovered_total{domain="uconn.edu",stage="stage1"} 50',
        'circuit_breaker_state{host="down.example",name="redis"} 1',
        'scrape_targets 12',
    ].join('\n'));
    assert.strictEqual(topScrapeTargets(unrelated).missing, true);
});

test('ranks host and domain labels and drops sentinels', () => {
    const metrics = parseMetrics([
        'scrape_targets{host="b.example.edu"} 2',
        'scrape_targets{host="a.example.edu"} 10',
        'scrape_targets{host="unknown"} 99',
        'scrape_targets{host="-"} 1',
        'scrape_targets{host="rejected"} 4',
        'other_metric{host="nope.example"} 100',
        'urls_by_domain{domain="lib.uconn.edu"} 4',
        'seed_hosts_total{host="a.example.edu",stage="stage2"} 3',
        'pipeline_scrape_targets{domain="d.edu",host="h.edu"} 1',
    ].join('\n'));
    const summary = topScrapeTargets(metrics);
    assert.strictEqual(summary.missing, false);
    assert.deepStrictEqual(summary.rows.map(r => [r.host, r.value]), [
        ['a.example.edu', 13],
        ['lib.uconn.edu', 4],
        ['b.example.edu', 2],
        ['h.edu', 1],
    ]);
});

test('API stub lists hosts and keeps raw text for the renderer', () => {
    const summary = topScrapeTargets({
        ok: true,
        targets: [
            { host: '<script>alert(1)</script>', value: 1 },
            { domain: 'c.edu', count: 5 },
            { host: 'unknown', value: 9 },
        ],
    }, 8);
    assert.strictEqual(summary.missing, false);
    assert.strictEqual(summary.rows[0].host, 'c.edu');
    assert.strictEqual(summary.rows[0].value, 5);
    assert.strictEqual(summary.rows[1].host, '<script>alert(1)</script>');
    assert.strictEqual(topScrapeTargets({ hosts: ['b.edu', 'a.edu'] }, 1).rows.length, 1);
    assert.strictEqual(topScrapeTargets(['b.edu', 'a.edu']).rows[0].host, 'a.edu');
});

test('overview card ships the empty state and app.js does not use innerHTML', () => {
    assert.match(html, /id="scrape-targets-card"/);
    assert.match(html, /id="scrape-targets-title">Scrape targets</);
    assert.match(html, new RegExp('id="scrape-targets-empty"[^>]*>' + SCRAPE_TARGET_EMPTY.replace('.', '\\.')));
    assert.match(html, /id="scrape-targets-table"[^>]*hidden/);
    const start = app.indexOf('function renderScrapeTargets');
    assert.ok(start > 0);
    const fn = app.slice(start, app.indexOf('\nfunction ', start + 10));
    assert.match(fn, /textContent = row\.host/);
    assert.match(fn, /topScrapeTargets\(metrics\)/);
    assert.ok(!fn.includes('innerHTML'));
    assert.match(app, /renderScrapeTargets\(metrics\)/);
});
