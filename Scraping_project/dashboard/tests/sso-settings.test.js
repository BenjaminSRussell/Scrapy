// SSO tab: OIDC fields are on the settings screen and login stays disabled (#673).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

test('SSO tab exposes OIDC fields and a disabled enabled switch', () => {
    assert.match(body, /data-tab="sso"/);
    assert.match(body, /id="tab-sso" role="tabpanel" aria-labelledby="tabbtn-sso"/);
    for (const id of ['oidc-issuer', 'oidc-client-id', 'oidc-client-secret', 'oidc-redirect-uri', 'oidc-scopes']) {
        assert.match(body, new RegExp(`<label for="${id}">`));
        assert.match(body, new RegExp(`id="${id}"`));
    }
    assert.match(body, /id="oidc-client-secret"[^>]*type="password"/);
    assert.match(body, /id="oidc-enabled"[^>]*disabled/);
    assert.match(body, /id="oidc-save"/);
    assert.match(body, /id="oidc-login"/);
    assert.match(body, /not_implemented/);
});

test('app saves OIDC config and reads the login path without innerHTML', () => {
    const start = app.indexOf('function oidcField');
    assert.ok(start >= 0);
    const slice = app.slice(start, app.indexOf('function initialize()'));
    assert.match(slice, /\/api\/sso\/oidc\/login/);
    assert.match(slice, /method: 'POST'/);
    assert.match(slice, /enabled: false/);
    assert.match(slice, /textContent/);
    assert.ok(!slice.includes('innerHTML'));
    assert.match(app, /setupSsoSettings\(\)/);
});
