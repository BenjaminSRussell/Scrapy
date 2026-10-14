// Run: node --test Scraping_project/dashboard/tests/
// Static Free / Team / Enterprise catalog (#674). Not a billing gate.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'plans.html'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

test('plans.html is a static matrix with no inline script', () => {
    assert.strictEqual((html.match(/<h1\b/g) || []).length, 1);
    assert.match(html, /<main id="main">/);
    assert.match(html, /<table>/);
    assert.match(html, /<th scope="col" aria-current="true">Free<\/th>/);
    assert.match(html, /<th scope="col">Team<\/th>/);
    assert.match(html, /<th scope="col">Enterprise<\/th>/);
    assert.ok(!/<script\b/.test(html), 'catalog is static');
});

test('current plan badge is Free', () => {
    const badge = html.match(/<span id="current-plan-badge"[^>]*>/);
    assert.ok(badge, 'current plan badge');
    assert.match(badge[0], /data-plan="free"/);
    assert.match(html, /id="current-plan-badge"[^>]*>Free<\/span>/);
});

test('upgrade CTA is a mailto stub and does not claim to change quotas', () => {
    const cta = html.match(/<a class="upgrade-cta" href="([^"]+)">/);
    assert.ok(cta, 'upgrade CTA');
    assert.match(cta[1], /^mailto:ops@example\.com\?/);
    assert.match(html, /id="upgrade-stub"/);
    assert.match(html, /does not fill a null cap/);
    assert.match(html, /Null, or a missing block, does not stop the worker/);
});

test('metering copy keeps the shipped quotas and the three SLOs', () => {
    assert.match(html, /stage3_max_gpu_minutes_per_day/);
    assert.match(html, /httpcache_max_bytes/);
    assert.match(html, /REDIS_HOST/);
    assert.ok(!/REDIS_URL/.test(html));
    assert.match(html, /Stage2SuccessSLO/);
    assert.match(html, /QueueBacklog/);
    assert.match(html, /KafkaConsumerLagSLO/);
    assert.match(html, /250000/);
    assert.match(html, /10000/);
    assert.match(html, /No fourth SLO/);
});

test('Control Center links to the catalog', () => {
    assert.match(index, /href="plans\.html"/);
});
