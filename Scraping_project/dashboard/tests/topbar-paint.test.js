// Run: node --test Scraping_project/dashboard/tests/
// #985: the sticky topbar must not blur-shadow the page on scroll, and
// reduced motion gets a static solid fill (no shadow animation).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));

function mediaBlock(query) {
    const start = css.indexOf(`@media (${query})`);
    assert.ok(start >= 0, query);
    let depth = 0;
    for (let i = css.indexOf('{', start); i < css.length; i++) {
        if (css[i] === '{') depth++;
        else if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1);
    }
    throw new Error('unterminated ' + query);
}

function ruleBody(block, selector) {
    const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}');
    const m = block.match(re);
    return m ? m[1] : '';
}

test('sticky topbar uses a hairline and paint containment, not a blurred shadow (#985)', () => {
    const body = ruleBody(css, '.topbar');
    assert.ok(body, '.topbar rule present');
    assert.match(body, /position:\s*sticky/);
    assert.match(body, /top:\s*0/);
    assert.match(body, /contain:\s*paint/);
    assert.match(body, /border-bottom:\s*1px solid/);
    assert.match(body, /box-shadow:\s*none/);
    assert.ok(!/box-shadow:\s*[^;]*\d+px/.test(body), 'no offset drop-shadow');
    assert.match(body, /linear-gradient/);
    assert.ok(!/transition\s*:/.test(body), 'shadow must not animate');
});

test('reduced motion flattens the topbar to a solid fill with no animation (#985)', () => {
    const block = mediaBlock('prefers-reduced-motion: reduce');
    const body = ruleBody(block, '.topbar');
    assert.match(body, /background:\s*var\(--primary\)/);
    assert.ok(!/linear-gradient/.test(body), 'no gradient inside reduced motion');
    assert.ok(!/transition|animation|@keyframes/.test(body));
});
