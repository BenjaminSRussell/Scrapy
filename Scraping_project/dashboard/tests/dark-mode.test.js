// Run: node --test Scraping_project/dashboard/tests/
// #946: dark scheme follows the system, a toggle remembers light or dark,
// and chart series stay at least 3:1 on the dark card.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');
const { parseStoredColorScheme, resolveColorScheme, chartPalette } = utils;

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));
const DARK_CARD = '#1e293b';

function lum(hex) {
    const c = hex.replace('#', '').match(/../g).map(h => parseInt(h, 16) / 255)
        .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function ratio(a, b) {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
}
function varsIn(block) {
    const out = {};
    for (const m of block.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
    return out;
}
function braceBlock(source, start) {
    let depth = 0;
    for (let i = source.indexOf('{', start); i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
    }
    return '';
}

test('stored scheme is only light or dark; the feature flag forces light', () => {
    assert.strictEqual(parseStoredColorScheme('dark'), 'dark');
    assert.strictEqual(parseStoredColorScheme('light'), 'light');
    assert.strictEqual(parseStoredColorScheme(''), null);
    assert.strictEqual(parseStoredColorScheme('system'), null);
    assert.strictEqual(resolveColorScheme(null, true, true), 'dark');
    assert.strictEqual(resolveColorScheme(null, false, true), 'light');
    assert.strictEqual(resolveColorScheme('light', true, true), 'light');
    assert.strictEqual(resolveColorScheme('dark', false, true), 'dark');
    assert.strictEqual(resolveColorScheme('dark', true, false), 'light');
    assert.strictEqual(resolveColorScheme('nope', true, true), 'dark');
});

test('dark chart colours clear 3:1 on the dark card and differ from light', () => {
    const light = chartPalette('light');
    const dark = chartPalette('dark');
    assert.strictEqual(light.blue, 'rgb(37, 99, 235)');
    assert.strictEqual(light.purple, 'rgb(124, 58, 237)');
    assert.strictEqual(light.baseline, '#475569');
    for (const key of ['blue', 'purple', 'green', 'amber', 'tick', 'baseline']) {
        assert.notStrictEqual(dark[key], light[key], key);
        assert.ok(ratio(dark[key], DARK_CARD) >= 3, `${key} ${dark[key]} on ${DARK_CARD} is ${ratio(dark[key], DARK_CARD).toFixed(2)}`);
    }
});

test('dark tokens meet AA and match in both scheme selectors', () => {
    const explicit = braceBlock(css, css.indexOf('html[data-theme="dark"]'));
    const mediaStart = css.indexOf('@media (prefers-color-scheme: dark)');
    const media = braceBlock(css, mediaStart);
    assert.match(media, /html:not\(\[data-theme="light"\]\)/);
    const a = varsIn(explicit);
    const b = varsIn(media);
    assert.deepStrictEqual(a, b);
    assert.strictEqual(a['--page-bg'], '#0f172a');
    assert.strictEqual(a['--surface'], DARK_CARD);
    const text = [
        ['--gray-900', '--surface'],
        ['--gray-900', '--page-bg'],
        ['--gray-800', '--surface'],
        ['--gray-800', '--gray-100'],
        ['--gray-700', '--surface'],
        ['--gray-700', '--gray-50'],
        ['--gray-700', '--page-bg'],
        ['--muted-text', '--surface'],
        ['--muted-text', '--page-bg'],
        ['--muted-text', '--gray-50'],
        ['--accent', '--gray-50'],
        ['--accent', '--surface'],
        ['--primary-dark', '--surface'],
        ['--success', '--surface'],
        ['--warning', '--surface'],
        ['--danger', '--surface'],
        ['--gray-900', '--activity-success-bg'],
        ['--gray-900', '--activity-warning-bg'],
        ['--gray-900', '--activity-timeout-bg'],
        ['--gray-900', '--activity-danger-bg'],
        ['--gray-700', '--health-ok-bg'],
        ['--gray-700', '--health-bad-bg'],
        ['--compare-on-text', '--compare-on-bg'],
    ];
    for (const [fg, bg] of text) {
        const r = ratio(a[fg], a[bg]);
        assert.ok(r >= 4.5, `${fg} on ${bg} is ${r.toFixed(2)}`);
    }
    assert.ok(ratio(a['--line'], a['--surface']) >= 3, 'dividers');
    assert.ok(ratio('#ffffff', '#2563eb') >= 4.5, 'topbar gradient stays light-on-brand');
    assert.match(css, /--primary:\s*#2563eb/);
    assert.match(css, /--secondary:\s*#7c3aed/);
});

test('the top bar toggle is wired to localStorage and repaints charts', () => {
    assert.match(html, /id="theme-toggle"[^>]*aria-pressed="false"/);
    assert.match(html, />Dark mode</);
    assert.match(app, /STORE_THEME_KEY = 'cc\.colorScheme'/);
    assert.match(app, /storeSet\(STORE_THEME_KEY, next\)/);
    assert.match(app, /applyChartTheme\(next\)/);
    assert.match(app, /__CC_FEATURES__\.dark === false/);

    const store = new Map();
    let schemeDark = false;
    const listeners = [];
    const root = { dataset: {} };
    const btn = {
        hidden: false,
        attrs: {},
        setAttribute(k, v) { this.attrs[k] = String(v); },
        addEventListener(_type, fn) { this.onclick = fn; },
    };
    const live = { textContent: '' };
    const document = {
        readyState: 'loading',
        documentElement: root,
        addEventListener() {},
        getElementById(id) {
            if (id === 'theme-toggle') return btn;
            if (id === 'refresh-status') return live;
            return null;
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    const window = {
        localStorage: {
            getItem: (k) => (store.has(k) ? store.get(k) : null),
            setItem: (k, v) => store.set(k, String(v)),
        },
        matchMedia(q) {
            return {
                get matches() { return q.indexOf('prefers-color-scheme: dark') >= 0 ? schemeDark : false; },
                addEventListener(_type, fn) { listeners.push(fn); },
            };
        },
    };
    const urls = {
        data: { datasets: [
            { label: 'URLs Discovered', borderColor: '', backgroundColor: '', data: [1] },
            { ccBaseline: 'prev-hour', borderColor: '', backgroundColor: '', data: [0] },
        ] },
        options: { scales: { y: { ticks: {}, grid: {}, border: {} } }, plugins: { legend: { labels: {} } } },
        update() {},
    };
    const bar = {
        data: { datasets: [{ backgroundColor: [], borderColor: [], data: [0, 0, 0, 0] }] },
        options: { scales: { x: { ticks: {}, grid: {}, border: {} } }, plugins: { legend: { labels: {} } } },
        update() {},
    };
    const ctx = {
        ...utils,
        document,
        window,
        console: { log() {}, warn() {}, error() {} },
    };
    vm.createContext(ctx);
    vm.runInContext(app, ctx);
    ctx.__urls = urls;
    ctx.__bar = bar;
    vm.runInContext('charts = { urls: __urls, stageProgression: __bar };', ctx);

    schemeDark = true;
    vm.runInContext('setupTheme()', ctx);
    assert.strictEqual(btn.attrs['aria-pressed'], 'true');
    assert.strictEqual(root.dataset.theme, undefined);
    assert.strictEqual(urls.data.datasets[0].borderColor, chartPalette('dark').blue);
    assert.strictEqual(urls.data.datasets[1].borderColor, chartPalette('dark').baseline);
    assert.strictEqual(urls.options.scales.y.ticks.color, chartPalette('dark').tick);
    assert.strictEqual(bar.data.datasets[0].borderColor[0], chartPalette('dark').blue);
    assert.strictEqual(listeners.length, 1);

    btn.onclick();
    assert.strictEqual(store.get('cc.colorScheme'), 'light');
    assert.strictEqual(root.dataset.theme, 'light');
    assert.strictEqual(btn.attrs['aria-pressed'], 'false');
    assert.strictEqual(urls.data.datasets[0].borderColor, chartPalette('light').blue);
    assert.strictEqual(live.textContent, 'Light mode on');

    btn.onclick();
    assert.strictEqual(store.get('cc.colorScheme'), 'dark');
    assert.strictEqual(root.dataset.theme, 'dark');
    assert.strictEqual(btn.attrs['aria-pressed'], 'true');
    assert.strictEqual(urls.data.datasets[0].borderColor, chartPalette('dark').blue);

    schemeDark = false;
    listeners[0]();
    assert.strictEqual(root.dataset.theme, 'dark', 'a stored choice ignores the system scheme');
    assert.strictEqual(urls.data.datasets[0].borderColor, chartPalette('dark').blue);

    window.__CC_FEATURES__ = { dark: false };
    vm.runInContext('setupTheme()', ctx);
    assert.strictEqual(btn.hidden, true);
    assert.strictEqual(root.dataset.theme, 'light');
    assert.strictEqual(urls.data.datasets[0].borderColor, chartPalette('light').blue);
});
