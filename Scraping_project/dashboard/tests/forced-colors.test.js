// Run: node --test Scraping_project/dashboard/tests/
// #982: cards, tabs and state stay distinguishable under forced-colors.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function forcedBlock() {
    const start = html.indexOf('@media (forced-colors: active)');
    assert.ok(start > 0, 'forced-colors media query present');
    let depth = 0;
    for (let i = html.indexOf('{', start); i < html.length; i++) {
        if (html[i] === '{') depth++;
        else if (html[i] === '}' && --depth === 0) return html.slice(start, i + 1);
    }
    throw new Error('unterminated block');
}

function rule(block, selector) {
    const re = new RegExp('(^|[\\s,}])' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*(,[^{]*)?\\{([^}]*)\\}');
    const m = block.match(re);
    return m ? m[3] : null;
}

test('cards get a real border (box-shadow is dropped in forced colors)', () => {
    const b = forcedBlock();
    assert.match(rule(b, '.card') || '', /border:\s*1px solid CanvasText/);
    assert.match(rule(b, '.stage-card') || '', /border:\s*2px solid CanvasText/);
});

test('active tab differs from inactive tabs by system colours, not author colours', () => {
    const b = forcedBlock();
    assert.match(rule(b, '.tab-button') || '', /border-bottom:\s*3px solid Canvas\b/);
    const active = rule(b, '.tab-button.active') || '';
    assert.match(active, /background:\s*Highlight/);
    assert.match(active, /color:\s*HighlightText/);
});

test('active stage card and unhealthy items have non-colour cues', () => {
    const b = forcedBlock();
    assert.match(rule(b, '.stage-card.active') || '', /border:\s*4px double Highlight/);
    assert.match(rule(b, '.health-item.unhealthy') || '', /dashed/);
});

test('status dot and badges stay visible', () => {
    const b = forcedBlock();
    assert.match(rule(b, '.status-dot') || '', /background:\s*CanvasText/);
    assert.match(b, /\.card-badge,\s*\n\s*\.status-indicator\s*\{[^}]*border:\s*1px solid CanvasText/);
});

test('focus outlines are never removed', () => {
    assert.ok(!/outline:\s*(none|0)\b/.test(html), 'no outline:none anywhere');
    assert.match(forcedBlock(), /outline:\s*3px solid Highlight/);
});
