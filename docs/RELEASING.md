# Releasing

Release images are built, smoke-tested and pushed by
[`.github/workflows/cd-release.yml`](../.github/workflows/cd-release.yml) when a `v*`
tag is pushed. A tag is the only thing that publishes an image; merging to `main` does not.

## Versioning policy

The project follows [Semantic Versioning 2.0.0](https://semver.org/). The version
lives in `Scraping_project/pyproject.toml` (`[project] version`), and tags are that
version prefixed with `v` (`v0.2.0`).

| Change | Bump |
|--------|------|
| Incompatible change to something operators or consumers depend on: Delta table schema (column removed, renamed or retyped), a `docs/contracts` MAJOR bump, Kafka message fields or topic names, `config.yml` key removed or renamed, CLI flag removed, Helm values renamed | **MAJOR** |
| Backwards-compatible feature: new optional config key, new table column, new CLI command or metric | **MINOR** |
| Bug fix, performance or docs change with no interface change | **PATCH** |

While the version is `0.y.z`, breaking changes bump **MINOR** and everything else
bumps **PATCH**, as SemVer allows for initial development. Breaking changes always get
a `### Changed` or `### Removed` entry in [CHANGELOG.md](../CHANGELOG.md) that says
what operators must do.

Per-table JSON Schemas and their own version bumps are in
[docs/contracts](contracts/README.md). A contract MAJOR is a breaking change in
the table above.

## Release checklist

1. Make sure `main` is green (CI/CD Pipeline, CI Dashboard, CI Kafka Alerts).
2. In [CHANGELOG.md](../CHANGELOG.md), rename `## [Unreleased]` to
   `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty `## [Unreleased]` above it, and update
   the compare links at the bottom.
3. Set `version = "X.Y.Z"` in `Scraping_project/pyproject.toml`.
4. Open a PR titled `Release vX.Y.Z` with both changes and merge it once it is green.
5. Tag the merge commit and push the tag:
   ```bash
   git switch main && git pull
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```
6. Watch the `CD - Build and Push Images` run for the tag. The sign, verify, and
   SBOM steps must run. If they are skipped, `GHCR_PAT` was empty and no image
   was published.
7. Create the GitHub release from the tag and paste the version's CHANGELOG section as
   the notes. **Generate release notes** groups merged PRs by label using
   [`.github/release.yml`](../.github/release.yml) and can be appended under it.
   Download the `sbom-crawler`, `sbom-metrics`, and `sbom-kafka-delta-ingest`
   artifacts from that run and attach them to the release. The workflow artifact
   expires with the run; the signature does not.

## Image signature and SBOM

A tag push with `GHCR_PAT` set publishes `crawler`, `metrics`, and
`kafka-delta-ingest`. For each image the same job then:

1. Signs the pushed manifest digest with keyless
   [cosign](https://docs.sigstore.dev/cosign/overview/) (GitHub Actions OIDC).
   There is no cosign private key and no signing secret in the workflow.
   `GHCR_PAT` only lets the job push to GHCR. It is not the signer.
2. Verifies that signature before the job can succeed.
3. Writes an SPDX JSON SBOM of that same digest with Syft
   (`anchore/sbom-action`) and uploads it as the Actions artifact
   `sbom-<image>`.

The signature is an image signature recorded on the digest in GHCR and in the
public Rekor log. It is not a SLSA provenance statement, and no SLSA level is
claimed. The gap list stays in
[security/slsa-provenance.md](security/slsa-provenance.md).
[ADR-0004](adr/0004-keyless-cosign-for-release-images.md) records why the
signer is keyless.

Pull requests and `workflow_dispatch` do not push, sign, or upload an SBOM.

Verify the digest you intend to run, not a floating tag. Replace the image
name, the tag, and the digest (the push step logs `sha256:...`):

```bash
cosign verify \
  "ghcr.io/benjaminsrussell/scrapy/crawler@sha256:DIGEST" \
  --certificate-identity "https://github.com/BenjaminSRussell/Scrapy/.github/workflows/cd-release.yml@refs/tags/vX.Y.Z" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

The same identity covers `metrics` and `kafka-delta-ingest`. The image name
and digest pick which artifact you are checking. GHCR paths are lower case, so
the image name is too. The identity keeps the repository's own case.

To accept any `v*` tag built by that workflow file on this repository, replace
`--certificate-identity` with:

```bash
--certificate-identity-regexp '^https://github\.com/BenjaminSRussell/Scrapy/\.github/workflows/cd-release\.yml@refs/tags/v.+'
```

Do not accept a signature whose identity is a pull request, a branch, a
`workflow_dispatch` run, or any other repository. After verify, confirm the
tag still resolves to that digest. A moved tag was not what was signed.
