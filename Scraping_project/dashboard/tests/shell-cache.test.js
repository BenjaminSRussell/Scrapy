// Run: node --test Scraping_project/dashboard/tests/shell-cache.test.js
// #1005: optional service worker caches the shell only. Metrics stay network-only
// and an offline shell is always marked stale.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { offlineShellStale, staleState } = require('../format-utils.js');
const sw = require('../sw.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const features = fs.readFileSync(path.join(__dirname, '..', 'features.js'), 'utf8');
const ORIGIN = 'http://127.0.0.1:8080';

function htmlResponse(ctype, ok) {
    return {
        ok: ok !== false,
        status: ok === false ? 500 : 200,
        type: 'basic',
        headers: { get: (name) => (name.toLowerCase() === 'content-type' ? ctype : null) },
        clone() { return this; },
        body: ctype,
    };
}

test('shell decisions cache html/css/js and never metrics', () => {
    const shell = [
        ORIGIN + '/',
        ORIGIN + '/index.html',
        ORIGIN + '/app.js',
        ORIGIN + '/format-utils.js',
        ORIGIN + '/features.js',
        ORIGIN + '/version.js',
        ORIGIN + '/styles.css',
    ];
    for (const url of shell) {
        assert.strictEqual(sw.shellCacheDecision(url, ORIGIN, 'GET'), 'network-first-shell', url);
    }
    const metrics = [
        ORIGIN + '/api/metrics',
        ORIGIN + '/api/metrics?fresh=1',
        ORIGIN + '/api/queues',
        ORIGIN + '/api/health',
        ORIGIN + '/metrics',
        ORIGIN + '/exporter/metrics',
    ];
    for (const url of metrics) {
        assert.strictEqual(sw.shellCacheDecision(url, ORIGIN, 'GET'), 'network-only', url);
    }
    assert.strictEqual(sw.shellCacheDecision('https://cdn.jsdelivr.net/npm/chart.js', ORIGIN, 'GET'), 'passthrough');
    assert.strictEqual(sw.shellCacheDecision(ORIGIN + '/app.js', ORIGIN, 'POST'), 'passthrough');
    assert.strictEqual(sw.shellCacheDecision(ORIGIN + '/sw.js', ORIGIN, 'GET'), 'passthrough');
    assert.strictEqual(sw.shellCacheDecision(ORIGIN + '/icon.png', ORIGIN, 'GET'), 'passthrough');
});

test('precache list is shell assets only', () => {
    assert.ok(sw.SHELL_ASSETS.includes('/index.html'));
    assert.ok(sw.SHELL_ASSETS.includes('/app.js'));
    for (const pathName of sw.SHELL_ASSETS) {
        assert.doesNotMatch(pathName, /metrics|\/api\//);
        assert.strictEqual(sw.shellCacheDecision(ORIGIN + pathName, ORIGIN, 'GET'), 'network-first-shell');
    }
});

test('prometheus and json bodies are not stored', () => {
    assert.strictEqual(sw.shouldStoreShellResponse(htmlResponse('text/html; charset=utf-8')), true);
    assert.strictEqual(sw.shouldStoreShellResponse(htmlResponse('application/javascript; charset=utf-8')), true);
    assert.strictEqual(sw.shouldStoreShellResponse(htmlResponse('text/css')), true);
    assert.strictEqual(sw.shouldStoreShellResponse(htmlResponse('text/plain; version=0.0.4; charset=utf-8')), false);
    assert.strictEqual(sw.shouldStoreShellResponse(htmlResponse('application/json; charset=utf-8')), false);
    assert.strictEqual(sw.shouldStoreShellResponse(htmlResponse('text/html', false)), false);
    assert.strictEqual(sw.shouldStoreShellResponse({ ok: true, type: 'opaque', headers: { get: () => 'text/html' } }), false);
});

test('metrics fetch is network-only and does not open the cache', async () => {
    let opened = false;
    let init;
    const fetchImpl = async (req, options) => {
        init = options;
        assert.strictEqual(req.url, ORIGIN + '/api/metrics');
        return htmlResponse('text/plain; version=0.0.4');
    };
    const res = await sw.handleShellFetch(
        { url: ORIGIN + '/api/metrics', method: 'GET' },
        ORIGIN,
        { fetch: fetchImpl, caches: { open: async () => { opened = true; } } }
    );
    assert.strictEqual(res.ok, true);
    assert.strictEqual(init.cache, 'no-store');
    assert.strictEqual(opened, false);
});

test('shell network hit is stored; text/plain shell URL is not', async () => {
    const store = new Map();
    const cache = {
        async put(key, value) { store.set(key.url || key, value); },
        async match(key) { return store.get(key.url || key) || null; },
    };
    let calls = 0;
    const fetchImpl = async () => {
        calls += 1;
        return calls === 1 ? htmlResponse('application/javascript') : htmlResponse('text/plain; version=0.0.4');
    };
    const deps = { fetch: fetchImpl, caches: { open: async () => cache } };
    const js = await sw.handleShellFetch({ url: ORIGIN + '/app.js', method: 'GET' }, ORIGIN, deps);
    assert.strictEqual(js.ok, true);
    assert.ok(store.has(ORIGIN + '/app.js'));
    await sw.handleShellFetch({ url: ORIGIN + '/odd.js', method: 'GET' }, ORIGIN, deps);
    assert.strictEqual(store.has(ORIGIN + '/odd.js'), false);
});

test('offline navigation falls back to the cached shell and does not invent metrics', async () => {
    const store = new Map();
    store.set('/index.html', { ok: true, body: 'cached-shell' });
    const cache = {
        async put() { throw new Error('offline path must not store'); },
        async match(key) {
            const id = typeof key === 'string' ? key : key.url;
            return store.get(id) || store.get(new URL(id, ORIGIN).pathname) || null;
        },
    };
    const res = await sw.handleShellFetch(
        { url: ORIGIN + '/', method: 'GET', mode: 'navigate' },
        ORIGIN,
        {
            fetch: async () => { throw new Error('offline'); },
            caches: { open: async () => cache },
        }
    );
    assert.strictEqual(res.body, 'cached-shell');
    await assert.rejects(
        () => sw.handleShellFetch(
            { url: ORIGIN + '/api/metrics', method: 'GET' },
            ORIGIN,
            { fetch: async () => { throw new Error('offline'); }, caches: { open: async () => cache } }
        ),
        /offline/
    );
});

test('precache stores html/js and skips a metrics-shaped body', async () => {
    const stored = [];
    const fetched = [];
    const cache = { async put(key) { stored.push(key); } };
    const result = await sw.precacheShell({
        origin: ORIGIN,
        caches: { open: async () => cache },
        fetch: async (url) => {
            fetched.push(url);
            if (String(url).endsWith('/version.js')) return htmlResponse('text/plain; version=0.0.4');
            if (String(url).endsWith('.js')) return htmlResponse('application/javascript');
            return htmlResponse('text/html; charset=utf-8');
        },
    });
    assert.ok(fetched.every((url) => !String(url).includes('/api/') && !String(url).includes('metrics')));
    assert.ok(stored.includes('/index.html'));
    assert.ok(stored.includes('/app.js'));
    assert.strictEqual(stored.includes('/version.js'), false);
    assert.ok(result.includes('/') && !result.includes('/version.js'));
});

test('worker does not intercept metrics or cross-origin chart.js', async () => {
    const listeners = {};
    const fetches = [];
    const worker = {
        location: { origin: ORIGIN },
        addEventListener(name, fn) { listeners[name] = fn; },
        fetch: async (input, init) => {
            fetches.push({ input, init });
            return htmlResponse('text/plain; version=0.0.4');
        },
        caches: { open: async () => { throw new Error('cache opened'); } },
        skipWaiting: async () => {},
        clients: { claim: async () => {} },
    };
    sw.installShellWorker(worker);
    let metricsPromise;
    listeners.fetch({
        request: { url: ORIGIN + '/api/metrics', method: 'GET' },
        respondWith(p) { metricsPromise = p; },
    });
    await metricsPromise;
    assert.strictEqual(fetches[0].init.cache, 'no-store');
    let intercepted = false;
    listeners.fetch({
        request: { url: 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js', method: 'GET' },
        respondWith() { intercepted = true; },
    });
    assert.strictEqual(intercepted, false);
});

test('activate drops older shell caches only', async () => {
    const listeners = {};
    const deleted = [];
    const worker = {
        location: { origin: ORIGIN },
        addEventListener(name, fn) { listeners[name] = fn; },
        fetch: async () => htmlResponse('text/html'),
        caches: {
            open: async () => ({ put: async () => {}, match: async () => null }),
            keys: async () => ['cc-shell-v0', sw.SHELL_CACHE, 'unrelated'],
            delete: async (key) => { deleted.push(key); },
        },
        skipWaiting: async () => {},
        clients: { claim: async () => {} },
    };
    sw.installShellWorker(worker);
    let pending;
    listeners.activate({ waitUntil(p) { pending = p; } });
    await pending;
    assert.deepStrictEqual(deleted, ['cc-shell-v0']);
});

test('offline shell is stale before any successful fetch; aged rule unchanged', () => {
    assert.strictEqual(offlineShellStale(false, false), true);
    assert.strictEqual(offlineShellStale(true, false), false);
    assert.strictEqual(offlineShellStale(true, true), true);
    assert.strictEqual(staleState(null, 10000, 5000).stale, false);
});

test('page registers the worker only when the flag is on and shows the offline badge', () => {
    assert.match(features, /sw:\s*true/);
    assert.match(app, /function registerOptionalShellCache\(\)/);
    assert.match(app, /if \(!flags\.sw\) return;/);
    assert.match(app, /navigator\.serviceWorker\.register\('\/sw\.js', \{ scope: '\/' \}\)/);
    assert.match(app, /offlineShellStale\(onLine, aged\)/);
    assert.match(html, /id="offline-shell-banner"[^>]*hidden/);
    assert.match(html, /id="stale-badge"[^>]*hidden/);
    assert.match(html, /id="topbar-stale"[^>]*hidden/);
    assert.match(html, /Metrics are stale and are not served from cache/);
});
