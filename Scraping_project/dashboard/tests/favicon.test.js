// Control Center tab icon (#944). Distinct from Grafana and Prometheus.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const head = html.slice(html.indexOf('<head'), html.indexOf('</head>'));
const svg = fs.readFileSync(path.join(root, 'favicon.svg'), 'utf8');
const serve = fs.readFileSync(path.join(root, 'serve.py'), 'utf8');

const ICON_LINK = /<link rel="icon" href="favicon\.svg" type="image\/svg\+xml" sizes="any">/;

test('index.html points the tab at favicon.svg (#944)', () => {
    assert.match(head, ICON_LINK);
    assert.doesNotMatch(head, /rel="icon"[^>]*https?:/);
});

test('styleguide uses the same tab icon (#944)', () => {
    const guide = fs.readFileSync(path.join(root, 'styleguide.html'), 'utf8');
    const ghead = guide.slice(0, guide.indexOf('</head>'));
    assert.match(ghead, ICON_LINK);
});

test('favicon.svg is the title-mark spider on the Control Center tile (#944)', () => {
    assert.match(svg, /<svg\b[^>]*xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
    assert.match(svg, /viewBox="0 0 32 32"/);
    assert.match(svg, /#2563eb/);
    assert.match(svg, /#7c3aed/);
    assert.match(svg, /<circle cx="12" cy="8\.5" r="2"/);
    assert.match(svg, /M10 9\.5 5 6/);
    assert.ok(!/<script/i.test(svg), 'favicon must not embed script');
    assert.ok(svg.length < 2000);
});

test('serve.py sends SVG as image/svg+xml under nosniff (#944)', () => {
    assert.match(serve, /suffix\.lower\(\) == "\.svg"/);
    assert.match(serve, /return "image\/svg\+xml"/);
});
