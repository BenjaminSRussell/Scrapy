// Run: node --test Scraping_project/dashboard/tests/
// #1004: navigator.onLine and online/offline events show a banner that is not
// the metrics exporter error (HTTP 5xx, CORS, timeout).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const appSrc = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const OFFLINE_COPY = 'You are offline. Check Wi-Fi or your network connection. This is not a metrics exporter error.';

test('markup: offline banner is a different alert from the metrics banner', () => {
    assert.match(html, /id="offline-banner"[^>]*role="alert"[^>]*hidden/);
    assert.match(html, /id="fetch-error-banner"[^>]*role="alert"[^>]*hidden/);
    assert.ok(html.includes(OFFLINE_COPY));
    assert.ok(appSrc.includes(OFFLINE_COPY));
    assert.ok(!OFFLINE_COPY.includes('Could not load metrics'));
    assert.match(html, /id="fetch-error-text">Could not load metrics\./);
    assert.match(html, /\.offline-banner\s*\{[^}]*background:\s*#dbeafe/);
    assert.match(html, /\.fetch-error-banner\s*\{[^}]*background:\s*#fee2e2/);
    assert.match(html, /\.offline-banner\[hidden\]\s*\{\s*display:\s*none/);
});

test('app.js listens for online/offline and reads navigator.onLine', () => {
    assert.match(appSrc, /navigator\.onLine/);
    assert.match(appSrc, /addEventListener\('offline', onBrowserOffline\)/);
    assert.match(appSrc, /addEventListener\('online', onBrowserOnline\)/);
    assert.match(appSrc, /setupConnectivityBanner\(\);/);
    assert.match(appSrc, /showFetchError\(failure\.message\)/);
    assert.match(appSrc, /hideFetchError\(\);/);
});

function element(id) {
    return {
        id,
        hidden: true,
        textContent: '',
        className: '',
        dataset: {},
        style: {},
        attrs: {},
        offsetWidth: 1,
        classList: {
            add() {}, remove() {}, toggle() {}, contains() { return false; },
        },
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        querySelector() { return null; },
        appendChild(c) { return c; },
        append() {},
        remove() {},
        replaceChildren() {},
        addEventListener() {},
    };
}

function fakeTimers() {
    let id = 0;
    const pending = new Map();
    return {
        setTimeout(fn, ms) { const i = ++id; pending.set(i, { fn, ms }); return i; },
        clearTimeout(i) { pending.delete(i); },
        size: () => pending.size,
    };
}

function loadApp(onLine) {
    const els = {
        'offline-banner': element('offline-banner'),
        'offline-banner-text': element('offline-banner-text'),
        'fetch-error-banner': element('fetch-error-banner'),
        'fetch-error-text': element('fetch-error-text'),
        main: element('main'),
        'last-update': element('last-update'),
        'last-refresh-time': element('last-refresh-time'),
        uptime: element('uptime'),
    };
    const listeners = { online: [], offline: [] };
    const navigator = { onLine: onLine !== false };
    const net = {
        fetchImpl: async () => ({ ok: false, status: 500, statusText: 'err' }),
    };
    const document = {
        readyState: 'loading',
        title: 'Pipeline Control Center',
        addEventListener() {},
        body: element('body'),
        getElementById(id) { return els[id] || null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement: (tag) => element(tag),
        createTextNode: () => element('text'),
        createDocumentFragment: () => element('frag'),
    };
    const timers = fakeTimers();
    const windowStub = {
        location: { protocol: 'file:', search: '' },
        addEventListener(type, fn) {
            if (listeners[type]) listeners[type].push(fn);
        },
        removeEventListener() {},
    };
    const ctx = {
        ...require('../format-utils.js'),
        document,
        navigator,
        window: windowStub,
        console: { log() {}, warn() {}, error() {} },
        setTimeout,
        clearTimeout,
        AbortController,
        URLSearchParams,
        schedSetTimeout: timers.setTimeout,
        schedClearTimeout: timers.clearTimeout,
        fetch: (u, init) => net.fetchImpl(u, init),
    };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    vm.runInContext(`
        refreshScheduler = createRefreshScheduler({
            interval: 60000,
            fetch: fetchAndAnnounce,
            setTimeout: schedSetTimeout,
            clearTimeout: schedClearTimeout,
        });
        setupConnectivityBanner();
    `, ctx);
    return { ctx, els, listeners, navigator, net, timers };
}

function fire(listeners, type) {
    return Promise.all(listeners[type].map((fn) => fn()));
}

test('loading already offline shows the network banner before any fetch', () => {
    const { els } = loadApp(false);
    assert.strictEqual(els['offline-banner'].hidden, false);
    assert.strictEqual(els['offline-banner-text'].textContent, OFFLINE_COPY);
    assert.strictEqual(els['fetch-error-banner'].hidden, true);
});

test('offline event replaces a metrics error with the network banner', async () => {
    const { ctx, els, listeners, navigator, net } = loadApp(true);
    net.fetchImpl = async () => ({ ok: false, status: 502, statusText: 'Bad Gateway' });
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(els['fetch-error-banner'].hidden, false);
    assert.match(els['fetch-error-text'].textContent, /Could not load metrics:/);
    assert.match(els['fetch-error-text'].textContent, /502/);
    assert.strictEqual(els['offline-banner'].hidden, true);

    navigator.onLine = false;
    fire(listeners, 'offline');
    assert.strictEqual(els['offline-banner'].hidden, false);
    assert.strictEqual(els['offline-banner-text'].textContent, OFFLINE_COPY);
    assert.strictEqual(els['fetch-error-banner'].hidden, true);
    assert.notStrictEqual(els['offline-banner-text'].textContent, els['fetch-error-text'].textContent);
});

test('HTTP 502 while online is the metrics banner, not the offline banner', async () => {
    const { ctx, els, net } = loadApp(true);
    net.fetchImpl = async () => ({ ok: false, status: 502, statusText: 'Bad Gateway' });
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(els['offline-banner'].hidden, true);
    assert.strictEqual(els['fetch-error-banner'].hidden, false);
    assert.match(els['fetch-error-text'].textContent, /Could not load metrics: Failed to fetch metrics: HTTP 502/);
});

test('CORS-style Failed to fetch while online stays a metrics error', async () => {
    const { ctx, els, net } = loadApp(true);
    net.fetchImpl = async () => { throw new TypeError('Failed to fetch'); };
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(els['offline-banner'].hidden, true);
    assert.strictEqual(els['fetch-error-banner'].hidden, false);
    assert.match(els['fetch-error-text'].textContent, /Could not load metrics:/);
    assert.match(els['fetch-error-text'].textContent, /Failed to fetch/);
    assert.ok(!els['fetch-error-text'].textContent.includes(OFFLINE_COPY));
});

test('a fetch that fails while offline does not use the metrics banner', async () => {
    const { ctx, els, navigator, net } = loadApp(true);
    navigator.onLine = false;
    net.fetchImpl = async () => { throw new TypeError('Failed to fetch'); };
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(els['offline-banner'].hidden, false);
    assert.strictEqual(els['offline-banner-text'].textContent, OFFLINE_COPY);
    assert.strictEqual(els['fetch-error-banner'].hidden, true);
});

test('coming online restores when the exporter succeeds', async () => {
    const { ctx, els, listeners, navigator, net } = loadApp(false);
    assert.strictEqual(els['offline-banner'].hidden, false);
    navigator.onLine = true;
    net.fetchImpl = async () => ({ ok: true, status: 200, text: async () => 'pipeline_running 1\n' });
    await fire(listeners, 'online');
    assert.strictEqual(els['offline-banner'].hidden, true);
    assert.strictEqual(els['fetch-error-banner'].hidden, true);
    assert.strictEqual(vm.runInContext('hasEverSucceeded', ctx), true);
});

test('coming online shows the metrics error when the exporter still fails', async () => {
    const { ctx, els, listeners, navigator, net } = loadApp(false);
    navigator.onLine = true;
    net.fetchImpl = async () => ({ ok: false, status: 503, statusText: 'Unavailable' });
    await fire(listeners, 'online');
    assert.strictEqual(els['offline-banner'].hidden, true);
    assert.strictEqual(els['fetch-error-banner'].hidden, false);
    assert.match(els['fetch-error-text'].textContent, /Could not load metrics:/);
    assert.match(els['fetch-error-text'].textContent, /503/);
    assert.ok(!els['fetch-error-text'].textContent.includes('Wi-Fi'));
    assert.strictEqual(vm.runInContext('hasEverSucceeded', ctx), false);
});
