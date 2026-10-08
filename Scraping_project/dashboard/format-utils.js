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
    return { parseMetrics, formatNumber, formatBytes, formatEpochTime, countLabelValues, stageActivity };
});
