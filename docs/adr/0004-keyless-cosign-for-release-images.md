# ADR-0004: Release images are signed with keyless cosign

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #689

## Context

[`cd-release.yml`](../../.github/workflows/cd-release.yml) can publish three
images (`crawler`, `metrics`, `kafka-delta-ingest`) when a `v*` tag is pushed
and `GHCR_PAT` is set. Consumers had an SPDX SBOM of a tag, which can move,
and no signature.

[SLSA provenance](../security/slsa-provenance.md) is a separate gap. No SLSA
level is claimed, and this decision does not close that gap. A long-lived
cosign key in the job would be the wrong signer for that future statement:
the workflow could use the key on bytes the platform did not produce.

The repository is public. A keyless certificate from the public Sigstore
instance puts the image digest and the workflow identity in the public Rekor
log. It does not put registry credentials or image contents there.

## Decision

We will sign each pushed release-image digest with keyless cosign, using the
GitHub Actions OIDC token for this workflow file. The job must not hold a
cosign private key, a key password, or any other signing secret. `GHCR_PAT`
stays the registry credential only.

The same job verifies the signature against that workflow identity and the
issuer `https://token.actions.githubusercontent.com` before it succeeds. Syft
writes an SPDX JSON SBOM of the same digest and the job uploads it as the
Actions artifact `sbom-<image>`.

Pull requests do not push, sign, or upload that artifact. How to verify is
in [RELEASING.md](../RELEASING.md).

## Consequences

- Operators can reject an image whose digest was not signed by a `v*` tag run
  of `.github/workflows/cd-release.yml` on this repository.
- The SBOM names a digest, not a tag. The Actions artifact still expires with
  the workflow run. Attaching it to the GitHub release is a manual checklist
  step. The signature remains on the digest in GHCR.
- Signing runs only when the image is actually pushed. An empty `GHCR_PAT`
  still publishes nothing.
- This is not SLSA provenance. The tenant workflow chooses what `cosign sign`
  covers. A later provenance statement still has to be produced by the
  platform, for one digest, as the SLSA note describes.
- Revisit this if the registry cannot store the signature, if Sigstore's
  public instance is no longer acceptable for this repo, or if a platform
  attestation replaces the need for a separate image signature.

## Alternatives considered

- **A cosign key in Actions secrets:** rejected. The key would be available
  to the build steps, which is the opposite of an unforgeable signature, and
  it adds a secret to rotate.
- **GitHub artifact attestations (`actions/attest`):** not this change. That
  path needs `attestations: write` and is the candidate for SLSA provenance,
  which is still an open gap. It is not required to sign the image digest.
- **Signing the git tag instead of the digest:** rejected. A tag can move.
  The digest is the bytes consumers pull.
