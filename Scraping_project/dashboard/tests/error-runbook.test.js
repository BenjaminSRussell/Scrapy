// Run: node --test Scraping_project/dashboard/tests/
// #446: the fetch error banner links to runbooks and copies the diagnose command.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const repo = path.join(root, '..');

test('error banner stays an alert and links three runbooks', () => {
    assert.match(html, /id="fetch-error-banner"[^>]*role="alert"[^>]*hidden/);
    const links = html.match(/<a href="https:\/\/github\.com\/BenjaminSRussell\/Scrapy\/blob\/main\/[^"]+"/g) || [];
    assert.strictEqual(links.length, 3);
    assert.match(html, /Scraping_project\/README\.md#troubleshooting/);
    assert.match(html, /scripts\/README\.md#3-reset_grafana_complete\.sh/);
    assert.match(html, /docs\/guides\/RUNNING\.md#start-everything-docker-compose/);
});

test('runbook links point at files that exist in the repo', () => {
    const targets = [
        'README.md',
        'scripts/README.md',
        'docs/guides/RUNNING.md',
    ];
    for (const target of targets) {
        assert.ok(fs.existsSync(path.join(repo, target)), `${target} exists`);
    }
});

test('copy button reads the diagnose command and the script exists', () => {
    assert.match(html, /<button type="button" id="copy-diagnose"/);
    const command = html.match(/<code id="diagnose-command"[^>]*>([^<]+)<\/code>/);
    assert.ok(command, 'diagnose command rendered');
    const script = command[1].trim().replace(/^\.\//, '');
    assert.strictEqual(script, 'Scraping_project/scripts/diagnose_issues.sh');
    assert.ok(fs.existsSync(path.join(repo, '..', script)), 'diagnose script exists');
});

test('copy failure is announced, never silent', () => {
    assert.match(app, /function setupCopyDiagnose\(\)/);
    assert.match(app, /setupCopyDiagnose\(\);/);
    assert.match(app, /navigator\.clipboard\.writeText/);
    assert.match(app, /Could not copy\. Run this instead:/);
    assert.match(html, /id="copy-diagnose-status"[^>]*role="status"/);
});
