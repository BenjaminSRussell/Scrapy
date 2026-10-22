// Run: node --test Scraping_project/dashboard/tests/
// #404: first-run wizard markup and client wiring. The server side is covered in
// tests/unit/test_dashboard_first_run_404.py.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

function wizardSection() {
    const start = body.indexOf('id="first-run-wizard"');
    const end = body.indexOf('<div class="tabs">');
    assert.ok(start > 0 && end > start, 'wizard sits above the tabs');
    return body.slice(start, end);
}

test('wizard markup is hidden by default and has a labelled heading', () => {
    const section = wizardSection();
    assert.match(section, /<section id="first-run-wizard"[^>]*\shidden>/);
    assert.match(section, /aria-labelledby="first-run-title"/);
    assert.match(section, /id="first-run-title"/);
});

test('wizard offers checklist, sample seed load, Grafana link and finish', () => {
    const section = wizardSection();
    for (const id of ['first-run-checks', 'first-run-recheck', 'first-run-load-seeds', 'first-run-grafana', 'first-run-finish', 'first-run-status']) {
        assert.match(section, new RegExp(`id="${id}"`), id);
    }
    const grafana = section.match(/<a\b[^>]*id="first-run-grafana"[^>]*>/);
    assert.ok(grafana, 'Grafana is a link');
    assert.match(grafana[0], /href="http:\/\/localhost:3000\/"/);
    assert.match(grafana[0], /rel="noopener noreferrer"/);
});

test('initialize() sets up the wizard after the Grafana link', () => {
    const init = app.slice(app.indexOf('function initialize()'));
    assert.match(init, /applyGrafanaLink\(\);\s*setupFirstRunWizard\(\);/);
});

test('wizard reads state from and posts actions to the server endpoints', () => {
    assert.match(app, /fetch\('\/api\/first-run'/);
    assert.match(app, /postFirstRun\('\/api\/first-run\/sample-seeds'\)/);
    assert.match(app, /postFirstRun\('\/api\/first-run\/complete'\)/);
    assert.match(app, /'Content-Type': 'application\/json'/);
});

test('wizard returns early when the server reports completed', () => {
    const fn = app.slice(app.indexOf('async function setupFirstRunWizard'), app.indexOf('function initialize()'));
    assert.match(fn, /if \(!state \|\| state\.completed\) return;/);
    assert.match(fn, /wizard\.hidden = false;/);
    assert.match(fn, /wizard\.hidden = true;/);
});

test('checklist rendering never uses innerHTML for service details', () => {
    const fn = app.slice(app.indexOf('function renderFirstRunChecks'), app.indexOf('function setFirstRunStatus'));
    assert.match(fn, /textContent/);
    assert.doesNotMatch(fn, /innerHTML/);
});
