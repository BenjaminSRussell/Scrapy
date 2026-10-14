// Run: node --test Scraping_project/dashboard/tests/
// #1044: one Overview screenshot artifact, documented. The capture itself is an
// optional Playwright job, not part of this default suite.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..', '..');
const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '..', 'overview_screenshot.py'), 'utf8');
const workflow = fs.readFileSync(path.join(root, '.github', 'workflows', 'ci-dashboard-visual.yml'), 'utf8');

test('Overview screenshot artifact is documented (#1044)', () => {
    assert.match(readme, /dashboard-overview-screenshot/);
    assert.match(readme, /overview\.png/);
    assert.match(readme, /#tab-overview/);
    assert.match(readme, /optional/i);
    assert.match(script, /ARTIFACT_NAME = "dashboard-overview-screenshot"/);
    assert.match(script, /#tab-overview/);
    assert.match(script, /overview-s1-discovered/);
    assert.match(workflow, /name: dashboard-overview-screenshot/);
    assert.match(workflow, /overview\.png/);
    assert.match(workflow, /continue-on-error:\s*true/);
    assert.match(workflow, /playwright==1\.55\.0/);
    assert.match(workflow, /workflow_dispatch/);
    assert.doesNotMatch(workflow, /redis_url|REDIS_URL/i);
});
