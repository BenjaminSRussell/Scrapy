// #657: Activity tab lists operator audit events. The panel is read-only.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('operator audit card is inside the activity tab and has no controls', () => {
    const activity = html.indexOf('id="tab-activity"');
    const cardAt = html.indexOf('id="operator-audit-card"');
    const refresh = html.indexOf('class="refresh-bar"');
    assert.ok(activity !== -1 && cardAt > activity && cardAt < refresh, 'card sits in the activity tab');
    const card = html.slice(cardAt, html.indexOf('id="operator-audit"', cardAt));
    const panel = html.slice(cardAt, html.indexOf('</table>', cardAt));
    assert.match(panel, /Read-only/);
    assert.match(panel, />Time</);
    assert.match(panel, />Actor</);
    assert.match(panel, />Action</);
    assert.match(panel, />Job</);
    assert.ok(!/<button\b/.test(panel), 'audit panel has no buttons');
    assert.ok(!/clear/i.test(card), 'audit card has no clear control');
    assert.match(html, /id="operator-audit-status"[^>]*role="status"/);
});

test('app.js renders /api/audit with textContent and refreshes it', () => {
    const fn = app.slice(app.indexOf('async function fetchOperatorAudit'), app.indexOf('async function fetchMetrics'));
    assert.ok(fn.includes('function fetchOperatorAudit'), 'fetchOperatorAudit present');
    assert.match(app, /const AUDIT_URL = '\/api\/audit\?limit=50'/);
    assert.match(fn, /fetch\(AUDIT_URL/);
    assert.match(fn, /textContent/);
    assert.ok(!/innerHTML/.test(fn));
    assert.match(app, /fetchOperatorAudit\(\)/);
});
