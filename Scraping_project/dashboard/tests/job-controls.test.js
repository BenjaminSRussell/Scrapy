// Run: node --test Scraping_project/dashboard/tests/
// #1000: Stop / Pause / Resume stub on Overview. Disabled until a control API.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

function overviewPanel() {
    const start = body.indexOf('id="tab-overview"');
    const end = body.indexOf('id="tab-pipeline"');
    assert.ok(start >= 0 && end > start);
    return body.slice(start, end);
}

test('overview shows a disabled Stop / Pause / Resume cluster (#1000)', () => {
    const overview = overviewPanel();
    assert.match(overview, /id="job-controls"/);
    assert.match(overview, /id="job-controls-reason"[^>]*>[^<]*job-control API/);
    assert.ok(!/visually-hidden|hidden/.test(overview.slice(
        overview.indexOf('id="job-controls-reason"'),
        overview.indexOf('job-controls-cluster')
    )), 'reason text is visible');
    const buttons = [
        ['job-control-stop', 'Stop'],
        ['job-control-pause', 'Pause'],
        ['job-control-resume', 'Resume'],
    ];
    for (const [id, label] of buttons) {
        const m = overview.match(new RegExp(`<button\\b[^>]*id="${id}"[^>]*>${label}</button>`));
        assert.ok(m, id);
        assert.match(m[0], /class="[^"]*\bbtn\b/);
        assert.match(m[0], /\bdisabled\b/);
        assert.match(m[0], /aria-describedby="job-controls-reason"/);
    }
});

test('Stop confirm dialog stays disabled and sends nothing (#1000)', () => {
    assert.match(body, /<dialog\b[^>]*id="job-stop-confirm"/);
    const confirm = body.match(/<button\b[^>]*id="job-stop-confirm-btn"[^>]*>/);
    assert.ok(confirm);
    assert.match(confirm[0], /class="[^"]*\bbtn\b/);
    assert.match(confirm[0], /\bdisabled\b/);
    assert.match(body, /id="job-stop-cancel"/);
    const start = app.indexOf('function setupJobControls');
    assert.ok(start >= 0);
    const next = app.indexOf('\nfunction ', start + 1);
    const fn = app.slice(start, next);
    assert.match(fn, /el\.disabled = true/);
    assert.match(fn, /job-stop-confirm/);
    assert.match(fn, /showModal/);
    assert.ok(!/fetch\s*\(/.test(fn));
    assert.ok(!/XMLHttpRequest/.test(fn));
    assert.ok(!/WebSocket/.test(fn));
    assert.match(app, /setupJobControls\(\)/);
});
