// Run: node --test Scraping_project/dashboard/tests/
// Static guards for responsive layout regressions in index.html (#954, #957, #981).
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

// Preferred size of `clamp(min, a + b*vw, max)` at a viewport width, in px.
// Lengths are rem or a bare 0. Root font is 16px, matching the dashboard.
function clampPx(clampValue, viewportPx, rootPx = 16) {
    const m = clampValue.match(/clamp\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)/);
    assert.ok(m, `clamp() value, got: ${clampValue}`);
    const length = (token) => {
        const t = token.trim();
        if (t.endsWith('rem')) return parseFloat(t) * rootPx;
        if (t.endsWith('px')) return parseFloat(t);
        if (t === '0') return 0;
        throw new Error(`unsupported length ${t}`);
    };
    const preferred = m[2].split('+').reduce((sum, part) => {
        const t = part.trim();
        const vw = t.match(/^([\d.]+)vw$/);
        if (vw) return sum + (parseFloat(vw[1]) / 100) * viewportPx;
        return sum + length(t);
    }, 0);
    return Math.min(length(m[3]), Math.max(length(m[1]), preferred));
}

test('title and metric values use a fluid clamp scale (#981)', () => {
    const root = rule(':root');
    assert.match(root, /--type-title:\s*clamp\(\s*1\.125rem\s*,/);
    assert.match(root, /--type-metric:\s*clamp\(\s*1rem\s*,/);
    assert.match(root, /--type-stat:\s*clamp\(\s*1rem\s*,/);
    assert.match(root, /--type-stage-metric:\s*clamp\(\s*0\.85rem\s*,/);

    const title = rule('.topbar-title h1');
    assert.match(title, /font-size:\s*var\(--type-title\)/);
    assert.match(title, /white-space:\s*nowrap/);
    assert.match(rule('.metric-value'), /font-size:\s*var\(--type-metric\)/);
    assert.match(rule('.topbar-stat-value'), /font-size:\s*var\(--type-stat\)/);
    assert.match(rule('.stage-metric-value'), /font-size:\s*var\(--type-stage-metric\)/);
    assert.match(rule('.health-value'), /font-size:\s*var\(--type-metric\)/);

    const varValue = (name) => {
        const m = root.match(new RegExp(`${name}:\\s*(clamp\\([^)]*\\))`));
        assert.ok(m, name);
        return m[1];
    };
    // 375px content box is 375 - 4rem of topbar padding. 1.21rem title
    // ("Control Center" + badge) fits; the old fixed 1.75rem did not.
    const titleAt375 = clampPx(varValue('--type-title'), 375);
    assert.ok(titleAt375 <= 1.25 * 16, `title at 375px should be <= 1.25rem, got ${titleAt375}px`);
    assert.ok(titleAt375 >= 1.125 * 16, `title floor is 1.125rem, got ${titleAt375}px`);
    assert.strictEqual(clampPx(varValue('--type-title'), 1280), 1.75 * 16);

    const metricAt375 = clampPx(varValue('--type-metric'), 375);
    assert.ok(metricAt375 >= 16, `metric values stay >= 1rem, got ${metricAt375}px`);
    assert.ok(metricAt375 <= 20, `metric values shrink on a phone, got ${metricAt375}px`);
    assert.strictEqual(clampPx(varValue('--type-metric'), 1280), 1.25 * 16);

    const statAt375 = clampPx(varValue('--type-stat'), 375);
    assert.ok(statAt375 >= 16, `topbar stats stay >= 1rem, got ${statAt375}px`);
    assert.strictEqual(clampPx(varValue('--type-stat'), 1600), 1.5 * 16);

    const stageAt375 = clampPx(varValue('--type-stage-metric'), 375);
    assert.ok(stageAt375 >= 0.85 * 16, `stage metrics stay >= 0.85rem, got ${stageAt375}px`);

    // #155 compact row must not pin stats back to a fixed size.
    const start = html.indexOf('@media (max-width: 1024px)');
    const end = html.indexOf('@media', start + 1);
    const narrow = rule('.topbar-stat-value', html.slice(start, end));
    assert.ok(narrow, 'topbar-stat-value still tuned inside 1024px');
    assert.ok(!/font-size:\s*1rem/.test(narrow), 'narrow stats stay on the fluid scale');
});
