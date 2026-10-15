// Run: node --test Scraping_project/dashboard/tests/
// #992: shared .btn variants, disabled fill, and :focus-visible ring.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const css = fs.readFileSync(path.join(root, 'buttons.css'), 'utf8');
const guide = fs.readFileSync(path.join(root, 'styleguide.html'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

const VARIANTS = ['btn-primary', 'btn-secondary', 'btn-danger', 'btn-ghost'];

function lum(hex) {
    const c = hex.replace('#', '').match(/../g).map(h => parseInt(h, 16) / 255)
        .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function ratio(a, b) {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
}
function ruleBody(selector) {
    const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const re = /([^{}]+)\{([^{}]*)\}/g;
    let m;
    while ((m = re.exec(code))) {
        const selectors = m[1].split(',').map(s => s.trim());
        if (selectors.includes(selector)) return m[2];
    }
    assert.fail(`missing rule ${selector}`);
}
function paint(selector) {
    const body = ruleBody(selector);
    const bg = (body.match(/background:\s*([^;]+)/) || [])[1] || '';
    const fg = (body.match(/(?:^|[;\s])color:\s*([^;]+)/) || [])[1] || '';
    const hex = (value) => {
        const hit = value.match(/#([0-9a-fA-F]{6})\b/);
        return hit ? `#${hit[1].toLowerCase()}` : null;
    };
    return { bg: hex(bg), fg: hex(fg), rawBg: bg.trim(), rawFg: fg.trim() };
}

test('stylesheet is shared and names the four variants', () => {
    assert.match(html, /<link rel="stylesheet" href="buttons\.css">/);
    assert.match(guide, /<link rel="stylesheet" href="buttons\.css">/);
    assert.match(css, /\.btn\s*\{/);
    for (const name of VARIANTS) {
        assert.match(css, new RegExp(`\\.${name}\\s*\\{`));
        assert.match(guide, new RegExp(`\\.${name}\\b`));
        assert.match(guide, new RegExp(`class="btn ${name}"`));
    }
});

test('disabled and focus-visible states are styled, not outline-none', () => {
    assert.match(css, /\.btn:disabled,/);
    assert.match(css, /\.btn\[aria-disabled="true"\]/);
    assert.match(css, /\.btn:disabled:hover/);
    assert.match(css, /\.btn:focus-visible,\s*\n\s*\.btn\[aria-disabled="true"\]:focus-visible\s*\{[^}]*outline:\s*3px solid/);
    assert.match(css, /@media \(forced-colors: active\)[\s\S]*outline:\s*3px solid Highlight/);
    const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
    assert.ok(!/outline:\s*(none|0)\b/.test(code), 'button CSS must not remove outlines');
    const disabled = paint('.btn:disabled');
    assert.ok(ratio(disabled.fg, disabled.bg) >= 4.5, `${disabled.fg} on ${disabled.bg}`);
    for (const name of VARIANTS) {
        assert.match(guide, new RegExp(`class="btn ${name}"[^>]*disabled`));
    }
    assert.match(guide, /aria-disabled="true"/);
});

test('variant text meets WCAG AA against its fill', () => {
    const primary = paint('.btn-primary');
    assert.ok(ratio(primary.fg, primary.bg) >= 4.5, `primary ${primary.fg} on ${primary.bg}`);
    const primaryHover = paint('.btn-primary:hover');
    assert.ok(ratio(primaryHover.fg, primaryHover.bg) >= 4.5);
    const secondary = paint('.btn-secondary');
    assert.ok(ratio(secondary.fg, secondary.bg) >= 4.5);
    const danger = paint('.btn-danger');
    assert.ok(ratio(danger.fg, danger.bg) >= 4.5);
    const dangerHover = paint('.btn-danger:hover');
    assert.ok(ratio(dangerHover.fg, dangerHover.bg) >= 4.5);
    const ghost = paint('.btn-ghost');
    assert.strictEqual(ghost.rawBg, 'transparent');
    assert.ok(ratio(ghost.fg, '#ffffff') >= 4.5, 'ghost on white');
    assert.ok(ratio(ghost.fg, '#f3f4f6') >= 4.5, 'ghost on dashboard canvas');
    const base = paint('.btn');
    assert.ok(ratio(base.fg, base.bg) >= 4.5);
});

test('existing controls use the variants instead of a bare class', () => {
    assert.match(html, /id="retry-fetch" class="btn btn-primary"/);
    assert.match(html, /id="manual-refresh" class="btn btn-primary"/);
    assert.match(html, /id="pause-refresh" class="btn btn-secondary"/);
    assert.match(html, /id="export-activity-json" class="btn btn-secondary"/);
    assert.match(html, /id="export-activity-csv" class="btn btn-secondary"/);
    assert.match(html, /id="clear-activity" class="btn btn-danger"/);
    assert.match(html, /id="reset-chart-history" class="btn btn-ghost"/);
    assert.match(app, /class="btn btn-ghost"/);
});
