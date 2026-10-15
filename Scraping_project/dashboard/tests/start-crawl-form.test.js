// Run: node --test Scraping_project/dashboard/tests/
// #999: start-crawl form stub (seed URL, site profile, Submit gated on #147).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    START_CRAWL_API_TITLE,
    jobApiEnabled,
    validateSeedUrl,
    startCrawlSubmitState,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const features = fs.readFileSync(path.join(__dirname, '..', 'features.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

test('API tooltip string is the #147 gate', () => {
    assert.strictEqual(START_CRAWL_API_TITLE, 'Requires job API (#147)');
    assert.strictEqual(jobApiEnabled({ jobs: false }), false);
    assert.strictEqual(jobApiEnabled({}), false);
    assert.strictEqual(jobApiEnabled(null), false);
    assert.strictEqual(jobApiEnabled({ jobs: 'true' }), false);
    assert.strictEqual(jobApiEnabled({ jobs: true }), true);
});

test('seed URL accepts only http(s) with a host', () => {
    assert.strictEqual(validateSeedUrl('').ok, false);
    assert.strictEqual(validateSeedUrl('   ').ok, false);
    assert.strictEqual(validateSeedUrl('not a url').ok, false);
    assert.strictEqual(validateSeedUrl('javascript:alert(1)').ok, false);
    assert.strictEqual(validateSeedUrl('ftp://files.example.com/a').ok, false);
    assert.strictEqual(validateSeedUrl('http://').ok, false);
    const ok = validateSeedUrl('  https://uconn.edu/start?q=1  ');
    assert.strictEqual(ok.ok, true);
    assert.strictEqual(ok.href, 'https://uconn.edu/start?q=1');
    assert.strictEqual(validateSeedUrl('http://example.com').ok, true);
    assert.strictEqual(validateSeedUrl('http://127.0.0.1:8080/seed').ok, true);
});

test('Submit stays disabled until the job API flag is true', () => {
    const valid = 'https://uconn.edu/';
    const off = startCrawlSubmitState({ jobs: false }, valid);
    assert.strictEqual(off.disabled, true);
    assert.strictEqual(off.title, 'Requires job API (#147)');
    assert.strictEqual(off.api, false);
    assert.strictEqual(off.seed.ok, true);
    const badOff = startCrawlSubmitState({ jobs: false }, 'javascript:alert(1)');
    assert.strictEqual(badOff.disabled, true);
    assert.strictEqual(badOff.title, 'Requires job API (#147)');
    assert.strictEqual(badOff.seed.ok, false);
    const on = startCrawlSubmitState({ jobs: true }, valid);
    assert.strictEqual(on.disabled, false);
    assert.strictEqual(on.title, '');
    assert.strictEqual(on.api, true);
    const onBad = startCrawlSubmitState({ jobs: true }, 'nope');
    assert.strictEqual(onBad.disabled, false, 'flag, not the URL, owns the disabled state');
    assert.strictEqual(onBad.seed.ok, false);
});

test('Overview form has seed URL, profile select, and a disabled Submit', () => {
    const card = body.slice(body.indexOf('id="start-crawl-card"'), body.indexOf('id="tab-pipeline"'));
    assert.ok(card.includes('id="start-crawl-card"'));
    assert.match(card, /<h2 class="card-title">Start crawl<\/h2>/);
    assert.match(card, /<form id="start-crawl-form"/);
    assert.match(card, /id="start-crawl-seed"[^>]*type="url"/);
    assert.match(card, /<label for="start-crawl-seed">Seed URL<\/label>/);
    assert.match(card, /<label for="start-crawl-profile">Site profile<\/label>/);
    assert.match(card, /<select id="start-crawl-profile"/);
    assert.match(card, /<option value="uconn" selected>uconn \(uconn\.edu\)<\/option>/);
    assert.match(card, /<option value="example">example \(example\.com\)<\/option>/);
    const btn = card.match(/<button[^>]*id="start-crawl-submit"[^>]*>Submit<\/button>/)[0];
    assert.match(btn, /\bdisabled\b/);
    assert.match(btn, /title="Requires job API \(#147\)"/);
    assert.match(card, /id="start-crawl-api-note">Requires job API \(#147\)\.</);
    assert.match(card, /id="start-crawl-seed-error"/);
    assert.ok(!/innerHTML/.test(card));
});

test('app wires validation and never submits a crawl', () => {
    assert.match(app, /function syncStartCrawlForm\(\)/);
    assert.match(app, /function setupStartCrawlForm\(\)/);
    assert.match(app, /startCrawlSubmitState\(features, seedEl\.value\)/);
    assert.match(app, /event\.preventDefault\(\)/);
    assert.match(app, /seedEl\.setAttribute\('data-touched', '1'\)/);
    assert.match(app, /errorEl\.textContent = showError \? state\.seed\.message : ''/);
    assert.match(app, /This form does not call the job API/);
    const fn = app.slice(app.indexOf('function setupStartCrawlForm'), app.indexOf('function applyFeatureFlags'));
    assert.ok(!/fetch\s*\(/.test(fn));
    assert.ok(!/innerHTML/.test(fn));
    assert.ok(!/XMLHttpRequest/.test(fn));
    assert.match(features, /jobs:\s*false/);
    assert.match(app, /setupStartCrawlForm\(\)/);
});
