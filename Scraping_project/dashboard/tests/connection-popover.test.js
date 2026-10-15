// Run: node --test Scraping_project/dashboard/tests/
// #1020: the Status connection popover shows the resolved metrics URL.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('status popover names the metrics URL and can forget a remembered one', () => {
    assert.match(html, /id="connection-popover"/);
    assert.match(html, /<summary>Connection<\/summary>/);
    assert.match(html, /id="connection-metrics-url"/);
    assert.match(html, /id="connection-metrics-source"/);
    assert.match(html, /id="connection-forget"[^>]*hidden/);
    assert.match(html, /aria-label="Metrics connection"/);
    // The loading placeholder the a11y test matches stays intact.
    assert.match(html, /id="topbar-status">Loading…</);
});

test('app.js resolves query > storage > override and paints the popover (#1020)', () => {
    assert.match(app, /metricsEndpoint\(loc,/);
    assert.match(app, /storeGet\(STORE_METRICS_URL_KEY\)/);
    assert.match(app, /window\.CC_METRICS_URL/);
    assert.match(app, /if \(METRICS_ENDPOINT\.source === 'query'\) storeSet\(STORE_METRICS_URL_KEY, METRICS_URL\)/);
    assert.match(app, /urlEl\.textContent = METRICS_URL/);
    assert.match(app, /metricsEndpointLabel\(METRICS_ENDPOINT, loc\)/);
    assert.match(app, /storeRemove\(STORE_METRICS_URL_KEY\)/);
    assert.match(app, /searchParams\.delete\('metrics'\)/);
    assert.match(app, /renderConnectionPopover\(\)/);
});
