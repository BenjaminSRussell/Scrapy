// Run: node --test Scraping_project/dashboard/tests/
// #1040: System Health links to Grafana home, not a dashboard UID.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { grafanaHomeUrl } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

function systemTab() {
    const start = body.indexOf('id="tab-system"');
    const end = body.indexOf('id="tab-activity"');
    assert.ok(start > 0 && end > start);
    return body.slice(start, end);
}

test('System Health card links to Grafana home (#1040)', () => {
    const tab = systemTab();
    assert.match(tab, /id="grafana-link-card"/);
    assert.match(tab, /<h2 class="card-title">Open Grafana analytics<\/h2>/);
    const link = tab.match(/<a\b[^>]*id="grafana-analytics-link"[^>]*>/);
    assert.ok(link, 'link present on System Health');
    const href = link[0].match(/href="([^"]+)"/)[1];
    assert.strictEqual(href, 'http://localhost:3000/');
    assert.ok(!href.includes('/d/'), 'home, not a dashboard UID');
    assert.match(link[0], /rel="noopener noreferrer"/);
    assert.match(tab, /Open Grafana analytics/);
    assert.match(tab, /id="grafana-analytics-base"/);
});

test('app.js applies the configured Grafana base on init', () => {
    assert.match(app, /function applyGrafanaLink\(/);
    assert.match(app, /grafanaHomeUrl\(window\.__CC_GRAFANA_URL__/);
    assert.match(app, /applyGrafanaLink\(\)/);
});

test('grafanaHomeUrl keeps a base and drops a dashboard path', () => {
    assert.strictEqual(grafanaHomeUrl(''), 'http://localhost:3000/');
    assert.strictEqual(grafanaHomeUrl('http://localhost:3000'), 'http://localhost:3000/');
    assert.strictEqual(grafanaHomeUrl('https://graf.example/grafana'), 'https://graf.example/grafana/');
    assert.strictEqual(
        grafanaHomeUrl('http://localhost:3000/d/scraping-pipeline-health?orgId=1#top'),
        'http://localhost:3000/');
    assert.strictEqual(
        grafanaHomeUrl('https://graf.example/grafana/d/pipeline-slos'),
        'https://graf.example/grafana/');
    assert.strictEqual(grafanaHomeUrl('http://[::1]:3000/'), 'http://[::1]:3000/');
    for (const bad of ['javascript:alert(1)', 'http://user:pw@localhost:3000/', 'file:///tmp', 'not a url', 'http://localhost:0/']) {
        assert.strictEqual(grafanaHomeUrl(bad), 'http://localhost:3000/', bad);
    }
    assert.strictEqual(grafanaHomeUrl('javascript:alert(1)', 'http://localhost:3000/'), 'http://localhost:3000/');
});
