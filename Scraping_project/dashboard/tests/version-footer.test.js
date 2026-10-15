// Run: node --test Scraping_project/dashboard/tests/
// #907: footer build label (dev = unreleased) and the UI/API build-mismatch check.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { buildLabel, versionStatus } = require('../format-utils.js');

const UNRELEASED = 'dev · unreleased';

test('a dev build (no CC_VERSION, no git checkout) is labelled unreleased', () => {
    for (const v of ['dev', undefined, null, '', '  ']) {
        assert.strictEqual(buildLabel(v), UNRELEASED);
    }
});

test('a sha or release build keeps exactly one v prefix', () => {
    assert.strictEqual(buildLabel('1c8ec90'), 'v1c8ec90');
    assert.strictEqual(buildLabel('v1.2.0'), 'v1.2.0');
});

test('an API that reports no version is never compared', () => {
    for (const api of [undefined, null, '', 42, {}]) {
        const s = versionStatus('1c8ec90', api);
        assert.strictEqual(s.mismatch, false);
        assert.strictEqual(s.label, 'v1c8ec90');
    }
});

test('the same build on UI and API does not warn', () => {
    const s = versionStatus('1c8ec90', '1c8ec90');
    assert.strictEqual(s.mismatch, false);
    assert.strictEqual(s.label, 'v1c8ec90');
});

test('a different API build warns and names both builds', () => {
    const s = versionStatus('1c8ec90', 'ffeedd0');
    assert.strictEqual(s.mismatch, true);
    assert.match(s.label, /^⚠ build mismatch/);
    assert.ok(s.label.includes('UI v1c8ec90') && s.label.includes('API vffeedd0'));
    assert.match(s.title, /differs from API build vffeedd0/);
});

test('a dev page against a released API warns; two dev builds do not', () => {
    assert.strictEqual(versionStatus(undefined, 'abc1234').mismatch, true);
    const same = versionStatus('dev', 'dev');
    assert.strictEqual(same.mismatch, false);
    assert.strictEqual(same.label, UNRELEASED);
});

test('the footer hosts the build label and every refresh checks the API build', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    assert.match(html, /class="refresh-bar">[\s\S]*id="cc-version"/);
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(app, /Promise\.all\(\[fetchMetrics\(\), fetchQueueDepths\(\), fetchApiVersion\(\)\]\)/);
});
