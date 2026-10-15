// Run: node --test Scraping_project/dashboard/tests/
// #989: URLs and Summaries stay on screen when grid-4 wraps on a short window.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function mediaBlock(query) {
    const needle = `@media ${query}`;
    const start = html.indexOf(needle);
    assert.ok(start >= 0, `missing ${needle}`);
    const next = html.indexOf('@media', start + needle.length);
    return html.slice(start, next === -1 ? undefined : next);
}

test('overview KPI strip is the first content in the overview tab (#989)', () => {
    const overview = html.slice(html.indexOf('id="tab-overview"'), html.indexOf('id="tab-pipeline"'));
    const strip = overview.indexOf('class="overview-kpi"');
    const grid = overview.indexOf('class="grid grid-4"');
    assert.ok(strip >= 0 && grid > strip, 'KPI strip must precede the stage grid');
    assert.match(overview, /id="overview-kpi-urls"/);
    assert.match(overview, /id="overview-kpi-summaries"/);
    assert.match(overview, /aria-label="Priority KPIs"/);
    assert.match(overview, />URLs</);
    assert.match(overview, />Summaries</);
});

test('KPI strip reads the same counters as the top bar (#989)', () => {
    assert.match(app, /setMetricText\('overview-kpi-urls', formatNumber\(s1Discovered\)\)/);
    assert.match(app, /setMetricText\('overview-kpi-summaries', formatNumber\(s3Summaries\)\)/);
});

test('medium widths keep four overview columns, then prioritize Stage 3 (#989)', () => {
    const wide = mediaBlock('(min-width: 1000px)');
    assert.match(wide, /#tab-overview > \.grid\.grid-4\s*\{[^}]*repeat\(\s*4\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\s*\)/);
    const wrap = mediaBlock('(min-width: 640px) and (max-width: 999px)');
    assert.match(wrap, /\.card:nth-child\(3\)\s*\{\s*order:\s*2\s*;\s*\}/);
    assert.match(wrap, /\.card:nth-child\(2\)\s*\{\s*order:\s*3\s*;\s*\}/);
    // Phones stay on the #954 auto-fit rule (one column, source order).
    const base = html.match(/\.grid-4\s*\{([^}]*)\}/);
    assert.ok(base);
    assert.match(base[1], /minmax\(\s*min\(\s*100%\s*,\s*250px\s*\)\s*,\s*1fr\s*\)/);
});
