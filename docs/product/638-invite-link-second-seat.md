# Invite link for a second operator seat

- **Status:** Stub. Design only. No route, handler, token store, table, config key, or dashboard change is added here.
- **Date:** 2026-10-15
- **Issue:** [#638](https://github.com/BenjaminSRussell/Scrapy/issues/638)

This file does not change runtime behavior. It turns the three acceptance criteria in #638 into rules a later implementation must meet, and it lists the choices that are still open.

The growth path is a team plan: a second person watches and operates the Control Center under their own account, not under the one shared credential that exists today.

## What exists today

- The only auth is `AuthGate` in [serve.py](../../Scraping_project/dashboard/serve.py) (line 141). It is off unless `CC_AUTH_TOKEN` or `CC_BASIC_AUTH` is set, and each setting is a single shared credential. Every caller who presents it is the same caller: there is no user name to tell two operators apart, no account record, and no operator role. Its docstring calls it "a shared-secret gate for LAN demos, not an identity provider" (lines 147-148). The [dashboard README](../../Scraping_project/dashboard/README.md) documents both settings (lines 82-83).
- The dashboard server has no route that changes state. `do_OPTIONS`, `do_HEAD`, and `do_GET` are its only handlers (`serve.py` lines 425, 444, 449), and the `/api/` routes are GET routes (`API_ROUTES`, line 423). So today an operator role could only gate reads of the Control Center.
- The tree has no invite, seat, or multi-user code. Before this stub, a search of the tree found no file containing `invite`, `seat`, or `multi-user`. The issue lists a "multi-user stub" as existing code. The nearest thing is `AuthGate`, which is why it is the starting point above.
- [federated-tenant-gold-query.md](federated-tenant-gold-query.md) (#770) already defines principals, per-tenant grants, and roles. This stub follows that model and changes none of its rules.

## Scope

The issue asks for one more operator account, created from an invite link. An operator is a person who watches or runs the pipeline through the Control Center. "Seat" means that one account. It does not mean a price, a plan tier, or a seat count. The seat count is a later decision (see Open decisions).

`operator` is the only role in this stub. It is not an admin role, and it carries no tenant grant.

## Acceptance criteria as rules

### 1. Generate an invite token

- An existing operator issues the invite. The server creates the token. The issuer does not choose its value.
- The token comes from a cryptographically secure random source with at least 256 bits of entropy. It is a bearer secret: anyone who holds it can claim the seat until it is used or expires.
- The server stores the invite id, a SHA-256 digest of the token, the issuing account, `created_at`, and `expires_at`. It does not store the token. A plain digest is enough because the token is random, not a human-chosen password.
- The issuer sees the token once. The token is never written to logs, metric labels, or the audit record. Audit records use the invite id.
- The token carries no claims. The role, the issuer, and the expiry come from the stored invite, never from the token.
- The link carries the token in the URL fragment, not the query string, so a reverse proxy or access log does not record it. The page reads the fragment and sends the token in the body of the accept request.

### 2. Accepting creates an operator user

- Accepting takes the token and a credential the invitee sets for themselves. The server does not generate a password and does not send one. The credential type is an open decision.
- A successful accept creates one user with `role = operator`, links that user to the invite, and marks the invite used, in one step. The accept request has no role field. The server sets the role.
- An invite is single use. The first successful accept consumes it. A later accept with the same token is refused.
- An accept made when the deployment has no open operator seat fails and creates nothing.
- An unknown, expired, or used token gets one generic refusal that does not say which of the three it was. This matches the no-existence-oracle rule in the [#770 stub](federated-tenant-gold-query.md) (rule 6).
- Accepting grants no tenant access. Any tenant grant is a separate decision under the grant rules in the #770 stub.

### 3. Expires in 72 hours

- `expires_at` is `created_at` plus 72 hours (259,200 seconds). Both are stored in UTC when the invite is created.
- Accept compares the server clock with `expires_at` at the moment of the request. The invitee's clock and time zone do not count.
- An expired invite is refused like an unknown one. No background job is needed for the refusal. A later cleanup job may delete expired rows; deleting a row is not what ends the invite.
- An expired invite cannot be extended. A new invite needs a new token.

## Open decisions

None of these is decided here. Each needs a decision before an implementation.

1. **First operator account.** No accounts exist, and issuing an invite needs an issuing account. The first named account needs a bootstrap path: a config key, a CLI command, or an external identity provider. This stub picks none.
2. **Credential type.** Password, passkey, or an external identity provider. `AuthGate` is none of these. The shared credential stays for existing deployments until a later change replaces it.
3. **Storage.** Accounts and invites must survive a restart. They stay out of the Delta lake, which holds crawl data. The pattern in [seed_ops.py](../../Scraping_project/src/common/seed_ops.py) (a JSON registry with an append-only audit log) is one option. A database table is another.
4. **Seat count.** How many operator seats a deployment has, and where that number is set.
5. **Revocation.** This stub has no way to cancel an unused invite before it expires. It lapses within 72 hours. Removing an operator is not covered.
6. **Enforcement point.** Nothing checks a role today. Whether the check runs in the dashboard server, an authenticating proxy, or the store is for the implementation.

## Out of scope

- Any route, handler, token store, table, migration, config key, Helm value, CLI command, or dashboard change. `dashboard/serve.py` and `config.yml` are not touched by this stub.
- Delivering the link. The issuer passes it to the invitee by another channel. The app sends no email or chat message.
- SSO, OIDC, SAML, and MFA.
- Team plans, billing, and seat pricing. The issue names the plan as a growth path only.
- Changing `AuthGate` or the shared credential that existing deployments use.

## Related

- [#638](https://github.com/BenjaminSRussell/Scrapy/issues/638): this stub.
- [federated-tenant-gold-query.md](federated-tenant-gold-query.md) (#770): principals, per-tenant grants, and roles.
- [dashboard/README.md](../../Scraping_project/dashboard/README.md): the current shared-credential settings.
