// UI feature flags (hints only, not authorization). Kept out of index.html so the
// page needs no inline <script> under serve.py's enforcing CSP (#245).
window.__CC_FEATURES__ = window.__CC_FEATURES__ || {
    jobs: false,
    dark: true,
    // #1005: cache the shell (html/css/js) for an offline hard refresh.
    // Metrics are never cached. Set false to skip service-worker registration.
    sw: true,
    env: (window.__CC_ENV__ || 'local')
};
