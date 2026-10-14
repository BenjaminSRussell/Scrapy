// Pure helpers shared by app.js and node tests (#1043).
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    // Prometheus text exposition: `name{l1="v",l2="w"} value [timestamp_ms]`.
    // Label values may contain spaces, commas and escaped quotes.
    const SERIES_RE = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(?:[^"}]|"(?:[^"\\]|\\.)*")*\})?\s+(\S+)(?:\s+-?\d+)?\s*$/;
    // #665: bare names the Control Center reads. Anything else in a /metrics
    // flood is ignored. Documented in dashboard/README.md.
    const CONTROL_CENTER_METRICS = Object.freeze([
        'stage1_urls_discovered_total',
        'stage1_urls_queued_total',
        'stage2_pages_analyzed_total',
        'stage2_quality_docs_total',
        'stage2_massive_docs_total',
        'stage2_avg_word_count',
        'stage3_summaries_created_total',
        'stage3_documents_deduplicated_total',
        'stage4_large_doc_summaries_total',
        'stage4_avg_compression_ratio',
        'pipeline_redis_keys',
        'pipeline_redis_memory_bytes',
        'pipeline_last_update_timestamp',
        'pipeline_scout_instances',
        'pipeline_running',
        'delta_lake_records',
        'delta_lake_records_total',
        // #1035: optional previous-hour gauges. Absent series stay placeholders.
        'stage1_urls_discovered_prev_hour_avg',
        'stage2_pages_analyzed_prev_hour_avg',
        'stage3_summaries_created_prev_hour_avg',
        'stage1_urls_per_min_prev_hour_avg',
        'stage2_pages_per_min_prev_hour_avg',
    ]);
    // Only these names keep individual labelled keys (distinct `table` values).
    // Every other allowlisted name keeps a running sum, so a per-domain flood
    // cannot grow the map the DOM walks.
    const CC_LABEL_METRICS = Object.freeze([
        'delta_lake_records',
        'delta_lake_records_total',
    ]);
    // Per label-retained name. Further series of that name still add to the sum.
    const CC_METRICS_SERIES_CAP = 128;
    const CC_METRIC_ALLOW = new Set(CONTROL_CENTER_METRICS);
    const CC_LABEL_KEEP = new Set(CC_LABEL_METRICS);
    function isNameStart(c) {
        return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95 || c === 58;
    }
    function isNameCont(c) {
        return isNameStart(c) || (c >= 48 && c <= 57);
    }
    function metricName(line) {
        const n = line.length;
        if (n === 0 || !isNameStart(line.charCodeAt(0))) return '';
        let i = 1;
        while (i < n && isNameCont(line.charCodeAt(i))) i++;
        return line.slice(0, i);
    }
    function allowSetFrom(allow) {
        if (!allow) return null;
        if (allow === CONTROL_CENTER_METRICS) return CC_METRIC_ALLOW;
        if (allow instanceof Set) return allow;
        if (Array.isArray(allow)) return new Set(allow);
        return null;
    }
    // #365: labelled series are kept under their full key (e.g. `x{a="b"}`, used
    // by countLabelValues) AND summed under the bare metric name, unless the
    // exporter also emits an unlabelled series of that name, which then wins.
    // So `errors_total{stage="s1"} 3` + `errors_total{stage="s2"} 4` gives
    // metrics.errors_total === 7 for the flat names updateDashboard reads.
    // #665: pass `{ allow, maxSeries }` to drop every other name and cap retained
    // label keys. Unknown or malformed lines are skipped; they never throw.
    // With no options the historical "keep every series" behaviour stays.
    function parseMetrics(text, opts) {
        let body;
        try {
            body = typeof text === 'string' ? text : (text == null ? '' : String(text));
        } catch (_) {
            return {};
        }
        const allow = allowSetFrom(opts && opts.allow);
        let cap = CC_METRICS_SERIES_CAP;
        if (opts && opts.maxSeries != null && opts.maxSeries !== '') {
            const n = Number(opts.maxSeries);
            if (Number.isFinite(n) && n >= 0) cap = Math.floor(n);
        }
        const metrics = {};
        const sums = Object.create(null);
        const unlabelled = new Set();
        const perName = Object.create(null);
        const lines = body.split('\n');
        for (let li = 0; li < lines.length; li++) {
            let line = lines[li];
            if (line.length === 0) continue;
            let c0 = line.charCodeAt(0);
            if (c0 === 35) continue; // '#'
            if (c0 === 32 || c0 === 9 || c0 === 13) {
                line = line.trim();
                if (line.length === 0) continue;
                c0 = line.charCodeAt(0);
                if (c0 === 35) continue;
            }
            const name = metricName(line);
            if (!name) continue;
            if (allow && !allow.has(name)) continue;
            let matched;
            try {
                matched = line.match(SERIES_RE);
            } catch (_) {
                continue;
            }
            if (!matched) continue;
            const value = parseFloat(matched[3]);
            if (isNaN(value)) continue;
            const labels = matched[2] || '';
            if (!allow) {
                metrics[name + labels] = value;
            } else if (!labels) {
                metrics[name] = value;
            } else if (CC_LABEL_KEEP.has(name)) {
                const seen = perName[name] || 0;
                if (seen < cap) {
                    metrics[name + labels] = value;
                    perName[name] = seen + 1;
                }
            }
            if (labels) sums[name] = (sums[name] || 0) + value;
            else unlabelled.add(name);
        }
        for (const name of Object.keys(sums)) {
            if (!unlabelled.has(name)) metrics[name] = sums[name];
        }
        return metrics;
    }
    // #665: at most one dashboard paint per frame. Later calls before the frame
    // replace the pending callback so a burst cannot stack DOM writes.
    function createFrameCoalescer(requestFrame, scheduleTimeout) {
        const raf = typeof requestFrame === 'function' ? requestFrame : null;
        const later = scheduleTimeout || ((fn, ms) => setTimeout(fn, ms));
        let queued = false;
        let pending = null;
        function schedule(fn) {
            pending = fn;
            if (queued) return;
            queued = true;
            const run = () => {
                queued = false;
                const cb = pending;
                pending = null;
                if (typeof cb === 'function') cb();
            };
            if (raf) raf(run);
            else later(run, 16);
        }
        // A failed fetch must not let an older success paint over the error.
        schedule.cancel = function () { pending = null; };
        return schedule;
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
            caption = `${okCount}/${windowed.length} succeeded, ${failCount} failed \u00b7 ${newest}`;
        }
        const label = windowed.length === 0
            ? `Metrics poll history, last ${n} outcomes. No polls yet. Oldest on the left.`
            : `Metrics poll history, last ${n} outcomes. ${caption}. Oldest on the left.`;
        return { slots, ok: okCount, fail: failCount, total: windowed.length, limit: n, label, caption };
    }
    // #1029: sessionStorage ring for the activity log. Newest first, same cap
    // the on-screen log already uses. Only type/message/timestamp are kept.
    const ACTIVITY_LOG_CAP = 50;
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
        return `Restored ${n} earlier entries from this tab. They are from before the reload and may be stale.`;
    }
    function restoreActivityLog(raw, cap) {
        let parsed = null;
        if (typeof raw === 'string' && raw.length > 0 && raw.length <= 200000) {
            try { parsed = JSON.parse(raw); } catch (_) { parsed = null; }
        }
        const items = capActivityLog(parsed, cap);
        return { items, staleNote: activityStaleNote(items.length) };
    }
    return { tabKeyTarget, staleState, queueDepthRows, metricsFailureActivity, shortcutHintText, parseMetrics, CONTROL_CENTER_METRICS, CC_LABEL_METRICS, CC_METRICS_SERIES_CAP, createFrameCoalescer, ratePerMinute, resolveMetricsUrl, formatNumber, formatBytes, formatEpochTime, countLabelValues, createRefreshScheduler, stageActivity, stageDrilldown, stageDrilldownKey, doughnutLegendLayout, connectionState, documentTitle, createActivityAnnouncer, metricsUrlProblem, splitLinks, createMetricsFetcher, REFRESH_INTERVAL_CHOICES, parseRefreshInterval, shortcutAction, pickInitialTab, METRIC_UI_MAP, renderMetricNameMap, parseDangerSoundEnabled, dangerSoundDecision, HEALTH_HISTORY_LIMIT, pushPollOutcome, healthHistoryView, COMPARISON_BASELINE_ROLE, PREV_HOUR_AVG_KEYS, readPrevHourAverage, comparisonBaselineSeries, comparisonNote, ACTIVITY_LOG_CAP, capActivityLog, activityStaleNote, restoreActivityLog };
});
