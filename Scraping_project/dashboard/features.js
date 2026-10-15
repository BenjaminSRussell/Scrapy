// UI feature flags (hints only, not authorization). Kept out of index.html so the
// page needs no inline <script> under serve.py's enforcing CSP (#245).
// jobs defaults on so the empty Jobs panel is in the IA (#996). Set jobs:false
// to hide that tab again (#1047). Flags are UI hints, not authorization.
window.__CC_FEATURES__ = window.__CC_FEATURES__ || {
    jobs: true,
    dark: true,
    sw: false,
    env: (window.__CC_ENV__ || 'local')
};
