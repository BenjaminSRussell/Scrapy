// Run: node --test Scraping_project/dashboard/tests/
// Static guards for responsive layout regressions in index.html (#954, #957).
// Verified in headless Chrome at 360/375/768/1280px when written; these checks
// keep the CSS from drifting back.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function rule(selector, source = html) {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = source.match(new RegExp(esc + '\\s*\\{([^}]*)\\}'));
    return m ? m[1] : null;
}

test('grid tracks never exceed the container on narrow phones (#954)', () => {
    for (const sel of ['.grid-2', '.grid-3', '.grid-4']) {
        const body = rule(sel);
        assert.ok(body, `${sel} rule present`);
        assert.match(body, /minmax\(\s*min\(\s*100%\s*,\s*\d+px\s*\)\s*,\s*1fr\s*\)/,
            `${sel} minimum must be min(100%, Npx), got: ${body.trim()}`);
    }
});

test('stage-pipeline connector hidden once cards wrap (#957)', () => {
    const start = html.indexOf('@media (max-width: 1024px)');
    assert.ok(start >= 0, '1024px breakpoint present');
    // The media block ends at the next top-level @media.
    const end = html.indexOf('@media', start + 1);
    const block = html.slice(start, end);
    const body = rule('.stage-pipeline::before', block);
    assert.ok(body, 'connector rule inside the 1024px breakpoint');
    assert.match(body, /display:\s*none/);
    // Desktop (4 columns) still draws it.
    assert.match(rule('.stage-pipeline::before'), /content:/);
});
