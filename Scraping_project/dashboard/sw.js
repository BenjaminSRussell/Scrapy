// Optional Control Center shell cache (#1005).
// HTML/CSS/JS are network-first with a cache fallback so a hard refresh still
// shows the last shell offline. Metrics and other /api responses are never
// written to the Cache API and are always revalidated with cache: 'no-store'.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
    if (typeof ServiceWorkerGlobalScope !== 'undefined' && root instanceof ServiceWorkerGlobalScope) {
        api.installShellWorker(root);
    }
})(typeof self !== 'undefined' ? self : this, function () {
    const SHELL_CACHE = 'cc-shell-v1';
    // Same-origin documents and scripts only. /sw.js is excluded so an update
    // check cannot be pinned to a cached worker. No /api and no /metrics.
    const SHELL_ASSETS = ['/', '/index.html', '/app.js', '/format-utils.js', '/features.js', '/version.js'];

    function shellCacheDecision(urlString, origin, method) {
        if (method && method !== 'GET' && method !== 'HEAD') return 'passthrough';
        let url;
        try {
            url = new URL(urlString);
        } catch (e) {
            return 'passthrough';
        }
        if (url.origin !== origin) return 'passthrough';
        const path = url.pathname;
        if (path === '/sw.js') return 'passthrough';
        if (path.startsWith('/api/') || path === '/metrics' || path.endsWith('/metrics')) return 'network-only';
        if (path === '/' || path.endsWith('.html') || path.endsWith('.css') || path.endsWith('.js')) {
            return 'network-first-shell';
        }
        return 'passthrough';
    }

    function responseContentType(resp) {
        if (!resp || !resp.headers) return '';
        if (typeof resp.headers.get === 'function') return resp.headers.get('content-type') || '';
        return resp.headers['content-type'] || resp.headers['Content-Type'] || '';
    }

    // Missing type is allowed (extension was already checked). Prometheus text
    // and JSON must not be stored even if a URL was misclassified as a shell.
    function shouldStoreShellResponse(resp) {
        if (!resp || !resp.ok) return false;
        if (resp.type === 'opaque' || resp.type === 'opaqueredirect') return false;
        const c = String(responseContentType(resp)).toLowerCase();
        if (!c) return true;
        if (c.includes('text/plain') || c.includes('application/json') || c.includes('prometheus')) return false;
        return c.includes('text/html') || c.includes('javascript') || c.includes('text/css');
    }

    async function matchShell(cache, request, origin) {
        const path = new URL(request.url).pathname;
        const keys = [request, path];
        if (request.mode === 'navigate' || path === '/' || path === '/index.html') {
            keys.push('/index.html', '/', origin + '/', origin + '/index.html');
        }
        for (const key of keys) {
            const hit = await cache.match(key);
            if (hit) return hit;
        }
        return null;
    }

    async function handleShellFetch(request, origin, deps) {
        const decision = shellCacheDecision(request.url, origin, request.method || 'GET');
        if (decision === 'passthrough') return null;
        if (decision === 'network-only') {
            return deps.fetch(request, { cache: 'no-store' });
        }
        const cache = await deps.caches.open(deps.cacheName || SHELL_CACHE);
        try {
            const resp = await deps.fetch(request, { cache: 'no-store' });
            if (shouldStoreShellResponse(resp)) {
                try {
                    const copy = typeof resp.clone === 'function' ? resp.clone() : resp;
                    await cache.put(request, copy);
                } catch (e) { /* cache full or a redirected body: keep the live response */ }
            }
            return resp;
        } catch (err) {
            const cached = await matchShell(cache, request, origin);
            if (cached) return cached;
            throw err;
        }
    }

    async function precacheShell(deps) {
        const cache = await deps.caches.open(deps.cacheName || SHELL_CACHE);
        const origin = deps.origin;
        const stored = [];
        for (const path of SHELL_ASSETS) {
            const absolute = new URL(path, origin).href;
            if (shellCacheDecision(absolute, origin, 'GET') !== 'network-first-shell') continue;
            try {
                const resp = await deps.fetch(absolute, { cache: 'no-store', credentials: 'same-origin' });
                if (!shouldStoreShellResponse(resp)) continue;
                await cache.put(path, resp);
                stored.push(path);
            } catch (e) { /* auth or offline during install: runtime fetch fills the cache later */ }
        }
        return stored;
    }

    function installShellWorker(worker) {
        const origin = worker.location.origin;
        const deps = {
            fetch: (input, init) => worker.fetch(input, init),
            caches: worker.caches,
            cacheName: SHELL_CACHE,
            origin: origin,
        };
        worker.addEventListener('install', (event) => {
            event.waitUntil((async () => {
                await precacheShell(deps);
                await worker.skipWaiting();
            })());
        });
        worker.addEventListener('activate', (event) => {
            event.waitUntil((async () => {
                const keys = await worker.caches.keys();
                await Promise.all(keys
                    .filter((key) => key.startsWith('cc-shell-') && key !== SHELL_CACHE)
                    .map((key) => worker.caches.delete(key)));
                await worker.clients.claim();
            })());
        });
        worker.addEventListener('fetch', (event) => {
            const decision = shellCacheDecision(event.request.url, origin, event.request.method);
            if (decision === 'passthrough') return;
            event.respondWith(handleShellFetch(event.request, origin, deps));
        });
    }

    return {
        SHELL_CACHE: SHELL_CACHE,
        SHELL_ASSETS: SHELL_ASSETS,
        shellCacheDecision: shellCacheDecision,
        shouldStoreShellResponse: shouldStoreShellResponse,
        handleShellFetch: handleShellFetch,
        precacheShell: precacheShell,
        installShellWorker: installShellWorker,
    };
});
