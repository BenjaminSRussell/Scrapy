// Run: node --test Scraping_project/dashboard/tests/
// #991: color, space, radius, and type live in tokens.css. Component CSS
// references those names. Semantic names (danger, success) are the API.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const tokens = fs.readFileSync(path.join(root, 'tokens.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const guide = fs.readFileSync(path.join(root, 'styleguide.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const docs = fs.readFileSync(path.join(root, 'TOKENS.md'), 'utf8');

function styleBlock(source) {
    const start = source.indexOf('<style>');
    const end = source.indexOf('</style>');
    assert.ok(start >= 0 && end > start, 'style block');
    return source.slice(start, end).replace(/\/\*[\s\S]*?\*\//g, '');
}

function rawPaints(css) {
    const hex = css.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
    const fn = css.match(/rgba?\(/g) || [];
    const named = css.match(/(?<![\w-])(?:white|black)(?![\w-])/g) || [];
    return hex.concat(fn, named);
}

test('tokens.css defines semantic color, space, radius, and type', () => {
    for (const name of [
        '--color-primary:', '--color-accent:', '--color-success:', '--color-warning:',
        '--color-danger:', '--color-text-muted:',
        '--space-1:', '--space-4:', '--space-8:',
        '--radius-sm:', '--radius-lg:', '--radius-pill:',
        '--font-sans:', '--font-size-xs:', '--font-size-2xl:', '--font-weight-bold:',
    ]) {
        assert.ok(tokens.includes(name), name);
    }
    assert.match(tokens, /--color-danger:\s*#[0-9a-fA-F]{6}/);
    assert.match(tokens, /--color-success:\s*#[0-9a-fA-F]{6}/);
    assert.match(tokens, /--primary:\s*var\(--color-primary\)/);
    assert.match(tokens, /--success:\s*var\(--color-success\)/);
    assert.match(tokens, /--danger:\s*var\(--color-danger\)/);
    assert.match(tokens, /--muted-text:\s*var\(--color-text-muted\)/);
});

test('TOKENS.md names danger and success, not hex alone', () => {
    assert.match(docs, /--color-danger/);
    assert.match(docs, /--color-success/);
    assert.match(docs, /danger/i);
    assert.match(docs, /success/i);
    assert.match(docs, /--space-4/);
    assert.match(docs, /--font-size-md/);
});

test('dashboard and styleguide load tokens and do not paint raw colors', () => {
    assert.ok(html.indexOf('href="tokens.css"') < html.indexOf('<style>'), 'tokens.css before component CSS');
    assert.ok(guide.includes('href="tokens.css"'));
    const styleAttrs = (source) => [...source.matchAll(/\bstyle="([^"]*)"/g)].map(m => m[1]).join('\n');
    const pagePaints = rawPaints(styleBlock(html) + '\n' + styleAttrs(html));
    assert.deepStrictEqual(pagePaints, [], 'index.html style: ' + pagePaints.join(', '));
    const guidePaints = rawPaints(styleBlock(guide) + '\n' + styleAttrs(guide));
    assert.deepStrictEqual(guidePaints, [], 'styleguide: ' + guidePaints.join(', '));
    assert.ok(!/style="[^"]*#[0-9a-fA-F]{3,8}/.test(html), 'no inline hex style');
});

test('chart series resolve semantic tokens, and fallbacks match tokens.css', () => {
    assert.match(app, /function chartToken\(name, alpha\)/);
    assert.ok(!/rgb\(37,\s*99,\s*235\)/.test(app), 'primary series is not a raw rgb');
    const fallback = app.slice(app.indexOf('CHART_TOKEN_FALLBACK'), app.indexOf('function chartToken'));
    for (const name of ['--color-primary', '--color-accent', '--color-success', '--color-warning']) {
        const hex = tokens.match(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ':\\s*(#[0-9a-fA-F]{6})'));
        assert.ok(hex, name);
        assert.ok(fallback.includes("'" + name + "': '" + hex[1] + "'"), name + ' fallback ' + hex[1]);
    }
});
