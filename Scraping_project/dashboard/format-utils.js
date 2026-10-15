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
    // #690: one exposition sample line is one series. The count is a
    // non-enumerable symbol so Object.keys / spread stay the flat map.
    const METRIC_SERIES_COUNT = Symbol('metricSeriesCount');
    function parseMetrics(text) {
        const metrics = {};
        const sums = Object.create(null);
        const unlabelled = new Set();
        let seriesCount = 0;
        for (const raw of String(text || '').split('\n')) {
            const line = raw.trim();
            if (line === '' || line.startsWith('#')) continue;
            const m = line.match(SERIES_RE);
            if (!m) continue;
            const value = parseFloat(m[3]);
            if (isNaN(value)) continue;
            seriesCount += 1;
            const name = m[1];
            const labels = m[2] || '';
            metrics[name + labels] = value;
            if (labels) sums[name] = (sums[name] || 0) + value;
            else unlabelled.add(name);
        }
        for (const name of Object.keys(sums)) {
            if (!unlabelled.has(name)) metrics[name] = sums[name];
        }
        Object.defineProperty(metrics, METRIC_SERIES_COUNT, { value: seriesCount, enumerable: false });
        return metrics;
    }
    function metricSeriesCount(metrics) {
        if (!metrics) return 0;
        const n = metrics[METRIC_SERIES_COUNT];
        return Number.isFinite(n) ? n : 0;
    }
    // #690: chart history is noise (and CPU) once a scrape is too wide or too
    // slow to parse. Defaults are the documented Control Center knobs.
    // Query params override window.CC_METRICS_FLOOD; bad values keep the default.
    // A sample trips flood only when it is strictly over a limit.
    const METRICS_FLOOD_DEFAULTS = Object.freeze({
        maxSeries: 2000,
        maxParseMs: 50,
        recoverIntervals: 3,
    });
    function finiteAtLeast(raw, min, fallback) {
        const n = Number(raw);
        if (!Number.isFinite(n) || n < min) return fallback;
        return n;
    }
    function metricsFloodLimits(source) {
        const src = source || {};
        const cfg = (src.CC_METRICS_FLOOD && typeof src.CC_METRICS_FLOOD === 'object') ? src.CC_METRICS_FLOOD : {};
        let search = '';
        if (typeof src.search === 'string') search = src.search;
        else if (src.location && typeof src.location.search === 'string') search = src.location.search;
        const params = new URLSearchParams(search);
        function pick(key, queryName, fallback, min, integer) {
            const q = params.get(queryName);
            const raw = (q != null && q !== '') ? q : cfg[key];
            let n = finiteAtLeast(raw, min, fallback);
            if (integer) n = Math.floor(n);
            if (!Number.isFinite(n) || n < min) return fallback;
            return n;
        }
        return {
            maxSeries: pick('maxSeries', 'floodMaxSeries', METRICS_FLOOD_DEFAULTS.maxSeries, 0, true),
            maxParseMs: pick('maxParseMs', 'floodMaxParseMs', METRICS_FLOOD_DEFAULTS.maxParseMs, 0, false),
            recoverIntervals: pick('recoverIntervals', 'floodRecover', METRICS_FLOOD_DEFAULTS.recoverIntervals, 1, true),
        };
    }
    // state: { degraded, calm }. Calm counts consecutive under-limit samples
    // while already degraded; one over-limit sample zeros the streak.
    function advanceMetricsFlood(state, sample, limits) {
        const limIn = limits || METRICS_FLOOD_DEFAULTS;
        const maxSeries = finiteAtLeast(limIn.maxSeries, 0, METRICS_FLOOD_DEFAULTS.maxSeries);
        const maxParseMs = finiteAtLeast(limIn.maxParseMs, 0, METRICS_FLOOD_DEFAULTS.maxParseMs);
        const recoverIntervals = Math.floor(finiteAtLeast(limIn.recoverIntervals, 1, METRICS_FLOOD_DEFAULTS.recoverIntervals));
        const prevDegraded = !!(state && state.degraded);
        const prevCalm = (state && Number.isFinite(Number(state.calm))) ? Number(state.calm) : 0;
        const seriesCount = Math.max(0, Number(sample && sample.seriesCount) || 0);
        const parseMs = Math.max(0, Number(sample && sample.parseMs) || 0);
        const overSeries = seriesCount > maxSeries;
        const overParse = parseMs > maxParseMs;
        const flooded = overSeries || overParse;
        let degraded = prevDegraded;
        let calm = prevCalm;
        let recovered = false;
        if (flooded) {
            degraded = true;
            calm = 0;
        } else if (prevDegraded) {
            calm = prevCalm + 1;
            if (calm >= recoverIntervals) {
                degraded = false;
                calm = 0;
                recovered = true;
            }
        } else {
            calm = 0;
        }
        return {
            degraded,
            calm,
            recovered,
            flooded,
            overSeries,
            overParse,
            entered: degraded && !prevDegraded,
            seriesCount,
            parseMs,
            limits: { maxSeries, maxParseMs, recoverIntervals },
        };
    }
    function metricsFloodBannerText(decision) {
        const d = decision || {};
        if (!d.degraded) return '';
        const lim = d.limits || METRICS_FLOOD_DEFAULTS;
        const series = Math.round(Number(d.seriesCount) || 0);
        const parseMs = Math.round((Number(d.parseMs) || 0) * 10) / 10;
        if (d.flooded) {
            const bits = [];
            if (d.overSeries) bits.push('series count ' + formatNumber(series, undefined, 0) + ' exceeds ' + formatNumber(lim.maxSeries, undefined, 0));
            if (d.overParse) bits.push('parse time ' + formatNumber(parseMs, undefined, Number.isInteger(parseMs) ? 0 : 1) + 'ms exceeds ' + formatNumber(lim.maxParseMs, undefined, 0) + 'ms');
            const why = bits.length ? bits.join(' and ') : 'the scrape is over the flood threshold';
            return 'Metrics flood: charts paused. Showing summary counters only because ' + why + '. Charts return after ' + formatNumber(lim.recoverIntervals, undefined, 0) + ' calm intervals.';
        }
        const left = Math.max(1, lim.recoverIntervals - (Number(d.calm) || 0));
        return 'Metrics flood: charts still paused. Showing summary counters only. Last sample is under the limit (' + formatNumber(series, undefined, 0) + ' series, ' + formatNumber(parseMs, undefined, Number.isInteger(parseMs) ? 0 : 1) + 'ms). Charts return after ' + formatNumber(left, undefined, 0) + ' more calm interval' + (left === 1 ? '' : 's') + '.';
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
    // #960: one locale for every user-visible number and clock. An explicit
    // argument wins. The browser page follows navigator.language so a non-en-US
    // operator does not see en-US counts next to a local clock. Node tests and
    // the vm sandboxes have no document, so they stay en-US.
    const DEFAULT_LOCALE = 'en-US';
    // Unit words live here so a later translation can replace them without
    // hunting string literals. The words stay English until that pass.
    const UNIT_STRINGS = {
        perMin: '/min',
        urlsPerMin: 'URLs/min',
        pagesPerMin: 'pages/min',
        summariesPerMin: 'summaries/min',
        docsPerMin: 'docs/min',
        compression: 'x',
        byte: 'B',
        kilobyte: 'KB',
        megabyte: 'MB',
        gigabyte: 'GB',
        agoSecond: 's ago',
        agoMinute: 'm ago',
        agoHour: 'h ago',
        never: 'never',
        neverClock: 'Never',
        uptimeHour: 'h',
        uptimeMinute: 'm',
        uptimeSecond: 's',
        thousand: 'K',
        million: 'M',
    };
    function dashboardLocale(explicit) {
        if (explicit != null && String(explicit).trim() !== '') return String(explicit);
        try {
            const inBrowser = typeof window !== 'undefined' && window.document;
            const language = inBrowser && typeof navigator !== 'undefined' && navigator.language;
            if (language) return String(language);
        } catch (_) { /* keep the default */ }
        return DEFAULT_LOCALE;
    }
    function formatDecimal(num, digits, locale) {
        const loc = dashboardLocale(locale);
        const opts = {};
        if (digits != null) {
            opts.minimumFractionDigits = digits;
            opts.maximumFractionDigits = digits;
        }
        try {
            return num.toLocaleString(loc, opts);
        } catch (_) {
            return num.toLocaleString(DEFAULT_LOCALE, opts);
        }
    }
    // Compact counts (K/M) when `digits` is omitted, so existing callers keep
    // 1.5K / 2.5M. Pass digits to force a fixed fraction and skip the suffix
    // (rates, exact table cells). Non-finite values are an em dash, never "NaN".
    function formatNumber(num, locale, digits) {
        const n = Number(num);
        if (!Number.isFinite(n)) return '\u2014';
        if (digits == null) {
            if (n >= 1000000) return formatDecimal(n / 1000000, 1, locale) + UNIT_STRINGS.million;
            if (n >= 1000) return formatDecimal(n / 1000, 1, locale) + UNIT_STRINGS.thousand;
            return formatDecimal(Math.round(n), null, locale);
        }
        const places = Math.floor(Number(digits));
        return formatDecimal(n, Number.isFinite(places) && places >= 0 ? places : 0, locale);
    }
    function formatBytes(bytes, locale) {
        const n = Number(bytes);
        if (!Number.isFinite(n)) return '\u2014';
        if (n >= 1073741824) return formatDecimal(n / 1073741824, 2, locale) + ' ' + UNIT_STRINGS.gigabyte;
        if (n >= 1048576) return formatDecimal(n / 1048576, 2, locale) + ' ' + UNIT_STRINGS.megabyte;
        if (n >= 1024) return formatDecimal(n / 1024, 2, locale) + ' ' + UNIT_STRINGS.kilobyte;
        return formatNumber(n, locale, 0) + ' ' + UNIT_STRINGS.byte;
    }
    // Accepts a Date (including a Date from another realm) or anything Date
    // can parse. Invalid input is an em dash, never "Invalid Date".
    function asDate(value) {
        if (value == null || value === '') return null;
        if (typeof value === 'object' && typeof value.getTime === 'function') {
            const t = value.getTime();
            return Number.isFinite(t) ? new Date(t) : null;
        }
        const d = new Date(value);
        return Number.isFinite(d.getTime()) ? d : null;
    }
    function formatTime(value, locale, options) {
        const d = asDate(value);
        if (!d) return '\u2014';
        const loc = dashboardLocale(locale);
        try {
            return d.toLocaleTimeString(loc, options);
        } catch (_) {
            try { return d.toLocaleTimeString(DEFAULT_LOCALE, options); }
            catch (__) { return '\u2014'; }
        }
    }
    function formatDate(value, locale, options) {
        const d = asDate(value);
        if (!d) return '\u2014';
        const loc = dashboardLocale(locale);
        try {
            return d.toLocaleDateString(loc, options);
        } catch (_) {
            try { return d.toLocaleDateString(DEFAULT_LOCALE, options); }
            catch (__) { return '\u2014'; }
        }
    }
    // #364: a missing or zero epoch rendered "Invalid Date" (or 1970). Show a
    // dash instead so "never updated" reads as that, not as a bug.
    function formatEpochTime(seconds, locale) {
        const n = Number(seconds);
        if (!Number.isFinite(n) || n <= 0) return '\u2014';
        return formatTime(new Date(n * 1000), locale);
    }
    // `unitKey` is a UNIT_STRINGS name. A leading slash stays glued ("1.0/min");
    // other units get a space ("1.0 URLs/min"). Non-finite rates are a dash.
    function formatRate(value, unitKey, locale) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '\u2014';
        const unit = Object.prototype.hasOwnProperty.call(UNIT_STRINGS, unitKey) ? UNIT_STRINGS[unitKey] : '';
        const body = formatNumber(n, locale, 1);
        if (!unit) return body;
        return unit.charAt(0) === '/' ? body + unit : body + ' ' + unit;
    }
    // Stage 4 shows (1/ratio)x. A missing or non-positive ratio is 0x, not NaN.
    function formatCompression(ratio, locale) {
        const n = Number(ratio);
        if (!Number.isFinite(n) || n <= 0) return formatNumber(0, locale, 0) + UNIT_STRINGS.compression;
        const shown = 1 / n;
        if (!Number.isFinite(shown)) return '\u2014';
        return formatNumber(shown, locale, 0) + UNIT_STRINGS.compression;
    }
    function formatUptime(elapsedMs, locale) {
        const ms = Number(elapsedMs);
        if (!Number.isFinite(ms) || ms < 0) return '\u2014';
        const seconds = Math.floor(ms / 1000);
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;
        const u = UNIT_STRINGS;
        const part = (v) => formatNumber(v, locale, 0);
        if (hours > 0) return part(hours) + u.uptimeHour + ' ' + part(minutes) + u.uptimeMinute;
        if (minutes > 0) return part(minutes) + u.uptimeMinute + ' ' + part(secs) + u.uptimeSecond;
        return part(secs) + u.uptimeSecond;
    }
    function formatRelative(ts, now, locale) {
        if (ts == null || ts === '') return UNIT_STRINGS.never;
        const then = Number(ts);
        const clock = now == null ? Date.now() : Number(now);
        if (!Number.isFinite(then) || !Number.isFinite(clock)) return UNIT_STRINGS.never;
        const sec = Math.max(0, Math.floor((clock - then) / 1000));
        const part = (v) => formatNumber(v, locale, 0);
        if (sec < 60) return part(sec) + UNIT_STRINGS.agoSecond;
        if (sec < 3600) return part(Math.floor(sec / 60)) + UNIT_STRINGS.agoMinute;
        return part(Math.floor(sec / 3600)) + UNIT_STRINGS.agoHour;
    }
    // Chart axis label. Include a short date only when the day key changes
    // (midnight). `previousDayKey` null means this is the first sample.
    function formatHistoryStamp(date, previousDayKey, locale) {
        const d = asDate(date);
        if (!d) return { label: '\u2014', dayKey: previousDayKey == null ? null : previousDayKey };
        const dayKey = d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate();
        const time = formatTime(d, locale);
        if (time === '\u2014') return { label: '\u2014', dayKey: previousDayKey == null ? null : previousDayKey };
        if (previousDayKey != null && previousDayKey !== dayKey) {
            const day = formatDate(d, locale, { month: '2-digit', day: '2-digit' });
            if (day !== '\u2014') return { label: day + ' ' + time, dayKey: dayKey };
        }
        return { label: time, dayKey: dayKey };
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
    // Comparison mode (#1035). The metrics feed does not publish previous-hour
    // averages yet. A missing or non-finite gauge stays a dashed placeholder
    // (flat zero); a finite gauge replaces that series with a horizontal line.
    const COMPARISON_BASELINE_ROLE = 'prev-hour';
    const PREV_HOUR_AVG_KEYS = {
        urls: 'stage1_urls_discovered_prev_hour_avg',
        pages: 'stage2_pages_analyzed_prev_hour_avg',
        summaries: 'stage3_summaries_created_prev_hour_avg',
        urlsRate: 'stage1_urls_per_min_prev_hour_avg',
        pagesRate: 'stage2_pages_per_min_prev_hour_avg',
    };
    function finiteNumberOrNull(value) {
        if (value == null || value === '') return null;
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }
    function readPrevHourAverage(metrics, seriesKey) {
        const name = PREV_HOUR_AVG_KEYS[seriesKey];
        if (!name) return null;
        return finiteNumberOrNull(metrics && metrics[name]);
    }
    function comparisonBaselineSeries(pointCount, average) {
        const n = Math.max(0, Math.floor(Number(pointCount) || 0));
        const value = finiteNumberOrNull(average);
        const placeholder = value === null;
        const data = [];
        for (let i = 0; i < n; i++) data.push(placeholder ? 0 : value);
        return {
            ccBaseline: COMPARISON_BASELINE_ROLE,
            label: placeholder ? 'Previous hour (placeholder)' : 'Previous hour avg',
            data: data,
            placeholder: placeholder,
            borderDash: [6, 4],
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false,
            tension: 0,
            borderColor: '#475569',
            backgroundColor: 'transparent',
        };
    }
    function comparisonNote(enabled, placeholderCount, realCount) {
        if (!enabled) return '';
        const placeholders = Number(placeholderCount) || 0;
        const reals = Number(realCount) || 0;
        if (reals > 0 && placeholders === 0) {
            return 'Comparison on. Dashed lines are the previous hour average.';
        }
        if (reals > 0 && placeholders > 0) {
            return 'Comparison on. Dashed lines use previous-hour averages where the metrics feed provides them; other series stay on the placeholder baseline.';
        }
        if (placeholders === 0) {
            return 'Comparison on. Placeholder baseline is ready and will overlay the line charts when they are available.';
        }
        return 'Comparison on. Showing a dashed placeholder baseline until previous-hour averages are in the metrics feed.';
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
        const suffix = n > 1 ? ' (' + formatNumber(n, undefined, 0) + ' failed)' : '';
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
                        const secs = Math.round(timeoutMs / 100) / 10;
                        e = new Error('Timed out after ' + formatNumber(secs, undefined, Number.isInteger(secs) ? 0 : 1) + 's');
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
    // #1037: danger chime is opt-in. Missing or unrecognised storage stays off.
    function parseDangerSoundEnabled(stored) {
        if (stored === true || stored === 1) return true;
        if (stored == null || stored === false || stored === 0) return false;
        const s = String(stored).trim().toLowerCase();
        return s === '1' || s === 'true' || s === 'on' || s === 'yes';
    }
    // Play only for a new danger row, and only when the operator turned sound
    // on. reducedMotion and prefersMuted win over the toggle. announced false
    // is a repeat the activity announcer already suppressed.
    function dangerSoundDecision(opts) {
        const o = opts || {};
        if (String(o.type || '') !== 'danger') return { play: false, reason: 'not-danger' };
        if (!parseDangerSoundEnabled(o.enabled)) return { play: false, reason: 'off' };
        if (o.reducedMotion) return { play: false, reason: 'reduced-motion' };
        if (o.prefersMuted) return { play: false, reason: 'prefers-muted' };
        if (o.announced === false) return { play: false, reason: 'repeat' };
        return { play: true, reason: 'danger' };
    }
    // #767: a timed-out metrics request is its own activity type, not a generic error.
    function metricsFailureActivity(error, timeoutMs) {
        if (error && error.timedOut) {
            const secs = Math.round((Number(timeoutMs) || 0) / 100) / 10;
            const shown = formatNumber(secs, undefined, Number.isInteger(secs) ? 0 : 1);
            return { type: 'timeout', message: `Metrics request timed out after ${shown}s and was aborted` };
        }
        return { type: 'danger', message: `Failed to fetch metrics: ${error && error.message ? error.message : String(error)}` };
    }
    // #1027: Activity-tab chips. An empty selection shows every level; otherwise
    // only the pressed levels. Counts cover the whole log, not the filtered slice.
    const ACTIVITY_LEVELS = ['success', 'warning', 'danger', 'timeout', 'info'];
    function activityLevelCounts(items) {
        const counts = {};
        for (const level of ACTIVITY_LEVELS) counts[level] = 0;
        for (const item of items || []) {
            const type = item && item.type;
            if (Object.prototype.hasOwnProperty.call(counts, type)) counts[type] += 1;
        }
        return counts;
    }
    function activityLevelSelection(selected) {
        if (selected == null) return [];
        if (typeof selected === 'string') return selected ? [selected] : [];
        return Array.from(selected).filter(level => level);
    }
    function filterActivityItems(items, selected) {
        const list = Array.isArray(items) ? items : [];
        const levels = activityLevelSelection(selected);
        if (levels.length === 0) return list.slice();
        const on = new Set(levels);
        return list.filter(item => item && on.has(item.type));
    }
    function toggleActivityLevel(selected, level) {
        const next = new Set(activityLevelSelection(selected));
        if (!level) return next;
        if (next.has(level)) next.delete(level);
        else next.add(level);
        return next;
    }
    // #1028: case-insensitive substring over activity message text. A blank
    // query matches every item. Not a regex: "." and "(" are literal. Order
    // is preserved and the input array is not mutated. Non-arrays yield [].
    function filterActivityMessages(items, query) {
        const list = Array.isArray(items) ? items : [];
        const q = String(query == null ? '' : query).trim().toLowerCase();
        if (!q) return list.slice();
        const out = [];
        for (const item of list) {
            const msg = String(item && item.message != null ? item.message : '').toLowerCase();
            if (msg.includes(q)) out.push(item);
        }
        return out;
    }
    // Empty string when the query is blank (the status stays hidden). Counts
    // are of the capped Activity-tab buffer, not the overview feed.
    function activityFilterStatus(shown, total, query) {
        const q = String(query == null ? '' : query).trim();
        if (!q) return '';
        const n = Math.max(0, Math.floor(Number(shown) || 0));
        const t = Math.max(0, Math.floor(Number(total) || 0));
        if (n === 0) return `No activity matches "${q}".`;
        return `Showing ${formatNumber(n, undefined, 0)} of ${formatNumber(t, undefined, 0)}`;
    }
    // #503: on-screen hint text for the digit shortcuts.
    function shortcutHintText(tabCount) {
        const n = Math.min(Math.max(0, Number(tabCount) || 0), 9);
        if (n === 0) return 'Shortcut: R refresh';
        const shown = formatNumber(n, undefined, 0);
        const one = formatNumber(1, undefined, 0);
        return `Shortcuts: ${n === 1 ? shown : one + '\u2013' + shown} switch tabs \u00b7 R refresh`;
    }
    // #153: WAI-ARIA tabs keyboard model. Index of the tab to focus/select for
    // `key` from `index` among `count` tabs (wrapping), or null if not a tab key.
    function tabKeyTarget(key, index, count) {
        const n = Math.floor(Number(count) || 0);
        if (n <= 0) return null;
        const i = Math.min(Math.max(Math.floor(Number(index) || 0), 0), n - 1);
        switch (key) {
            case 'ArrowRight': case 'Right': return (i + 1) % n;
            case 'ArrowLeft': case 'Left': return (i - 1 + n) % n;
            case 'Home': return 0;
            case 'End': return n - 1;
            default: return null;
        }
    }
    // #1033: side panel for one pipeline stage. Metrics are a subset of the
    // card (not every line). Job and queue hrefs are fragments only —
    // those views do not exist yet (`placeholder: true`).
    const STAGE_DRILLDOWNS = [
        {
            stage: 1,
            title: 'URL Discovery',
            metrics: [
                { id: 'pipeline-s1-discovered', label: 'URLs Discovered' },
                { id: 'pipeline-s1-queued', label: 'Queued for Stage 2' },
            ],
        },
        {
            stage: 2,
            title: 'Page Analysis',
            metrics: [
                { id: 'pipeline-s2-analyzed', label: 'Pages Analyzed' },
                { id: 'pipeline-s2-quality', label: 'Quality Documents' },
            ],
        },
        {
            stage: 3,
            title: 'Summarization',
            metrics: [
                { id: 'pipeline-s3-summaries', label: 'Summaries Created' },
                { id: 'pipeline-s3-rate', label: 'Processing Rate' },
            ],
        },
        {
            stage: 4,
            title: 'Large Documents',
            metrics: [
                { id: 'pipeline-s4-summaries', label: 'Large Summaries' },
                { id: 'pipeline-s4-rate', label: 'Processing Rate' },
            ],
        },
    ];
    function stageDrilldown(stage) {
        const spec = STAGE_DRILLDOWNS.find(s => s.stage === Number(stage));
        if (!spec) return null;
        return {
            stage: spec.stage,
            title: spec.title,
            metrics: spec.metrics.map(m => ({ id: m.id, label: m.label })),
            links: [
                { kind: 'jobs', label: `Stage ${spec.stage} jobs`, href: '#jobs', placeholder: true },
                { kind: 'queue', label: `Stage ${spec.stage} queue`, href: '#queues', placeholder: true },
            ],
        };
    }
    // Enter/Space activate a stage card; Escape closes the panel. Other keys
    // are left to the tab shortcuts.
    function stageDrilldownKey(key) {
        if (key === 'Enter' || key === ' ' || key === 'Spacebar') return 'activate';
        if (key === 'Escape' || key === 'Esc') return 'close';
        return null;
    }
    // #351: data is stale once the last successful fetch is older than
    // `factor` refresh intervals (default 3; never before the first success,
    // which is "loading", not "stale"). Paused refresh is reported separately.
    function staleState(lastSuccessAt, now, intervalMs, factor = 3) {
        if (!lastSuccessAt) return { stale: false, ageMs: null };
        const ageMs = Math.max(0, Number(now) - Number(lastSuccessAt));
        const limit = Math.max(1, Number(intervalMs) || 5000) * Math.max(1, Number(factor) || 3);
        return { stale: ageMs > limit, ageMs, limitMs: limit };
    }
    // #1038: series the dashboard reads, and the UI fields each one fills.
    // Rendered in the System tab Settings panel. An exporter rename moves a
    // field only when this row changes with the reader in app.js.
    const METRIC_UI_MAP = [
        {
            metric: 'stage1_urls_discovered_total',
            read: 'Counter. Rates are items per minute since the previous sample; the Stage 1 card is active when that rate is above 0. Milestones at 1000, 10000, 100000 and 1000000.',
            fields: [
                { label: 'Top bar · Total URLs', id: 'topbar-urls' },
                { label: 'Overview · URLs Found', id: 'overview-s1-discovered' },
                { label: 'Pipeline · URLs Discovered', id: 'pipeline-s1-discovered' },
                { label: 'Performance · URLs discovered chart', id: 'urls-chart' },
                { label: 'Overview · Stage 1 discovery trend', id: 'spark-overview-s1-discovered' },
                { label: 'Overview · Stage 1 trend direction', id: 'spark-dir-overview-s1-discovered' },
                { label: 'Performance · Stage 1 rate', id: 'perf-s1-rate' },
                { label: 'Overview · Throughput URLs/min', id: 'throughput-chart' },
                { label: 'Pipeline · Stage progression (Stage 1)', id: 'stage-progression-chart' },
                { label: 'Activity log', id: 'activity-log' },
                { label: 'Overview · Recent Activity', id: 'overview-activity' },
            ],
        },
        {
            metric: 'stage1_urls_queued_total',
            read: 'Counter.',
            fields: [
                { label: 'Overview · Queued', id: 'overview-s1-queued' },
                { label: 'Pipeline · Queued for Stage 2', id: 'pipeline-s1-queued' },
            ],
        },
        {
            metric: 'stage2_pages_analyzed_total',
            read: 'Counter. Rates are items per minute; the Stage 2 card is active when that rate is above 0. Same milestone thresholds as URLs discovered.',
            fields: [
                { label: 'Overview · Pages Analyzed', id: 'overview-s2-analyzed' },
                { label: 'Pipeline · Pages Analyzed', id: 'pipeline-s2-analyzed' },
                { label: 'Performance · Pages analyzed chart', id: 'pages-chart' },
                { label: 'Overview · Stage 2 analyze trend', id: 'spark-overview-s2-analyzed' },
                { label: 'Overview · Stage 2 trend direction', id: 'spark-dir-overview-s2-analyzed' },
                { label: 'Performance · Stage 2 rate', id: 'perf-s2-rate' },
                { label: 'Overview · Throughput Pages/min', id: 'throughput-chart' },
                { label: 'Pipeline · Stage progression (Stage 2)', id: 'stage-progression-chart' },
                { label: 'Activity log', id: 'activity-log' },
                { label: 'Overview · Recent Activity', id: 'overview-activity' },
            ],
        },
        {
            metric: 'stage2_quality_docs_total',
            read: 'Counter.',
            fields: [
                { label: 'Overview · Quality Docs', id: 'overview-s2-quality' },
                { label: 'Pipeline · Quality Documents', id: 'pipeline-s2-quality' },
                { label: 'Pipeline · Traffic routing (Quality Docs)', id: 'routing-chart' },
            ],
        },
        {
            metric: 'stage2_massive_docs_total',
            read: 'Counter.',
            fields: [
                { label: 'Pipeline · Massive Documents', id: 'pipeline-s2-massive' },
                { label: 'Pipeline · Traffic routing (Massive Docs)', id: 'routing-chart' },
            ],
        },
        {
            metric: 'stage2_avg_word_count',
            read: 'Gauge.',
            fields: [
                { label: 'Pipeline · Avg Word Count', id: 'pipeline-s2-words' },
            ],
        },
        {
            metric: 'stage3_summaries_created_total',
            read: 'Counter. Rates are items per minute; the Stage 3 card is active when that rate is above 0. The Stage 4 overview spark uses this sample history, not stage4_large_doc_summaries_total.',
            fields: [
                { label: 'Top bar · Summaries', id: 'topbar-summaries' },
                { label: 'Overview · Summaries', id: 'overview-s3-summaries' },
                { label: 'Pipeline · Summaries Created', id: 'pipeline-s3-summaries' },
                { label: 'Performance · Summaries produced chart', id: 'summaries-chart' },
                { label: 'Overview · Stage 3 summaries trend', id: 'spark-overview-s3-summaries' },
                { label: 'Overview · Stage 3 trend direction', id: 'spark-dir-overview-s3-summaries' },
                { label: 'Overview · Stage 4 large-doc trend', id: 'spark-overview-s4-summaries' },
                { label: 'Overview · Stage 4 trend direction', id: 'spark-dir-overview-s4-summaries' },
                { label: 'Pipeline · Stage 3 processing rate', id: 'pipeline-s3-rate' },
                { label: 'Performance · Stage 3 rate', id: 'perf-s3-rate' },
                { label: 'Pipeline · Stage progression (Stage 3)', id: 'stage-progression-chart' },
                { label: 'Activity log', id: 'activity-log' },
                { label: 'Overview · Recent Activity', id: 'overview-activity' },
            ],
        },
        {
            metric: 'stage3_documents_deduplicated_total',
            read: 'Counter. A positive delta is also written to the activity log.',
            fields: [
                { label: 'Overview · Deduplicated', id: 'overview-s3-dedup' },
                { label: 'Pipeline · Deduplicated', id: 'pipeline-s3-dedup' },
                { label: 'Activity log', id: 'activity-log' },
                { label: 'Overview · Recent Activity', id: 'overview-activity' },
            ],
        },
        {
            metric: 'stage4_large_doc_summaries_total',
            read: 'Counter. Rates are items per minute; the Stage 4 card is active when that rate is above 0. Does not feed the Stage 4 overview spark.',
            fields: [
                { label: 'Overview · Processed', id: 'overview-s4-summaries' },
                { label: 'Pipeline · Large Summaries', id: 'pipeline-s4-summaries' },
                { label: 'Pipeline · Stage 4 processing rate', id: 'pipeline-s4-rate' },
                { label: 'Performance · Stage 4 rate', id: 'perf-s4-rate' },
                { label: 'Pipeline · Stage progression (Stage 4)', id: 'stage-progression-chart' },
                { label: 'Activity log', id: 'activity-log' },
                { label: 'Overview · Recent Activity', id: 'overview-activity' },
            ],
        },
        {
            metric: 'stage4_avg_compression_ratio',
            read: 'Gauge. Shown as (1/value)x when value is above 0, otherwise 0x.',
            fields: [
                { label: 'Overview · Compression (out÷in)', id: 'overview-s4-compression' },
                { label: 'Pipeline · Avg Compression', id: 'pipeline-s4-compression' },
            ],
        },
        {
            metric: 'pipeline_redis_keys',
            read: 'Gauge, 0 when absent. Redis health is Healthy when this series or pipeline_redis_memory_bytes is present.',
            fields: [
                { label: 'System · Redis Keys', id: 'redis-keys' },
                { label: 'System · Redis health', id: 'redis-health' },
            ],
        },
        {
            metric: 'pipeline_redis_memory_bytes',
            read: 'Bytes. Redis health uses the same presence check as pipeline_redis_keys.',
            fields: [
                { label: 'System · Memory Used', id: 'redis-memory' },
                { label: 'System · Redis health', id: 'redis-health' },
            ],
        },
        {
            metric: 'pipeline_last_update_timestamp',
            read: 'Unix seconds. Missing, zero, or non-finite shows an em dash.',
            fields: [
                { label: 'System · Last Update timestamp', id: 'last-update' },
            ],
        },
        {
            metric: 'pipeline_scout_instances',
            read: 'Gauge. An absent series shows an em dash, not zero.',
            fields: [
                { label: 'Pipeline · Scout Instances', id: 'scout-instances' },
            ],
        },
        {
            metric: 'pipeline_running',
            read: 'After a successful fetch, 0 is Pipeline offline. Any other value, including an absent series, stays Online.',
            fields: [
                { label: 'Top bar · Status', id: 'topbar-status' },
                { label: 'System · Status', id: 'system-status' },
            ],
        },
        {
            metric: 'delta_lake_records',
            read: 'Count of distinct table label values. The sample value is not shown. No labelled series shows an em dash.',
            fields: [
                { label: 'System · Delta Lake Tables', id: 'delta-table-count' },
            ],
        },
        {
            metric: '(any series)',
            read: 'Collecting when the scrape parsed at least one sample; Stale when it parsed none; Fetch failed after an error.',
            fields: [
                { label: 'System · Metrics health', id: 'metrics-health' },
            ],
        },
    ];
    function renderMetricNameMap(root, doc) {
        const d = doc || (typeof document !== 'undefined' ? document : null);
        if (!root || !d || typeof d.createElement !== 'function') return;
        while (root.firstChild) root.removeChild(root.firstChild);
        const table = d.createElement('table');
        table.className = 'data-fallback-table';
        table.id = 'metric-name-map-table';
        const caption = d.createElement('caption');
        caption.className = 'visually-hidden';
        caption.textContent = 'Prometheus metric names and the UI fields that read them';
        table.appendChild(caption);
        const thead = d.createElement('thead');
        const hr = d.createElement('tr');
        for (const label of ['Prometheus series', 'UI fields', 'Read as']) {
            const th = d.createElement('th');
            th.scope = 'col';
            th.textContent = label;
            hr.appendChild(th);
        }
        thead.appendChild(hr);
        table.appendChild(thead);
        const tbody = d.createElement('tbody');
        for (const row of METRIC_UI_MAP) {
            const tr = d.createElement('tr');
            const name = d.createElement('td');
            const code = d.createElement('code');
            code.textContent = row.metric;
            name.appendChild(code);
            tr.appendChild(name);
            const fields = d.createElement('td');
            const list = d.createElement('ul');
            for (const field of row.fields) {
                const li = d.createElement('li');
                li.textContent = field.label + ' (' + field.id + ')';
                list.appendChild(li);
            }
            fields.appendChild(list);
            tr.appendChild(fields);
            const read = d.createElement('td');
            read.textContent = row.read;
            tr.appendChild(read);
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        root.appendChild(table);
    }
    // #1030: rolling window of metrics-poll outcomes. The status pill is only
    // the latest sample; this keeps the last N so a flapping exporter shows up.
    const HEALTH_HISTORY_LIMIT = 20;
    function healthHistoryLimit(limit) {
        const n = Math.floor(Number(limit));
        return Number.isFinite(n) && n >= 1 ? n : HEALTH_HISTORY_LIMIT;
    }
    // Newest outcomes, capped at `limit`. Only strict `true` is a success.
    function pollWindow(history, limit) {
        const n = healthHistoryLimit(limit);
        const next = [];
        if (Array.isArray(history)) {
            for (const item of history) next.push(item === true);
        }
        if (next.length > n) next.splice(0, next.length - n);
        return next;
    }
    function pushPollOutcome(history, ok, limit) {
        const n = healthHistoryLimit(limit);
        const next = pollWindow(history, n);
        next.push(ok === true);
        if (next.length > n) next.splice(0, next.length - n);
        return next;
    }
    // Slots are oldest-left, newest-right, padded with 'empty' until `limit`.
    function healthHistoryView(history, limit) {
        const n = healthHistoryLimit(limit);
        const windowed = pollWindow(history, n);
        const slots = [];
        for (let i = windowed.length; i < n; i++) slots.push('empty');
        let okCount = 0;
        for (const item of windowed) {
            if (item) { slots.push('ok'); okCount += 1; }
            else slots.push('fail');
        }
        const failCount = windowed.length - okCount;
        let caption;
        if (windowed.length === 0) caption = 'No polls yet';
        else {
            const newest = windowed[windowed.length - 1] ? 'newest succeeded' : 'newest failed';
            caption = `${formatNumber(okCount, undefined, 0)}/${formatNumber(windowed.length, undefined, 0)} succeeded, ${formatNumber(failCount, undefined, 0)} failed \u00b7 ${newest}`;
        }
        const limitLabel = formatNumber(n, undefined, 0);
        const label = windowed.length === 0
            ? `Metrics poll history, last ${limitLabel} outcomes. No polls yet. Oldest on the left.`
            : `Metrics poll history, last ${limitLabel} outcomes. ${caption}. Oldest on the left.`;
        return { slots, ok: okCount, fail: failCount, total: windowed.length, limit: n, label, caption };
    }

    // #691: one ring-buffer cap for every client time-series (chart history and
    // the activity log). Chart.js may draw a shorter window; this bound does
    // not follow an axis or tick max, so a wider chart cannot grow memory.
    const HISTORY_MAX_POINTS = 50;
    function historyDebug() {
        return typeof process === 'undefined'
            || !process.env
            || process.env.NODE_ENV !== 'production';
    }
    // Debug builds (the static dashboard, and node tests) assert length ≤ cap.
    // NODE_ENV=production skips the assert; the trim itself still applies.
    function assertHistoryBound(length, max) {
        const cap = max == null ? HISTORY_MAX_POINTS : max;
        if (!historyDebug()) return true;
        const ok = Number(length) <= cap;
        if (typeof console !== 'undefined' && typeof console.assert === 'function') {
            console.assert(ok, `client history length ${length} exceeds cap ${cap}`);
        }
        if (!ok) throw new Error(`client history length ${length} exceeds cap ${cap}`);
        return true;
    }
    // drop 'start': oldest-first series (charts). drop 'end': newest-first logs.
    function boundHistoryBuffer(buffer, drop) {
        if (!Array.isArray(buffer)) return buffer;
        const cap = HISTORY_MAX_POINTS;
        const overflow = buffer.length - cap;
        if (overflow > 0) {
            if (drop === 'end') buffer.splice(cap, overflow);
            else buffer.splice(0, overflow);
        }
        assertHistoryBound(buffer.length, cap);
        return buffer;
    }
    function pushHistorySample(buffer, value) {
        if (!Array.isArray(buffer)) return buffer;
        buffer.push(value);
        return boundHistoryBuffer(buffer, 'start');
    }
    function enforceHistoryCap(store, drop) {
        if (!store || typeof store !== 'object') return store;
        for (const key of Object.keys(store)) {
            if (Array.isArray(store[key])) boundHistoryBuffer(store[key], drop);
        }
        return store;
    }
    // #1029: sessionStorage ring for the activity log. Newest first, same cap
    // the on-screen log already uses. Only type/message/timestamp are kept.
    const ACTIVITY_LOG_CAP = HISTORY_MAX_POINTS;
    const ACTIVITY_LOG_TEXT_MAX = 1000;
    const ACTIVITY_LOG_SCAN_MAX = 500;
    function activityLogCapSize(cap) {
        const n = Math.floor(Number(cap));
        return Number.isFinite(n) && n >= 1 ? n : ACTIVITY_LOG_CAP;
    }
    function activityLogItem(item) {
        if (!item || typeof item !== 'object') return null;
        const message = typeof item.message === 'string' ? item.message : '';
        if (!message.trim()) return null;
        const rawType = typeof item.type === 'string' ? item.type : '';
        const type = /^[a-z][a-z0-9-]{0,31}$/.test(rawType) ? rawType : 'info';
        const timestamp = typeof item.timestamp === 'string' ? item.timestamp.slice(0, 80) : '';
        return { type, message: message.slice(0, ACTIVITY_LOG_TEXT_MAX), timestamp };
    }
    function capActivityLog(items, cap) {
        const n = activityLogCapSize(cap);
        const list = Array.isArray(items) ? items : [];
        const out = [];
        const scan = Math.min(list.length, ACTIVITY_LOG_SCAN_MAX);
        for (let i = 0; i < scan && out.length < n; i++) {
            const row = activityLogItem(list[i]);
            if (row) out.push(row);
        }
        return out;
    }
    function activityStaleNote(count) {
        const n = Math.floor(Number(count));
        if (!Number.isFinite(n) || n <= 0) return null;
        if (n === 1) {
            return 'Restored 1 earlier entry from this tab. It is from before the reload and may be stale.';
        }
        return `Restored ${formatNumber(n, undefined, 0)} earlier entries from this tab. They are from before the reload and may be stale.`;
    }
    function restoreActivityLog(raw, cap) {
        let parsed = null;
        if (typeof raw === 'string' && raw.length > 0 && raw.length <= 200000) {
            try { parsed = JSON.parse(raw); } catch (_) { parsed = null; }
        }
        const items = capActivityLog(parsed, cap);
        return { items, staleNote: activityStaleNote(items.length) };
    }
    // #1052: hosts in play, ranked from a small set of labelled series or from
    // an API stub of the same shape. The configured `domain` label on crawl
    // counters is not a request host (see metric_labels.py) and is ignored.
    // Missing series, or only sentinel hosts (`unknown`, `rejected`, `-`),
    // are an empty state — never invented domains.
    const SCRAPE_TARGET_METRICS = {
        scrape_targets: 1,
        scrape_targets_total: 1,
        pipeline_scrape_targets: 1,
        pipeline_scrape_targets_total: 1,
        seed_hosts: 1,
        seed_hosts_total: 1,
        urls_by_host: 1,
        urls_by_host_total: 1,
        urls_by_domain: 1,
        urls_by_domain_total: 1,
    };
    const SCRAPE_TARGET_EMPTY = 'No scrape-target metric reported.';
    const SCRAPE_TARGET_SENTINELS = { '': 1, '-': 1, unknown: 1, rejected: 1 };

    function scrapeTargetCap(limit) {
        const n = Math.floor(Number(limit));
        if (!Number.isFinite(n) || n <= 0) return 8;
        return Math.min(n, 50);
    }
    function scrapeTargetHost(raw) {
        const host = String(raw == null ? '' : raw).replace(/\\(.)/g, '$1').trim();
        if (!host || host.length > 253 || SCRAPE_TARGET_SENTINELS[host.toLowerCase()]) return null;
        if (/[\u0000-\u001f\u007f]/.test(host)) return null;
        return host;
    }
    function scrapeTargetLabel(key, label) {
        const m = String(key).match(new RegExp('(?:^|[{,])' + label + '="((?:[^"\\\\]|\\\\.)*)"'));
        return m ? scrapeTargetHost(m[1]) : null;
    }
    function addScrapeTarget(totals, host, value) {
        const name = scrapeTargetHost(host);
        if (!name) return;
        const prev = totals.get(name) || { host: name, value: null };
        if (Number.isFinite(value) && value >= 0) {
            prev.value = (Number.isFinite(prev.value) ? prev.value : 0) + value;
        }
        totals.set(name, prev);
    }
    function rowsFromTargetItems(list) {
        const totals = new Map();
        for (const item of list || []) {
            if (typeof item === 'string') {
                addScrapeTarget(totals, item, null);
                continue;
            }
            if (!item || typeof item !== 'object') continue;
            const raw = item.host != null ? item.host : item.domain;
            const value = item.value != null ? item.value : item.count;
            const n = Number(value);
            addScrapeTarget(totals, raw, Number.isFinite(n) ? n : null);
        }
        return rankedScrapeTargets(totals);
    }
    function rowsFromTargetMetrics(metrics) {
        const totals = new Map();
        for (const key of Object.keys(metrics || {})) {
            const brace = key.indexOf('{');
            if (brace < 1) continue;
            if (!SCRAPE_TARGET_METRICS[key.slice(0, brace)]) continue;
            const host = scrapeTargetLabel(key, 'host') || scrapeTargetLabel(key, 'domain');
            const n = Number(metrics[key]);
            addScrapeTarget(totals, host, Number.isFinite(n) ? n : null);
        }
        return rankedScrapeTargets(totals);
    }
    function rankedScrapeTargets(totals) {
        return [...totals.values()].sort((a, b) => {
            const aNum = Number.isFinite(a.value);
            const bNum = Number.isFinite(b.value);
            if (aNum && bNum && a.value !== b.value) return b.value - a.value;
            if (aNum !== bNum) return aNum ? -1 : 1;
            return a.host < b.host ? -1 : a.host > b.host ? 1 : 0;
        });
    }
    function topScrapeTargets(input, limit) {
        const cap = scrapeTargetCap(limit);
        let rows;
        if (Array.isArray(input)) rows = rowsFromTargetItems(input);
        else if (input && typeof input === 'object' && (Array.isArray(input.targets) || Array.isArray(input.hosts))) {
            rows = rowsFromTargetItems(Array.isArray(input.targets) ? input.targets : input.hosts);
        } else rows = rowsFromTargetMetrics(input);
        if (!rows.length) return { missing: true, rows: [], empty: SCRAPE_TARGET_EMPTY };
        return { missing: false, rows: rows.slice(0, cap), empty: SCRAPE_TARGET_EMPTY };
    }
    // #1040: Grafana home for the System Health card. http(s) only, no userinfo,
    // query, or fragment. A /d/<uid> path is cut back to the base so a missing
    // dashboard JSON still opens Grafana home. Rejected values use fallback.
    const DEFAULT_GRAFANA_HOME = 'http://localhost:3000/';
    function grafanaHomeUrl(raw, fallback) {
        const fb = (typeof fallback === 'string' && fallback) ? fallback : DEFAULT_GRAFANA_HOME;
        if (typeof raw !== 'string') return fb;
        const text = raw.trim();
        if (!text || /[\s\\]/.test(text) || /[\u0000-\u001f]/.test(text)) return fb;
        let url;
        try { url = new URL(text); } catch (e) { return fb; }
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return fb;
        if (url.username || url.password || url.port === '0') return fb;
        const segments = url.pathname.split('/').filter(function (s) {
            return s && s !== '.' && s !== '..';
        });
        const dash = segments.indexOf('d');
        if (dash >= 0 && dash + 1 < segments.length) segments.splice(dash);
        url.pathname = segments.length ? '/' + segments.join('/') + '/' : '/';
        url.search = '';
        url.hash = '';
        return url.href;
    }
    // #1022: a confirmed job start is one check, never confetti. Reduced motion
    // skips the stroke draw and shows the finished check.
    function jobStartedPresentation(reducedMotion) {
        const reduced = !!reducedMotion;
        return {
            animate: !reduced,
            className: reduced ? 'is-static' : 'is-on',
            label: 'Job started',
            confetti: false
        };
    }
    // #1017: custom accent. Only #rrggbb is accepted so a stored value cannot
    // be written into a CSS variable as anything other than a color.
    const DEFAULT_ACCENT = '#2563eb';
    const ACCENT_INK_DARK = '#111827';
    const ACCENT_BORDER_BG = '#f9fafb'; // active tab bar (--gray-50)

    function parseAccentColor(value, fallback) {
        const fb = arguments.length > 1 ? fallback : DEFAULT_ACCENT;
        const s = String(value == null ? '' : value).trim();
        return /^#[0-9a-fA-F]{6}$/.test(s) ? s.toLowerCase() : fb;
    }

    function _hexRgb(hex) {
        const n = parseInt(hex.slice(1), 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    function _relLuminance(hex) {
        const lin = (c) => {
            const v = c / 255;
            return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        };
        const [r, g, b] = _hexRgb(hex);
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    }

    function _contrast(a, b) {
        const x = _relLuminance(a);
        const y = _relLuminance(b);
        const hi = x > y ? x : y;
        const lo = x > y ? y : x;
        return (hi + 0.05) / (lo + 0.05);
    }

    // Button label: white or near-black, whichever contrasts more with the fill.
    function accentInk(hex) {
        const color = parseAccentColor(hex);
        return _contrast(color, '#ffffff') >= _contrast(color, ACCENT_INK_DARK) ? '#ffffff' : ACCENT_INK_DARK;
    }

    // Active-tab underline. A pale accent is mixed toward black until it is
    // at least 3:1 against the tab bar, so the border stays visible.
    function accentBorder(hex, against) {
        const bg = parseAccentColor(against, ACCENT_BORDER_BG);
        let [r, g, b] = _hexRgb(parseAccentColor(hex));
        for (let i = 0; i < 24; i++) {
            const cur = '#' + [r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('');
            if (_contrast(cur, bg) >= 3) return cur;
            r = Math.round(r * 0.82);
            g = Math.round(g * 0.82);
            b = Math.round(b * 0.82);
        }
        return ACCENT_INK_DARK;
    }

    function accentCssVars(hex) {
        const color = parseAccentColor(hex);
        return {
            '--accent': color,
            '--accent-ink': accentInk(color),
            '--accent-border': accentBorder(color),
        };
    }

    return { tabKeyTarget, staleState, queueDepthRows, metricsFailureActivity, shortcutHintText, parseMetrics, metricSeriesCount, METRICS_FLOOD_DEFAULTS, metricsFloodLimits, advanceMetricsFlood, metricsFloodBannerText, ratePerMinute, resolveMetricsUrl, dashboardLocale, UNIT_STRINGS, formatNumber, formatBytes, formatTime, formatDate, formatEpochTime, formatRate, formatCompression, formatUptime, formatRelative, formatHistoryStamp, countLabelValues, createRefreshScheduler, stageActivity, stageDrilldown, stageDrilldownKey, doughnutLegendLayout, connectionState, documentTitle, createActivityAnnouncer, metricsUrlProblem, splitLinks, createMetricsFetcher, REFRESH_INTERVAL_CHOICES, parseRefreshInterval, shortcutAction, pickInitialTab, METRIC_UI_MAP, renderMetricNameMap, parseDangerSoundEnabled, dangerSoundDecision, HEALTH_HISTORY_LIMIT, pushPollOutcome, healthHistoryView, COMPARISON_BASELINE_ROLE, PREV_HOUR_AVG_KEYS, readPrevHourAverage, comparisonBaselineSeries, comparisonNote, ACTIVITY_LOG_CAP, capActivityLog, activityStaleNote, restoreActivityLog, topScrapeTargets, SCRAPE_TARGET_EMPTY, ACTIVITY_LEVELS, activityLevelCounts, filterActivityItems, filterActivityMessages, activityFilterStatus, toggleActivityLevel, HISTORY_MAX_POINTS, assertHistoryBound, boundHistoryBuffer, pushHistorySample, enforceHistoryCap, grafanaHomeUrl, jobStartedPresentation, DEFAULT_ACCENT, parseAccentColor, accentInk, accentBorder, accentCssVars };
});
