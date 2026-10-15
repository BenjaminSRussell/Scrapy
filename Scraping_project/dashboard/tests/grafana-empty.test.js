// Empty Grafana home (#980): operators get Control Center and the import path
// from the repo note and from the Compose home dashboard JSON.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dash = path.join(__dirname, '..');
const root = path.join(dash, '..');
const note = fs.readFileSync(path.join(dash, 'grafana-empty.md'), 'utf8');
const readme = fs.readFileSync(path.join(dash, 'README.md'), 'utf8');
const home = JSON.parse(fs.readFileSync(path.join(dash, 'grafana-home.json'), 'utf8'));
const compose = fs.readFileSync(path.join(root, 'docker-compose.yml'), 'utf8');

test('empty-state note links Control Center and the import directory', () => {
    assert.match(note, /127\.0\.0\.1:8080/);
    assert.match(note, /monitoring\/dashboards\//);
    assert.match(note, /dashboard\/import/);
    assert.match(note, /scraping_pipeline_health\.json/);
    assert.match(note, /grafana-home\.json/);
});

test('dashboard README UI section points at the note', () => {
    assert.match(readme, /Grafana empty home \(#980\)/);
    assert.match(readme, /grafana-empty\.md/);
    assert.match(readme, /127\.0\.0\.1:8080/);
});

test('home dashboard JSON is the same guidance, with one-click links', () => {
    assert.strictEqual(home.uid, 'pipeline-home');
    assert.strictEqual(home.schemaVersion, 39);
    const text = home.panels.filter((p) => p.type === 'text');
    assert.strictEqual(text.length, 1);
    assert.match(text[0].options.content, /127\.0\.0\.1:8080/);
    assert.match(text[0].options.content, /monitoring\/dashboards\//);
    const urls = home.links.map((l) => l.url);
    assert.ok(urls.includes('http://127.0.0.1:8080'));
    assert.ok(urls.includes('/dashboard/import'));
});

test('Compose mounts the home file outside the provisioning directory', () => {
    assert.match(compose, /GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=\/etc\/grafana\/home\.json/);
    assert.match(compose, /\.\/dashboard\/grafana-home\.json:\/etc\/grafana\/home\.json:ro/);
});
