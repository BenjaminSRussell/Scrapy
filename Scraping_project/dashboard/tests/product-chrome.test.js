// Run: node --test Scraping_project/dashboard/tests/
// #947: the chrome and the serve.py banner name this page as the ops Control
// Center and point Analytics at the monitoring-profile Grafana. The pack URL
// stays the other board. Both files use the same two URL strings.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const serve = fs.readFileSync(path.join(__dirname, '..', 'serve.py'), 'utf8');
const MONITORING = 'http://localhost:3000';
const PACK = 'http://127.0.0.1:3001';

test('topbar states ops Control Center and links Analytics to Grafana :3000 (#947)', () => {
    assert.match(html, /Ops Control Center \(port 8080\), not Grafana/);
    const link = html.match(/<a\b[^>]*id="grafana-analytics-link"[^>]*>[\s\S]*?<\/a>/);
    assert.ok(link, 'analytics link present');
    assert.match(link[0], new RegExp(`href="${MONITORING}/"`));
    assert.match(link[0], /rel="noopener noreferrer"/);
    assert.match(link[0], />Analytics: Grafana</);
    assert.match(link[0], /aria-describedby="cc-product-note"/);
});

test('footer help text names both documented Grafana origins (#947)', () => {
    const note = html.match(/<p id="cc-product-note">([\s\S]*?)<\/p>/);
    assert.ok(note, 'product note present');
    assert.match(note[1], /ops Control Center/);
    assert.match(note[1], /http:\/\/127\.0\.0\.1:8080\//);
    assert.match(note[1], /does not embed Grafana/);
    assert.match(note[1], new RegExp(MONITORING.replace(/[.]/g, '\\.')));
    assert.match(note[1], new RegExp(PACK.replace(/[.]/g, '\\.')));
    assert.match(note[1], /not port 3000 and not this page/);
});

test('serve.py banner uses the same Grafana URLs as the chrome (#947)', () => {
    assert.match(serve, /GRAFANA_MONITORING_URL = "http:\/\/localhost:3000"/);
    assert.match(serve, /GRAFANA_PACK_URL = "http:\/\/127\.0\.0\.1:3001"/);
    assert.match(serve, /ops Control Center, not Grafana/);
    assert.match(serve, /Grafana \{GRAFANA_MONITORING_URL\}/);
    assert.match(serve, /Grafana \{GRAFANA_PACK_URL\}/);
    assert.doesNotMatch(serve, /http:\/\/localhost:3001 \(separate analytics\)/);
    assert.doesNotMatch(serve, /separate from Grafana \(port 3001\)/);
});
