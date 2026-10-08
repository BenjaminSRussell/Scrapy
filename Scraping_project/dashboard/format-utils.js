// Pure helpers shared by app.js and node tests (#1043).
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    // Prometheus text exposition: `name{l1="v",l2="w"} value [timestamp_ms]`.
    // Label values may contain spaces, commas and escaped quotes.
    const SERIES_RE = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(?:[^"}]|"(?:[^"\\]|\\.)*")*\})?\s+(\S+)(?:\s+-?\d+)?\s*$/;
    // #365: labelled series are kept under their full key (e.g. `x{a="b"}`, used
    // by countLabelValues) AND summed under the bare metric name, unless the
    // exporter also emits an unlabelled series of that name, which then wins.
    // So `errors_total{stage="s1"} 3` + `errors_total{stage="s2"} 4` gives
    // metrics.errors_total === 7 for the flat names updateDashboard reads.
    function parseMetrics(text) {
        const metrics = {};
        const sums = Object.create(null);
        const unlabelled = new Set();
        for (const raw of String(text || '').split('\n')) {
            const line = raw.trim();
            if (line === '' || line.startsWith('#')) continue;
            const m = line.match(SERIES_RE);
            if (!m) continue;
            const value = parseFloat(m[3]);
            if (isNaN(value)) continue;
            const name = m[1];
            const labels = m[2] || '';
            metrics[name + labels] = value;
            if (labels) sums[name] = (sums[name] || 0) + value;
            else unlabelled.add(name);
        }
        for (const name of Object.keys(sums)) {
            if (!unlabelled.has(name)) metrics[name] = sums[name];
        }
        return metrics;
    }
    // #141: every throughput figure is items per MINUTE, computed from the real
    // time between samples (refreshes can be paused or late). Counter resets
    // and missing values read as 0 rather than negative or NaN rates.
    function ratePerMinute(current, previous, elapsedMs) {
        const c = Number(current), p = Number(previous), ms = Number(elapsedMs);
        if (!Number.isFinite(c) || !Number.isFinite(p) || !Number.isFinite(ms) || ms <= 0) return 0;
        const delta = c - p;
        if (delta <= 0) return 0;
        return delta / (ms / 60000);
    }
    // #141/#400: no hard-coded localhost. Precedence: explicit override
    // (window.CC_METRICS_URL), then ?metrics=<url>, then the same-origin proxy
    // /api/metrics on serve.py (METRICS_UPSTREAM picks the exporter server-side).
    // Opened as a file:// page there is no proxy, so fall back to localhost:9090.
    function resolveMetricsUrl(loc, override) {
        if (override) return String(override);
        const search = (loc && loc.search) || '';
        const fromQuery = new URLSearchParams(search).get('metrics');
        if (fromQuery && /^https?:\/\/[^\s]+$/i.test(fromQuery)) return fromQuery;  // http(s) only
        if (loc && /^https?:$/.test(loc.protocol)) return '/api/metrics';
        return 'http://localhost:9090/metrics';
    }
    // #401: /api/queues payload -> rows for the System tab. Redis down (ok:false)
    // is reported as an error, never as zero-depth queues.
    function queueDepthRows(payload) {
        if (!payload || payload.ok !== true || !Array.isArray(payload.queues)) {
            const why = payload && typeof payload.error === 'string' ? payload.error : 'no data';
            return { error: `Queue depths unavailable: ${why}`, rows: [] };
        }
        const rows = payload.queues
            .filter(q => q && typeof q.key === 'string')
            .map(q => ({
                key: q.key,
                type: typeof q.type === 'string' ? q.type : 'unknown',
                depth: Number.isFinite(Number(q.depth)) ? Number(q.depth) : null,
            }));
        return { error: null, rows };
    }
    // #910: an explicit ?metrics= that we refuse must be reported, not silently
    // replaced by the default (the operator would watch the wrong exporter).
    function metricsUrlProblem(loc) {
        const search = (loc && loc.search) || '';
        const raw = new URLSearchParams(search).get('metrics');
        if (raw === null) return null;
        if (/^https?:\/\/[^\s]+$/i.test(raw)) return null;
        const shown = raw.length > 80 ? raw.slice(0, 80) + '\u2026' : raw;
        return `Ignored ?metrics=${JSON.stringify(shown)}: only http(s):// URLs are allowed; using the default exporter URL.`;
    }
    // #906: split text into plain and http(s)-link segments so callers can build
    // DOM nodes (textContent / href) instead of HTML strings. Trailing
    // punctuation is kept out of the link. Other schemes are never links.
    function splitLinks(text) {
        const s = String(text ?? '');
        const out = [];
        const re = /https?:\/\/[^\s<>"']+/gi;
        let last = 0;
        let m;
        while ((m = re.exec(s)) !== null) {
            let url = m[0];
            const trail = url.match(/[.,;:!?)\]]+$/);
            if (trail) url = url.slice(0, -trail[0].length);
            if (m.index > last) out.push({ text: s.slice(last, m.index) });
            out.push({ text: url, href: url });
            last = m.index + url.length;
            re.lastIndex = last;
        }
        if (last < s.length) out.push({ text: s.slice(last) });
        return out;
    }
    function formatNumber(num) {
        num = Number(num) || 0;
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return Math.round(num).toLocaleString('en-US');
    }
    function formatBytes(bytes) {
        bytes = Number(bytes) || 0;
        if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(2) + ' GB';
        if (bytes >= 1048576) return (bytes / 1048576).toFixed(2) + ' MB';
        if (bytes >= 1024) return (bytes / 1024).toFixed(2) + ' KB';
        return bytes + ' B';
    }
    // #364: a missing or zero epoch rendered "Invalid Date" (or 1970). Show a
    // dash instead so "never updated" reads as that, not as a bug.
    function formatEpochTime(seconds) {
        const n = Number(seconds);
        if (!Number.isFinite(n) || n <= 0) return '\u2014';
        const d = new Date(n * 1000);
        return isNaN(d.getTime()) ? '\u2014' : d.toLocaleTimeString();
    }
    // #389: count distinct values of `label` across series whose name contains
    // `nameFragment` (e.g. tables reporting delta_lake_records). null when no
    // such series exists, so the caller can say "not reported" instead of
    // inventing a number.
    function countLabelValues(metrics, nameFragment, label) {
        const seen = new Set();
        const re = new RegExp(label + '="([^"]*)"');
        for (const key of Object.keys(metrics || {})) {
            if (!key.includes(nameFragment)) continue;
            const m = key.match(re);
            if (m) seen.add(m[1]);
        }
        return seen.size > 0 ? seen.size : null;
    }
    // Which pipeline stages are currently doing work (#976). Order matches the
    // four Pipeline-tab stage cards. A stage is active when its per-interval
    // rate is a finite number above `threshold`; missing/NaN rates (series not
    // exported, first sample) count as idle rather than guessing.
    const STAGE_RATE_KEYS = ['urls', 'pages', 'summaries', 'largeDocs'];
    function stageActivity(rates, threshold = 0) {
        return STAGE_RATE_KEYS.map(k => {
            const v = Number(rates && rates[k]);
            return Number.isFinite(v) && v > threshold;
        });
    }
    // Doughnut legend layout by canvas width (#977): beside the ring when the
    // card is wide (a doughnut leaves horizontal space unused), compact labels
    // underneath when narrow so the legend never clips or eats the ring.
    function doughnutLegendLayout(width) {
        if (Number(width) >= 480) {
            return { position: 'right', labels: { boxWidth: 14, padding: 12, font: { size: 12 } } };
        }
        return { position: 'bottom', labels: { boxWidth: 10, padding: 6, font: { size: 11 } } };
    }
    // Single refresh scheduler (#986). Owns the only fetch timer and records
    // when the next fetch will start, so the visible countdown is derived from
    // `nextFetchAt` instead of a second, independently ticking counter that
    // drifted against the real poll. Timers/clock are injectable for tests.
    function createRefreshScheduler(opts) {
        let interval = opts.interval;
        const doFetch = opts.fetch;
        const now = opts.now || (() => Date.now());
        const setT = opts.setTimeout || ((fn, ms) => setTimeout(fn, ms));
        const clearT = opts.clearTimeout || ((id) => clearTimeout(id));
        let timer = null;
        let nextAt = null;
        let paused = false;
        let inFlight = null;

        function cancel() {
            if (timer !== null) clearT(timer);
            timer = null;
            nextAt = null;
        }
        function schedule() {
            cancel();
            if (paused) return;
            nextAt = now() + interval;
            timer = setT(run, interval);
        }
        function run() {
            if (inFlight) return inFlight;
            cancel(); // countdown reads 0 while the fetch is running
            let started;
            try {
                started = Promise.resolve(doFetch()); // starts synchronously at 0
            } catch (err) {
                started = Promise.reject(err);
            }
            inFlight = started
                .catch(() => {})
                .then(() => { inFlight = null; schedule(); });
            return inFlight;
        }
        return {
            start: run,
            refreshNow: run,
            setPaused(p) {
                paused = !!p;
                if (paused) cancel();
                else if (!inFlight && timer === null) schedule();
            },
            // #1015: change the poll interval; a pending wait restarts with the new length.
            setInterval(ms) {
                const v = Number(ms);
                if (!Number.isFinite(v) || v <= 0) return;
                interval = v;
                if (timer !== null) schedule();
            },
            getInterval: () => interval,
            isPaused: () => paused,
            isFetching: () => inFlight !== null,
            nextFetchAt: () => nextAt,
            // Whole seconds until the next fetch starts; 0 while fetching,
            // null while paused.
            secondsRemaining() {
                if (paused) return null;
                if (nextAt === null) return 0;
                return Math.max(0, Math.ceil((nextAt - now()) / 1000));
            },
        };
    }
    // #945: one source of truth for the tab title and the topbar status.
    // kind is the metrics-fetch outcome ('online' | 'never' | 'offline');
    // pipelineRunning is the optional `pipeline_running` gauge (0 => stopped).
    function connectionState(kind, pipelineRunning, failures) {
        if (kind === 'online') {
            if (pipelineRunning === 0) {
                return { key: 'stopped', title: 'OFFLINE', glyph: '\u25CB', top: '\u25D0 Pipeline offline' };
            }
            return { key: 'online', title: 'ONLINE', glyph: '\u25CF', top: '\u25CF Online' };
        }
        const n = Math.max(0, Math.floor(Number(failures) || 0));
        const suffix = n > 1 ? ' (' + n + ' failed)' : '';
        if (kind === 'never') {
            return { key: 'never', title: 'ERROR' + suffix, glyph: '\u26A0', top: '\u25CB Not connected' };
        }
        return { key: 'offline', title: 'ERROR' + suffix, glyph: '\u26A0', top: '\u25CF Disconnected' };
    }
    function documentTitle(state, baseTitle) {
        const base = String(baseTitle || '').trim();
        const head = state.glyph + ' ' + state.title;
        return base ? head + ' \u00B7 ' + base : head;
    }
    // #962: what (if anything) to say to screen readers for a new activity
    // item. Identical consecutive messages (e.g. "Failed to fetch metrics"
    // every poll during an outage) are spoken once per repeatMs.
    const ACTIVITY_SPOKEN_TYPE = { success: 'Success', warning: 'Warning', danger: 'Error', info: 'Info', timeout: 'Timeout' };
    function createActivityAnnouncer(opts) {
        const o = opts || {};
        const now = o.now || (() => Date.now());
        const repeatMs = o.repeatMs == null ? 300000 : o.repeatMs;
        let lastKey = null;
        let lastAt = -Infinity;
        return {
            text(type, message) {
                const msg = String(message == null ? '' : message).replace(/\s+/g, ' ').trim();
                if (!msg) return null;
                const label = ACTIVITY_SPOKEN_TYPE[type] || 'Info';
                const key = label + '\u0000' + msg;
                const t = now();
                if (key === lastKey && t - lastAt < repeatMs) return null;
                lastKey = key;
                lastAt = t;
                return label + ': ' + msg;
            },
        };
    }
    // #1031/#1032: one metrics request at a time, bounded by a timeout. Starting a
    // new request aborts the previous one (its rejection has superseded=true so
    // callers can ignore it); a request still running after timeoutMs is
    // aborted and rejects with timedOut=true.
    function createMetricsFetcher(opts) {
        const o = opts || {};
        const doFetch = o.fetch;
        const timeoutMs = o.timeoutMs || 4000;
        const AC = o.AbortController || (typeof AbortController !== 'undefined' ? AbortController : null);
        const setT = o.setTimeout || ((fn, ms) => setTimeout(fn, ms));
        const clearT = o.clearTimeout || ((id) => clearTimeout(id));
        let current = null;
        function fetchText(url) {
            if (current) { current.superseded = true; if (current.ctrl) current.ctrl.abort(); }
            const req = { ctrl: AC ? new AC() : null, superseded: false, timedOut: false };
            current = req;
            const timer = setT(() => { req.timedOut = true; if (req.ctrl) req.ctrl.abort(); }, timeoutMs);
            const init = req.ctrl ? { signal: req.ctrl.signal } : {};
            return Promise.resolve()
                .then(() => doFetch(url, init))
                .then(response => {
                    if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    return response.text();
                })
                .catch(err => {
                    let e = err;
                    if (req.timedOut) {
                        e = new Error(`Timed out after ${Math.round(timeoutMs / 100) / 10}s`);
                        e.timedOut = true;
                    } else if (req.superseded) {
                        e = new Error('Superseded by a newer request');
                        e.superseded = true;
                    }
                    throw e;
                })
                .finally(() => { clearT(timer); if (current === req) current = null; });
        }
        return { fetchText, inFlight: () => current !== null, timeoutMs };
    }
    // #1015: allowed auto-refresh intervals (ms); anything else falls back to 5s.
    const REFRESH_INTERVAL_CHOICES = [5000, 10000, 30000];
    function parseRefreshInterval(value, fallback = 5000) {
        const v = Number(value);
        return REFRESH_INTERVAL_CHOICES.includes(v) ? v : fallback;
    }
    // #952/#1010: keyboard shortcuts. Digits 1..N pick the Nth visible tab, R
    // refreshes. Ignored with modifier keys, during IME composition, or while
    // typing in a form field / contenteditable.
    function shortcutAction(event, tabNames) {
        if (!event || event.defaultPrevented || event.isComposing) return null;
        if (event.ctrlKey || event.metaKey || event.altKey) return null;
        const t = event.target || {};
        const tag = String(t.tagName || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable) return null;
        const key = String(event.key || '');
        if (/^[1-9]$/.test(key)) {
            const tab = (tabNames || [])[Number(key) - 1];
            return tab ? { type: 'tab', tab } : null;
        }
        if (key === 'r' || key === 'R') return { type: 'refresh' };
        return null;
    }
    // #1034: a deep link (?tab= / #tab) wins; otherwise the remembered tab if it
    // still exists; otherwise null (keep the default tab).
    function pickInitialTab(deepLink, stored, validTabs) {
        const valid = new Set(validTabs || []);
        if (deepLink && valid.has(deepLink)) return deepLink;
        if (stored && valid.has(stored)) return stored;
        return null;
    }
    // #767: a timed-out metrics request is its own activity type, not a generic error.
    function metricsFailureActivity(error, timeoutMs) {
        if (error && error.timedOut) {
            const secs = Math.round((Number(timeoutMs) || 0) / 100) / 10;
            return { type: 'timeout', message: `Metrics request timed out after ${secs}s and was aborted` };
        }
        return { type: 'danger', message: `Failed to fetch metrics: ${error && error.message ? error.message : String(error)}` };
    }
    // #503: on-screen hint text for the digit shortcuts.
    function shortcutHintText(tabCount) {
        const n = Math.min(Math.max(0, Number(tabCount) || 0), 9);
        if (n === 0) return 'Shortcut: R refresh';
        return `Shortcuts: ${n === 1 ? '1' : '1\u2013' + n} switch tabs \u00b7 R refresh`;
    }
    return { queueDepthRows, metricsFailureActivity, shortcutHintText, parseMetrics, ratePerMinute, resolveMetricsUrl, formatNumber, formatBytes, formatEpochTime, countLabelValues, createRefreshScheduler, stageActivity, doughnutLegendLayout, connectionState, documentTitle, createActivityAnnouncer, metricsUrlProblem, splitLinks, createMetricsFetcher, REFRESH_INTERVAL_CHOICES, parseRefreshInterval, shortcutAction, pickInitialTab };
});
