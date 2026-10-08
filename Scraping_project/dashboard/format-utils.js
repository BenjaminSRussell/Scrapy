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
    // #141: no hard-coded localhost. Precedence: explicit override
    // (window.CC_METRICS_URL), then ?metrics=<url>, then port 9090 on the host the
    // dashboard was loaded from (works from other machines / containers).
    function resolveMetricsUrl(loc, override) {
        if (override) return String(override);
        const search = (loc && loc.search) || '';
        const fromQuery = new URLSearchParams(search).get('metrics');
        if (fromQuery && /^https?:\/\/[^\s]+$/i.test(fromQuery)) return fromQuery;  // http(s) only
        const protocol = loc && /^https?:$/.test(loc.protocol) ? loc.protocol : 'http:';
        const host = (loc && loc.hostname) || 'localhost';
        return `${protocol}//${host}:9090/metrics`;
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
    // Single refresh scheduler (#986). Owns the only fetch timer and records
    // when the next fetch will start, so the visible countdown is derived from
    // `nextFetchAt` instead of a second, independently ticking counter that
    // drifted against the real poll. Timers/clock are injectable for tests.
    function createRefreshScheduler(opts) {
        const interval = opts.interval;
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
    return { parseMetrics, ratePerMinute, resolveMetricsUrl, formatNumber, formatBytes, formatEpochTime, countLabelValues, createRefreshScheduler, stageActivity };
});
