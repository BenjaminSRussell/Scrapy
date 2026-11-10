// Run: node --test Scraping_project/dashboard/tests/
// #970: footer with version/build, last success, and local metrics + Grafana.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const serve = fs.readFileSync(path.join(root, 'serve.py'), 'utf8');

test('footer is in the refresh bar, after every tab panel', () => {
    const footerAt = html.indexOf('id="cc-footer"');
    const barAt = html.indexOf('class="refresh-bar"');
    const drillAt = html.indexOf('id="stage-drilldown"');
    assert.ok(footerAt > barAt, 'footer is inside the always-visible refresh bar');
    assert.ok(footerAt < drillAt, 'footer stays with the refresh bar');
    for (const id of ['tab-overview', 'tab-performance', 'tab-pipeline', 'tab-system', 'tab-activity']) {
        assert.ok(html.indexOf(`id="${id}"`) < barAt, `${id} opens before the refresh bar`);
    }
    assert.match(html, /<footer class="cc-footer" id="cc-footer">/);
});

test('footer links open the local metrics exporter and Grafana', () => {
    assert.match(html, /id="cc-footer-metrics"[^>]*href="http:\/\/127\.0\.0\.1:9090\/metrics"/);
    assert.match(html, /id="cc-footer-grafana"[^>]*href="http:\/\/127\.0\.0\.1:3000\/"/);
    assert.match(html, /id="cc-footer-metrics"[^>]*rel="noopener noreferrer"/);
    assert.match(html, /id="cc-footer-grafana"[^>]*rel="noopener noreferrer"/);
});

test('version comes from serve.py CC_VERSION and last success tracks lastMetricsAt', () => {
    assert.match(serve, /os\.environ\.get\('CC_VERSION'\)/);
    assert.match(serve, /window\.__CC_VERSION__/);
    assert.match(app, /\['cc-version', 'cc-footer-version'\]/);
    assert.match(app, /window\.__CC_VERSION__ \|\| 'dev'/);
    assert.match(app, /function renderFooterSuccess\(\)/);
    assert.match(app, /getElementById\('cc-footer-last-success'\)/);
    assert.match(app, /renderFooterSuccess\(\);\s*\n\s*renderStale\(\);/);
    const success = app.indexOf('lastMetricsAt = Date.now()');
    const fail = app.indexOf('metricsWasDown = true');
    assert.ok(success > 0 && fail > success, 'last success is recorded only on a good fetch');
    assert.ok(!app.slice(fail, fail + 800).includes('lastMetricsAt = Date.now()'));
});
