// Run: node --test Scraping_project/dashboard/tests/
// (#975) A stray `</ul>` left #tab-activity nested inside #tab-system, so the
// Activity tab always rendered blank (its parent panel is hidden whenever
// Activity is selected). Also keeps the refresh bar sticky while scrolling.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const body = html.slice(html.indexOf('<body'));

// <div> nesting depth at each tab panel's opening tag.
function tabDepths() {
    const re = /<div\b[^>]*>|<\/div>/g;
    let depth = 0;
    const out = {};
    let m;
    while ((m = re.exec(body))) {
        if (m[0] === '</div>') { depth--; continue; }
        const id = m[0].match(/id="(tab-[a-z]+)"/);
        if (id && /class="tab-content/.test(m[0])) out[id[1]] = depth;
        depth++;
    }
    return { out, final: depth };
}

test('all five tab panels are siblings (same nesting depth)', () => {
    const { out, final } = tabDepths();
    assert.deepStrictEqual(Object.keys(out).sort(),
        ['tab-activity', 'tab-overview', 'tab-performance', 'tab-pipeline', 'tab-system']);
    const depths = new Set(Object.values(out));
    assert.strictEqual(depths.size, 1, `tab depths differ: ${JSON.stringify(out)}`);
    assert.strictEqual(final, 0, 'div tags balanced');
});

test('no closing list tags without a matching opener', () => {
    for (const tag of ['ul', 'ol']) {
        const opens = (body.match(new RegExp(`<${tag}\\b`, 'g')) || []).length;
        const closes = (body.match(new RegExp(`</${tag}>`, 'g')) || []).length;
        assert.strictEqual(closes, opens, `<${tag}> open=${opens} close=${closes}`);
    }
});

test('refresh bar is sticky to the viewport bottom (#975)', () => {
    const m = html.match(/\.refresh-bar\s*\{([^}]*)\}/);
    assert.ok(m, '.refresh-bar rule present');
    assert.match(m[1], /position:\s*sticky/);
    assert.match(m[1], /bottom:\s*0/);
    assert.match(m[1], /flex-wrap:\s*wrap/);
});
