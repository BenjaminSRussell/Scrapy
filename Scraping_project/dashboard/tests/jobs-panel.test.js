// Run: node --test Scraping_project/dashboard/tests/
// Jobs tab is a shell until the crawl job API exists (#996).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const features = fs.readFileSync(path.join(root, 'features.js'), 'utf8');

function jobsPanel() {
    const start = html.indexOf('id="tab-jobs"');
    assert.ok(start > 0, 'jobs panel present');
    const open = html.lastIndexOf('<div', start);
    const end = html.indexOf('id="refresh-status"', start);
    assert.ok(end > open);
    return html.slice(open, end);
}

test('Jobs tab is visible in the tablist and wired as a panel', () => {
    assert.match(html, /data-tab="jobs"[^>]*id="tabbtn-jobs"[^>]*aria-controls="tab-jobs"/);
    assert.match(html, /id="tab-jobs" role="tabpanel" aria-labelledby="tabbtn-jobs"/);
    assert.match(features, /jobs:\s*true/);
});

test('empty state names the job API and Start stays disabled', () => {
    const panel = jobsPanel();
    assert.match(panel, /Job API not connected/);
    assert.match(panel, /job API is connected/);
    assert.match(panel, /<button[^>]*id="jobs-start"[^>]*\bdisabled\b[^>]*>Start<\/button>/);
    assert.doesNotMatch(panel, /<li\b|<tr\b|Running|Queued|Finished/);
    assert.doesNotMatch(app, /jobs-start|jobs-list|jobs-empty/);
});

test('jobs flag still hides the tab when off (#1047)', () => {
    assert.match(app, /\[data-tab="jobs"\], #tab-jobs/);
    assert.match(app, /el\.hidden = !f\.jobs/);
    assert.match(app, /if \(!f\.jobs\) el\.style\.display = 'none'/);
});
