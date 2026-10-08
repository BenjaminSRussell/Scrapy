// Pure helpers shared by app.js and node tests (#1043).
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    function parseMetrics(text) {
        const metrics = {};
        for (const line of String(text || '').split('\n')) {
            if (line.startsWith('#') || line.trim() === '') continue;
            const parts = line.trim().split(/\s+/);
            if (parts.length >= 2) {
                const value = parseFloat(parts[parts.length - 1]);
                if (!isNaN(value)) metrics[parts[0]] = value;
            }
        }
        return metrics;
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
    return { parseMetrics, formatNumber, formatBytes, formatEpochTime, countLabelValues, doughnutLegendLayout, createRefreshScheduler };
});
