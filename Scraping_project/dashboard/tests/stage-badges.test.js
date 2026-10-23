// Run: node --test Scraping_project/dashboard/tests/
// #384: Overview stage-card badges (and the System Health stage rows) are
// driven by live metrics: Running when the stage's rate is positive (or work
// is queued behind it), Standby when idle with an empty queue, Unknown when
// the counter is not exported or the fetch failed.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { stageBadgeStates, parseMetrics } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

const IDLE = [
    'stage1_urls_discovered_total 1200',
    'stage2_pages_analyzed_total 300',
    'stage3_summaries_created_total 42',
    'stage4_large_doc_summaries_total 7',
].join('\n');

test('idle metrics: every reported stage is standby', () => {
    const rates = { urls: 0, pages: 0, summaries: 0, largeDocs: 0 };
    assert.deepStrictEqual(stageBadgeStates(parseMetrics(IDLE), rates),
        ['standby', 'standby', 'standby', 'standby']);
});

test('idle -> active: a positive rate flips that stage to running', () => {
    const idle = stageBadgeStates(parseMetrics(IDLE), { urls: 0, pages: 0, summaries: 0, largeDocs: 0 });
    const active = stageBadgeStates(parseMetrics(IDLE), { urls: 0, pages: 0, summaries: 3.5, largeDocs: 0 });
    assert.strictEqual(idle[2], 'standby');
    assert.strictEqual(active[2], 'running');
    assert.deepStrictEqual(active, ['standby', 'standby', 'running', 'standby']);
});

test('zero rate with a queue backlog is running, not standby', () => {
    const metrics = parseMetrics(IDLE + '\nredis_queue_length{queue="stage2_queue",site_id="uconn"} 5');
    const states = stageBadgeStates(metrics, { urls: 0, pages: 0, summaries: 0, largeDocs: 0 });
    assert.strictEqual(states[1], 'running');
});

test('stage 1 backlog comes from stage1_urls_queued_total or the priority queue', () => {
    const rates = { urls: 0, pages: 0, summaries: 0, largeDocs: 0 };
    assert.strictEqual(stageBadgeStates(parseMetrics(IDLE + '\nstage1_urls_queued_total 9'), rates)[0], 'running');
    assert.strictEqual(stageBadgeStates(parseMetrics(IDLE + '\nredis_queue_length{queue="priority_queue",site_id="uconn"} 2'), rates)[0], 'running');
});

test('metrics unavailable: missing counter series is unknown, never guessed', () => {
    assert.deepStrictEqual(stageBadgeStates(null, null),
        ['unknown', 'unknown', 'unknown', 'unknown']);
    assert.deepStrictEqual(stageBadgeStates(parseMetrics('stage1_urls_discovered_total 5'), { urls: 0 }),
        ['standby', 'unknown', 'unknown', 'unknown']);
});

test('Overview stage badges carry ids and no hardcoded Running state', () => {
    for (const id of ['overview-s1-badge', 'overview-s2-badge', 'overview-s3-badge', 'overview-s4-badge']) {
        assert.ok(html.includes(`id="${id}"`), `missing ${id}`);
    }
    assert.ok(!/<span class="card-badge badge-success">Running<\/span>/.test(html));
});

test('app.js drives badges from updateDashboard and covers failure + health rows', () => {
    assert.match(app, /applyStageBadges\(metrics, rates\)/);
    assert.match(app, /stageBadgeStates\(metrics, rates\)/);
    // fetch-failure path: badges become Unknown/danger, stage health rows Unknown
    assert.match(app, /b\.textContent = 'Unknown'/);
    assert.match(app, /setHealthTile\(`stage\$\{i\}-health`, `Stage \$\{i\}`, false, 'Unknown'\)/);
    // health rows exist for all four stages
    for (let i = 1; i <= 4; i++) assert.ok(html.includes(`id="stage${i}-health"`));
});
