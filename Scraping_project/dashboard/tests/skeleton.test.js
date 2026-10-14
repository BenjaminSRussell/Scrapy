// Run: node --test Scraping_project/dashboard/tests/skeleton.test.js
// Shared loading skeleton for cards and charts (#1025).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));

function mediaBlock(query) {
    const start = css.indexOf(`@media (${query})`);
    if (start < 0) return null;
    let depth = 0;
    for (let i = css.indexOf('{', start); i < css.length; i++) {
        if (css[i] === '{') depth++;
        else if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1);
    }
    return null;
}

test('.skeleton is one pulse block that cards and charts both size (#1025)', () => {
    assert.match(css, /\.skeleton\s*\{[^}]*display:\s*block/);
    assert.match(css, /\.skeleton\s*\{[^}]*background:\s*var\(--gray-200\)/);
    assert.match(css, /\.skeleton\s*\{[^}]*animation:\s*pulse\b/);
    assert.match(css, /\.card \.skeleton/);
    assert.match(css, /\.chart-container \.skeleton\s*\{[^}]*position:\s*absolute/);
    assert.match(css, /\.chart-container\.is-unavailable \.skeleton\s*\{[^}]*display:\s*none/);
    assert.match(app, /el\.className = 'skeleton'/);
    assert.match(app, /function skeletonEl\(\)/);
    assert.match(app, /querySelectorAll\('\.chart-container \.skeleton'\)/);
});

test('reduced motion leaves the skeleton a static gray (#1025)', () => {
    const block = mediaBlock('prefers-reduced-motion: reduce');
    assert.ok(block, 'reduced-motion media query present');
    assert.match(block, /\.skeleton\s*\{[^}]*animation:\s*none/);
    assert.match(block, /\.skeleton\s*\{[^}]*background-color:\s*var\(--gray-200\)/);
    assert.match(block, /\.skeleton\s*\{[^}]*opacity:\s*1/);
    assert.doesNotMatch(block, /animation:\s*pulse/);
});
