# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/BenjaminSRussell/Scrapy/security/advisories/new)
(repository **Security** tab → **Report a vulnerability**). Only the maintainer
([@BenjaminSRussell](https://github.com/BenjaminSRussell)) can see the report.

If that button is not available, open an issue titled **"Security contact request"**
that contains **no details** of the problem. The maintainer will reply with a private
channel. Send the details there.

Please include:

- what is affected (file, endpoint, workflow, image, or Helm chart) and the commit or tag;
- steps to reproduce or a proof of concept;
- the impact you expect (data exposure, SSRF, code execution, denial of service, ...).

What to expect:

| Step | Target |
|------|--------|
| Acknowledgement | within 3 business days |
| Initial assessment (accepted / needs info / not a vulnerability) | within 10 business days |
| Fix or mitigation for accepted reports | as soon as practical; tracked in a private advisory |

You will be credited in the advisory and the [CHANGELOG](CHANGELOG.md) unless you ask not to be.

## Supported versions

The project has not cut a stable release yet (`pyproject.toml` version `0.1.0`).
Security fixes land on `main` only; release images are rebuilt from `v*` tags
(see [docs/RELEASING.md](docs/RELEASING.md)).

| Version | Supported |
|---------|-----------|
| `main` | yes |
| any earlier commit or tag | no, upgrade to `main` |

## Scope

In scope: code under `Scraping_project/`, the container images built by
`.github/workflows/cd-release.yml`, the Helm chart and Kubernetes manifests under
`Scraping_project/k8s/`, and the GitHub workflows.

Out of scope: `temp_scripts/` (unsupported one-off experiments), third-party
services (Kafka, Redis, Postgres, Grafana) beyond how this project configures them,
and findings that need an already-compromised host or cluster-admin access.
