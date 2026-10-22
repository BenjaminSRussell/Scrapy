// Run: node --test Scraping_project/dashboard/tests/
// #426: the Job alerts card has a test button, and the browser sends it through
// serve.py (the webhook URL and CORS stay on the server).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

test('job alerts card has a test button and a live status line', () => {
    assert.match(html, /id="job-alerts-card"/);
    assert.match(html, /<button[^>]*id="alert-test-send"[^>]*>Send test notification<\/button>/);
    assert.match(html, /id="alert-test-status"[^>]*role="status"[^>]*aria-live="polite"/);
});

test('the button POSTs JSON to the server route, not the webhook', () => {
    assert.match(app, /const ALERT_TEST_URL = '\/api\/alerts\/test';/);
    assert.match(app, /fetch\(ALERT_TEST_URL, \{\s*method: 'POST',\s*headers: \{ 'Content-Type': 'application\/json' \}/);
    assert.doesNotMatch(app, /ALERT_WEBHOOK|hooks\.slack|hooks\.example/);
});

test('setupAlertTest is wired at startup', () => {
    assert.match(app, /function setupAlertTest\(\) \{/);
    assert.match(app, /setupJobStarted\(\);\s*setupAlertTest\(\);/);
});

test('the button is disabled while a test is in flight', () => {
    assert.match(app, /btn\.disabled = true;/);
    assert.match(app, /finally \{\s*btn\.disabled = false;/);
});
