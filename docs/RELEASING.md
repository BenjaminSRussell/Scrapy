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
| Incompatible change to something operators or consumers depend on: Delta table schema (column removed, renamed or retyped), Kafka message fields or topic names, `config.yml` key removed or renamed, CLI flag removed, Helm values renamed | **MAJOR** |
| Backwards-compatible feature: new optional config key, new table column, new CLI command or metric | **MINOR** |
| Bug fix, performance or docs change with no interface change | **PATCH** |

While the version is `0.y.z`, breaking changes bump **MINOR** and everything else
bumps **PATCH**, as SemVer allows for initial development. Breaking changes always get
a `### Changed` or `### Removed` entry in [CHANGELOG.md](../CHANGELOG.md) that says
what operators must do.

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
6. Watch the `CD - Build and Push Images` run for the tag.
7. Create the GitHub release from the tag and paste the version's CHANGELOG section as
   the notes. **Generate release notes** groups merged PRs by label using
   [`.github/release.yml`](../.github/release.yml) and can be appended under it.
