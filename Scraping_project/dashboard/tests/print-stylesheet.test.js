// Run: node --test Scraping_project/dashboard/tests/
// #974: a printed incident snapshot must not be the purple sticky chrome,
// and charts in inactive tabs must be measurable before the print snapshot.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function printBlock() {
    const start = html.indexOf('@media print');
    assert.ok(start > 0, 'print media query present');
    let depth = 0;
    for (let i = html.indexOf('{', start); i < html.length; i++) {
        if (html[i] === '{') depth++;
        else if (html[i] === '}' && --depth === 0) return html.slice(start, i + 1);
    }
    throw new Error('unterminated print block');
}

test('print drops the purple sticky chrome and uses black text (#974)', () => {
    const block = printBlock();
    const top = block.match(/\.topbar\s*\{([^}]*)\}/);
    assert.ok(top, '.topbar rule inside @media print');
    assert.match(top[1], /position:\s*static/);
    assert.match(top[1], /background:\s*#fff/);
    assert.match(top[1], /background-image:\s*none/);
    assert.match(top[1], /color:\s*#000/);
    assert.match(top[1], /box-shadow:\s*none/);
    assert.doesNotMatch(top[1], /linear-gradient|sticky/);
    assert.doesNotMatch(block, /linear-gradient/);
    assert.match(block, /\.tab-buttons-wrap[\s\S]*?display:\s*none\s*!important/);
    assert.match(block, /\.tab-content\s*\{[^}]*display:\s*block\s*!important/);
    assert.match(block, /box-shadow:\s*none\s*!important/);
});

test('print expands every section and gives charts a fixed height (#974)', () => {
    const block = printBlock();
    const sections = [
        ['#tab-overview', 'Overview'],
        ['#tab-pipeline', 'Pipeline stages'],
        ['#tab-performance', 'Performance'],
        ['#tab-system', 'System health'],
        ['#tab-activity', 'Activity log'],
    ];
    for (const [id, label] of sections) {
        const re = new RegExp(id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
            '::before\\s*\\{[^}]*content:\\s*"' + label + '"');
        assert.match(block, re, label);
    }
    const chart = block.match(/\.chart-container\s*\{([^}]*)\}/);
    assert.ok(chart, '.chart-container rule inside @media print');
    assert.match(chart[1], /height:\s*2\.25in/);
    assert.match(chart[1], /overflow:\s*visible/);
    assert.match(block, /\.chart-container canvas[\s\S]*?opacity:\s*1\s*!important/);
    assert.match(block, /\.chart-container\.is-unavailable canvas\s*\{[^}]*display:\s*none/);
    assert.match(block, /\.activity-feed\s*\{[^}]*max-height:\s*none/);
    assert.match(block, /\.activity-feed\s*\{[^}]*overflow:\s*visible/);
});

test('screen print class and beforeprint resize hidden-tab charts (#974)', () => {
    assert.match(html, /html\.cc-print \.tab-content\s*\{[^}]*display:\s*block\s*!important/);
    assert.match(html, /html\.cc-print \.chart-container\s*\{[^}]*height:\s*2\.25in/);
    assert.match(app, /function setupPrintLayout\(/);
    assert.match(app, /addEventListener\('beforeprint'/);
    assert.match(app, /addEventListener\('afterprint'/);
    assert.match(app, /classList\.add\('cc-print'\)/);
    assert.match(app, /classList\.remove\('cc-print'\)/);
    assert.match(app, /setupPrintLayout\(\)/);
});
