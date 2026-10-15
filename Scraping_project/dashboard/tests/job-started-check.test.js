// Run: node --test Scraping_project/dashboard/tests/
// #1022: job start confirms with one CSS check. Reduced motion skips the draw.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { jobStartedPresentation } = require('../format-utils.js');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const utils = fs.readFileSync(path.join(root, 'format-utils.js'), 'utf8');

function mediaBlock(query) {
    const start = html.indexOf(`@media (${query})`);
    if (start < 0) return null;
    let depth = 0;
    for (let i = html.indexOf('{', start); i < html.length; i++) {
        if (html[i] === '{') depth++;
        else if (html[i] === '}' && --depth === 0) return html.slice(start, i + 1);
    }
    return null;
}

test('job start is a check, never confetti', () => {
    assert.doesNotMatch(html + app + utils, /canvas-confetti|confetti\s*\(/i);
    const moving = jobStartedPresentation(false);
    const still = jobStartedPresentation(true);
    assert.strictEqual(moving.animate, true);
    assert.strictEqual(moving.className, 'is-on');
    assert.strictEqual(moving.label, 'Job started');
    assert.strictEqual(moving.confetti, false);
    assert.strictEqual(still.animate, false);
    assert.strictEqual(still.className, 'is-static');
    assert.strictEqual(still.confetti, false);
});

test('Start hosts a check that draws once', () => {
    assert.match(html, /<button type="button" id="job-start"[^>]*disabled/);
    assert.match(html, /class="job-started-check"[^>]*hidden/);
    assert.match(html, /<path d="M5 12\.5 9\.5 17 19 7"><\/path>/);
    assert.match(html, /@keyframes job-start-check\s*\{[^}]*stroke-dashoffset:\s*0/);
    assert.match(html, /\.job-started-check\.is-on path\s*\{[^}]*animation:\s*job-start-check/);
    assert.match(app, /function showJobStarted\(root\)/);
    assert.match(app, /addEventListener\('cc:job-started'/);
    assert.match(app, /setupJobStarted\(\)/);
});

test('prefers-reduced-motion skips the check animation', () => {
    const block = mediaBlock('prefers-reduced-motion: reduce');
    assert.ok(block, 'reduced-motion media query present');
    assert.match(block, /\.job-started-check path\s*\{[^}]*animation:\s*none\s*!important/);
    assert.match(block, /\.job-started-check path\s*\{[^}]*stroke-dashoffset:\s*0\s*!important/);
    const keyframesAt = html.indexOf('@keyframes job-start-check');
    assert.ok(keyframesAt > 0 && keyframesAt < html.indexOf('@media (prefers-reduced-motion: reduce)'));
});
