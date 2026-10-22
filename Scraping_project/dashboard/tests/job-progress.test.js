// Run: node --test Scraping_project/dashboard/tests/
// #412: live job progress strip on Overview. Shows dashes, not zeros, when the
// exporter has not reported a series.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { jobProgress, parseMetrics } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

function overviewTab() {
    const start = body.indexOf('id="tab-overview"');
    const end = body.indexOf('id="tab-pipeline"');
    assert.ok(start > 0 && end > start);
    return body.slice(start, end);
}

const SAMPLE = [
    'stage1_urls_discovered_total 1200',
    'stage2_pages_analyzed_total 300',
    'stage3_summaries_created_total 42',
    'stage4_large_doc_summaries_total 7',
    'redis_queue_length{queue="priority_queue",site_id="uconn"} 15',
    'redis_queue_length{queue="stage2_queue",site_id="uconn"} 5',
    'pipeline_running 1',
].join('\n');

test('running job: stage totals, summed queue depth, URLs/min', () => {
    const p = jobProgress(parseMetrics(SAMPLE), { urls: 90, pages: 12, summaries: 0, largeDocs: 0 });
    assert.strictEqual(p.state, 'running');
    assert.deepStrictEqual(p.stages.map(s => s.total), [1200, 300, 42, 7]);
    assert.strictEqual(p.queueDepth, 20);
    assert.strictEqual(p.urlsPerMinute, 90);
    assert.deepStrictEqual(p.stages.map(s => s.perMinute), [90, 12, 0, 0]);
});

test('no metrics at all is the empty state with null values, never zeros', () => {
    for (const metrics of [null, undefined, {}, parseMetrics('# HELP x\n')]) {
        const p = jobProgress(metrics, null);
        assert.strictEqual(p.state, 'empty');
        assert.ok(p.stages.every(s => s.total === null && s.perMinute === null));
        assert.strictEqual(p.queueDepth, null);
    }
});

test('missing queue series is null even when stage counters exist', () => {
    const p = jobProgress(parseMetrics('stage1_urls_discovered_total 5'), { urls: 0 });
    assert.strictEqual(p.state, 'idle');
    assert.strictEqual(p.queueDepth, null);
    assert.strictEqual(p.stages[1].total, null);
});

test('stage counters but no movement and no running flag is idle', () => {
    const p = jobProgress(parseMetrics('stage2_pages_analyzed_total 9'), { urls: 0, pages: 0 });
    assert.strictEqual(p.state, 'idle');
});

test('non-finite rates count as not moving', () => {
    const p = jobProgress(parseMetrics('stage1_urls_discovered_total 9'), { urls: NaN });
    assert.strictEqual(p.state, 'idle');
    assert.strictEqual(p.urlsPerMinute, null);
});

test('overview has the progress strip with a Grafana deep-dive link', () => {
    const tab = overviewTab();
    assert.match(tab, /<section class="card job-progress" id="job-progress"/);
    for (const id of ['job-progress-urls-min', 'job-progress-queue', 'job-progress-stage1',
        'job-progress-stage2', 'job-progress-stage3', 'job-progress-stage4']) {
        assert.match(tab, new RegExp(`id="${id}"`), id);
    }
    const link = tab.match(/<a\b[^>]*id="job-progress-grafana"[^>]*>/);
    assert.ok(link, 'grafana link present');
    assert.match(link[0], /rel="noopener noreferrer"/);
    assert.match(tab, /id="job-progress-status"[^>]*aria-live="polite"/);
});

test('app.js renders the strip from the poll and falls back to empty on failure', () => {
    assert.match(app, /function renderJobProgress\(metrics, rates\)/);
    assert.match(app, /renderJobProgress\(metrics, rates\);/);
    assert.match(app, /renderJobProgress\(null, null\);/);
    assert.match(app, /document\.getElementById\('job-progress-grafana'\)/);
});
