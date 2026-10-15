// Run: node --test Scraping_project/dashboard/tests/
// #949: rough kWh badge on the job energy scorecard, plus the disclaimer.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    parseMetrics,
    estimateKwh,
    formatEnergyBadge,
    jobEnergyScorecard,
    JOB_ENERGY_DISCLAIMER,
    JOB_ENERGY_EMPTY,
    JOB_ENERGY_BADGE_EMPTY,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('kWh is runtime hours times the stated 100 W draw', () => {
    assert.strictEqual(estimateKwh(3600), 0.1);
    assert.strictEqual(estimateKwh(3600, 200), 0.2);
    assert.strictEqual(estimateKwh(0), 0);
    assert.strictEqual(estimateKwh(3600, 0), 0);
    for (const bad of [-1, true, false, '3600', null, undefined, NaN, Infinity]) {
        assert.strictEqual(estimateKwh(bad), null);
    }
    assert.strictEqual(estimateKwh(3600, -5), 0.1);
    assert.strictEqual(formatEnergyBadge(0.1), '≈ 0.100 kWh');
    assert.strictEqual(formatEnergyBadge(0), '≈ 0 kWh');
    assert.strictEqual(formatEnergyBadge(0.0004), '≈ <0.001 kWh');
    assert.strictEqual(formatEnergyBadge(null), 'n/a');
});

test('scorecard from runtime keeps the disclaimer and skips a bad duration', () => {
    const card = jobEnergyScorecard({ crawl_job_id: 'job-a', runtime_seconds: 3600 });
    assert.strictEqual(card.missing, false);
    assert.strictEqual(card.badge, '≈ 0.100 kWh');
    assert.strictEqual(card.rows.length, 1);
    assert.strictEqual(card.rows[0].job, 'job-a');
    assert.strictEqual(card.rows[0].kwh, 0.1);
    assert.strictEqual(card.rows[0].badge, '≈ 0.100 kWh');
    assert.strictEqual(card.disclaimer, JOB_ENERGY_DISCLAIMER);
    assert.match(card.disclaimer, /Not a measured kWh reading/);
    assert.match(card.disclaimer, /not a carbon inventory/);
    assert.match(card.disclaimer, /not a cloud bill/);

    const empty = jobEnergyScorecard({ crawl_job_id: 'job-b', runtime_seconds: -5 });
    assert.strictEqual(empty.missing, true);
    assert.strictEqual(empty.badge, JOB_ENERGY_BADGE_EMPTY);
    assert.deepStrictEqual(empty.rows, []);
    assert.strictEqual(empty.disclaimer, JOB_ENERGY_DISCLAIMER);
    assert.strictEqual(empty.empty, JOB_ENERGY_EMPTY);
});

test('gauge series are per job and do not invent a row when runtime is absent', () => {
    const missing = jobEnergyScorecard(parseMetrics('stage1_urls_discovered_total 4\n'));
    assert.strictEqual(missing.missing, true);
    assert.strictEqual(missing.badge, 'No runtime');
    assert.deepStrictEqual(missing.rows, []);

    const metrics = parseMetrics([
        'pipeline_estimated_energy_kwh{stage="stage1",worker_id="a",domain="d",site_id="s",crawl_job_id="job-b"} 0.05',
        'pipeline_estimated_energy_kwh{stage="stage1",worker_id="b",domain="d",site_id="s",crawl_job_id="job-b"} 0.05',
        'pipeline_estimated_energy_kwh{stage="stage1",worker_id="a",domain="d",site_id="s",crawl_job_id="job-a"} 0.2',
        'pipeline_estimated_energy_kwh{stage="stage1",worker_id="a",domain="d",site_id="s",crawl_job_id="<script>"} 9',
        'scrapy_crawl_duration_seconds{spider="scout"} 99999',
    ].join('\n'));
    const card = jobEnergyScorecard(metrics);
    assert.strictEqual(card.missing, false);
    assert.deepStrictEqual(card.rows.map(row => [row.job, row.badge]), [
        ['<script>', '≈ 9.00 kWh'],
        ['job-a', '≈ 0.200 kWh'],
        ['job-b', '≈ 0.100 kWh'],
    ]);
    assert.strictEqual(card.badge, '≈ 9.30 kWh');
    assert.strictEqual(card.rows.some(row => row.job === 'scout'), false);
});

test('overview scorecard ships the badge, disclaimer, and textContent renderer', () => {
    assert.match(html, /id="job-energy-scorecard"/);
    assert.match(html, /id="job-energy-title">Job energy</);
    assert.match(html, /id="job-energy-badge"[^>]*>No runtime</);
    assert.match(html, /badge-energy/);
    assert.match(html, /\.badge-energy\s*\{[^}]*text-transform:\s*none/);
    assert.match(html, new RegExp('id="job-energy-disclaimer"[^>]*>' + JOB_ENERGY_DISCLAIMER.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.match(html, new RegExp('id="job-energy-empty"[^>]*>' + JOB_ENERGY_EMPTY.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.match(html, /id="job-energy-table"[^>]*hidden/);
    const start = app.indexOf('function renderJobEnergyScorecard');
    assert.ok(start > 0);
    const fn = app.slice(start, app.indexOf('\nfunction ', start + 10));
    assert.match(fn, /textContent = row\.job/);
    assert.match(fn, /textContent = row\.badge/);
    assert.match(fn, /jobEnergyScorecard\(metrics\)/);
    assert.ok(!fn.includes('innerHTML'));
    assert.match(app, /renderJobEnergyScorecard\(metrics\)/);
});
