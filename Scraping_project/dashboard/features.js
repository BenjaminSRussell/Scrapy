// UI feature flags (hints only, not authorization). Kept out of index.html so the
// page needs no inline <script> under serve.py's enforcing CSP (#245).
window.__CC_FEATURES__ = window.__CC_FEATURES__ || {
    // Job API (#147). Start-crawl Submit stays disabled until this is true (#999).
    jobs: false,
    dark: true,
    sw: false,
    env: (window.__CC_ENV__ || 'local')
};
