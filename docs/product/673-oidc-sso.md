# OIDC SSO placeholder (disabled)

- **Status:** Placeholder. Login is not implemented.
- **Date:** 2026-10-14
- **Issue:** [#673](https://github.com/BenjaminSRussell/Scrapy/issues/673)

Control Center can store OIDC fields. It cannot sign anyone in. The shared-secret
gate (`CC_AUTH_TOKEN` / `CC_BASIC_AUTH`) is unchanged. This screen does not
replace an authenticating proxy in front of the dashboard.

## What ships today

The SSO tab in [`dashboard/index.html`](../../Scraping_project/dashboard/index.html)
has issuer URL, client id, client secret, redirect URI, and scopes. The enabled
checkbox is disabled. Save posts JSON to `POST /api/sso/oidc`. A blank secret
leaves a previously saved secret in place. Sending `client_secret: ""` clears it.

`GET /api/sso/oidc` returns the saved fields, `enabled: false`,
`client_secret_set`, and `login: "not_implemented"`. It never returns the secret.

`GET` and `POST /api/sso/oidc/login` respond `501` with:

```json
{"ok": false, "status": "not_implemented", "error": "not_implemented", "enabled": false}
```

`oidc_login()` in [`oidc_placeholder.py`](../../Scraping_project/src/utils/oidc_placeholder.py)
returns the string `not_implemented`. It does not open a socket. Saved issuer and
client values are ignored. `enabled: true` in the request, in the overlay file,
or in `config.yml` is stored and reported as false.

Shipped defaults live under `oidc:` in [`config.yml`](../../Scraping_project/config.yml).
Do not put `client_secret` there. A save writes `CC_OIDC_CONFIG_PATH` (default
`Scraping_project/dashboard/.oidc-sso.json`, mode `0600`, gitignored, not served
because it is a dotfile). An empty `CC_OIDC_CONFIG_PATH` keeps the document in
memory for that process only. Workers do not read `oidc:`.

The settings routes sit behind the existing auth gate. The login path does not,
so an unauthenticated client can see that SSO is off. The login response does
not include the issuer or client id.

## Future enablement

Do not turn SSO on by editing YAML or the overlay. A later change has to do all
of the following before `oidc_login` may return anything other than
`not_implemented`:

1. Implement the authorization-code flow with PKCE. Discover the provider at
   `{issuer}/.well-known/openid-configuration`. Use the saved redirect URI.
   This placeholder does not choose a library or add a dependency.
2. Replace `oidc_login()` in the same change that stops returning
   `not_implemented`. Until that lands, `enabled` stays false even if a file
   says true.
3. Keep `client_secret` on the server (the overlay file or a secret store).
   Do not return it from `GET /api/sso/oidc`, do not write it into `config.yml`,
   and do not log it or copy it into the activity log.
4. Validate `state` and `nonce` on the callback. Do not trust the browser with
   the secret. Prefer `https` issuers and redirect URIs outside local demos.
5. After a code exchange, a session cookie is a new decision. It does not
   replace `CC_AUTH_TOKEN` / `CC_BASIC_AUTH` until that decision is documented.
   Kubernetes and Compose still expect an authenticating proxy for anything
   beyond a trusted LAN. This tab is not that proxy.
6. No identity-provider call belongs in the default test suite. A test may
   only assert that login still returns `not_implemented` until the
   replacement above exists.
7. Do not add a fourth pipeline SLO for login. The three shipped objectives
   stay Stage 2 success, `QueueBacklog` above 10000, and Kafka consumer lag
   above 250000.

## Out of scope

No IdP redirect, no token validation, no new Python dependency, no change to
Redis, Kafka, cost caps, or the three SLOs.
