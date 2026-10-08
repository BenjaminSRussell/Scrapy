// UI feature flags (hints only, not authorization). Kept out of index.html so the
// page needs no inline <script> under serve.py's enforcing CSP (#245).
window.__CC_FEATURES__ = window.__CC_FEATURES__ || {
    jobs: false,
    dark: true,
    sw: false,
    env: (window.__CC_ENV__ || 'local')
};
