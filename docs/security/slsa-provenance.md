# SLSA provenance for release images

Design note for the release-image supply chain. It sets a target SLSA build
level and lists the gaps between that target and the CD workflows that exist
today.

**No SLSA level is claimed.** Not Build L3, not Build L2, not Build L1.
Nothing in this file is a statement that a level is already met. Checkboxes
below are open gaps. A box stays open until the workflow changes and a
consumer can verify a real image digest. Closing a box is not itself a level.

The spec used here is [SLSA v1.2](https://slsa.dev/spec/v1.2/build-track-basics)
(Build track). Requirements are in
[Producing artifacts](https://slsa.dev/spec/v1.2/build-requirements). How
attestations relate to artifacts is in
[Distributing provenance](https://slsa.dev/spec/v1.2/distributing-provenance).
The recommended predicate is
[SLSA Provenance](https://slsa.dev/spec/v1.2/provenance)
(`https://slsa.dev/provenance/v1`).

## Target

**SLSA v1.2 Build Level 3** for the three container images that
[`cd-release.yml`](../../.github/workflows/cd-release.yml) is able to publish.

| Image | Dockerfile target | Registry name |
| --- | --- | --- |
| `crawler` | `crawler` | `ghcr.io/benjaminsrussell/scrapy/crawler` |
| `metrics` | `metrics` | `ghcr.io/benjaminsrussell/scrapy/metrics` |
| `kafka-delta-ingest` | `kafka-delta-ingest` | `ghcr.io/benjaminsrussell/scrapy/kafka-delta-ingest` |

Targets are defined in [`Scraping_project/Dockerfile`](../../Scraping_project/Dockerfile).
The workflow's matrix is the list above, not the other Dockerfile stages
(`base`, `production`, `core`, `stage3`, `ml`, `stage4`).

Build L3 is the level the spec points at for most software releases: a hosted
platform generates provenance, signs it in its control plane, and isolates
builds so tenants cannot forge that statement. Build L1 (provenance exists)
and Build L2 (hosted platform, signed provenance) are prerequisites. They are
not an intermediate claim for this repo. The target is L3, and the checklist
includes every unmet prerequisite.

In scope for the target:

- Images pushed by a `v*` tag run of `cd-release.yml`, identified by manifest
  digest, not by a moving tag.
- A signed SLSA Provenance v1 attestation for each of those digests, produced
  by the build platform rather than by a key stored in the job.
- Distribution of that attestation to image consumers, bound to the digest.
- Verifier expectations: this repository, the workflow file, the builder
  identity, and the three image names.

Not in the target (see the checklist so they are not mistaken for it):

- The wheel copied by the `deploy` job in
  [`main.yml`](../../.github/workflows/main.yml).
- Helm charts under [`Scraping_project/k8s/`](../../Scraping_project/k8s/README.md).
- Dockerfile stages the release workflow does not push.
- The [Source track](https://slsa.dev/spec/v1.2/source-requirements). v1.2
  defines one. This note does not score it.
- Hermetic or bit-for-bit reproducible builds. v1.2 Build L3 does not require
  them. See
  [future directions](https://slsa.dev/spec/v1.2/future-directions).

## What CD does today

Facts from the workflow files. None of these is a SLSA level.

### [`cd-release.yml`](../../.github/workflows/cd-release.yml)

`CD - Build and Push Images` runs on a `v*` tag push, on a pull request that
touches the image recipe or this workflow, and on `workflow_dispatch`.

| Behavior | Where |
| --- | --- |
| One job, `build-and-push`, matrix of `kafka-delta-ingest`, `crawler`, `metrics`. Runner is `ubuntu-latest`. | job `build-and-push` |
| Token permissions are `contents: read`, `packages: write`, and `id-token: write`. `id-token` is only so keyless cosign can request a GitHub OIDC token. There is no `attestations: write`. | job `permissions` |
| A push happens only when the event is `push` (the tag trigger) and `secrets.GHCR_PAT` is non-empty. Pull requests and `workflow_dispatch` do not push. | env `PUSH` |
| Registry login uses that PAT against `ghcr.io`, and only when `PUSH` is true. | step "Log in to GitHub Container Registry" |
| First build loads `local/<image>:ci` into the Docker engine (`load: true`, `push: false`) and writes a GitHub Actions cache (`type=gha`, `mode=max`, scope = image name). | step "Build … image (load locally)" |
| Smoke test runs that local tag. | step "Smoke test …" |
| Trivy fails the job on fixable CRITICAL findings, using the local tag, before any push. The action is pinned to commit `ed142fd0673e97e23eac54620cfb913e5ce36c25`. Suppressions are [`Scraping_project/.trivyignore`](../../Scraping_project/.trivyignore). | step "Trivy scan …"; [scan notes](../../Scraping_project/trivy-reports/README.md) |
| Second build pushes `ghcr.io/<repo>/<image>` with tags from `docker/metadata-action` (the git tag, the commit SHA, and `latest` only when the event is on the default branch). Same GHA cache scope, read-only on this step. `provenance` is not set. `sbom` is not set. | step "Push … image" |
| After a push, keyless cosign (`cosign sign --yes --oidc-provider=github-actions`, no `--key`) signs `ghcr.io/<repo>/<image>@<digest the push step reported>`. The same job runs `cosign verify` with certificate identity `https://github.com/<repository>/.github/workflows/cd-release.yml@<github.ref>` and issuer `https://token.actions.githubusercontent.com`. The signature is stored with the digest in GHCR and in the public Rekor log. It is an image signature, not a SLSA Provenance statement. | steps "Install Cosign", "Sign … image (keyless cosign)", "Verify … image signature" |
| After that verify, Syft (`anchore/sbom-action`) writes an SPDX JSON SBOM for the same digest, not the git tag, and uploads it as a workflow artifact named `sbom-<image>`. The upload fails the job if the file is missing. | steps "Generate SBOM …" and "Upload … SBOM" |
| Tag runs are not cancelled. A new push to a pull request cancels the previous pull-request run. | `concurrency` |

Other actions in this file are floating major tags: `actions/checkout@v7`,
`docker/setup-buildx-action@v4`, `docker/login-action@v4`,
`docker/metadata-action@v6`, `docker/build-push-action@v7`,
`anchore/sbom-action@v0`, `actions/upload-artifact@v6`.
`sigstore/cosign-installer` is pinned to commit
`6f9f17788090df1f26f669e9d70d6ae9567deba6` (v4.1.2) and installs Cosign
`v3.0.6`. Checkout does not set `persist-credentials: false`.

There is a keyless cosign image-sign step. There is no
`actions/attest-build-provenance` step and no
`slsa-framework/slsa-github-generator` call in any workflow under
[`.github/workflows/`](../../.github/workflows/cd-release.yml).
The cosign step does not sign a SLSA predicate. It does not close the
provenance gaps below.

The push step does not opt out of BuildKit provenance. `docker/build-push-action`
v4 and later, including the v7 tag used here, requests a BuildKit provenance
attestation when `provenance` is unset, the exporter is not `load` into the
Docker engine, and `BUILDX_NO_DEFAULT_ATTESTATIONS` is unset. On a public
repository that default is `mode=max`. This repository is public. The load
step does not get that attestation, because `load: true` disables it. The
workflow never reads that BuildKit attestation back and never requires it.
The push step's digest output is used only for the image signature and the
SBOM. That default is unsigned builder metadata about the second
build. It is not accepted as any SLSA level. No release has been published to
inspect (see [SECURITY.md](../../SECURITY.md): version `0.1.0`, no stable
release yet), so this note does not describe a manifest that was actually
pulled.

### [`main.yml`](../../.github/workflows/main.yml)

`CI/CD Pipeline` runs tests on push to `main` and on pull requests. It does
not push container images.

| Job | Publishes? |
| --- | --- |
| `test`, `entrypoint-smoke` | No. |
| `package-smoke` | No. Builds a wheel with `python -m build` and installs it on the runner. |
| `deploy` | Conditionally. On push to `main`, after the three jobs above, it builds a wheel again. If `DEPLOY_HOST`, `DEPLOY_USER`, and `DEPLOY_KEY` are all set, it copies `Scraping_project/dist/*.whl` to the host and reinstalls it. If any secret is empty, it prints a notice and does not copy. There is no digest and no attestation. |

[Releasing](../RELEASING.md) says a `v*` tag is the only image publish. The
`deploy` job is a separate, secret-gated wheel copy. It is not part of the
image target.

### Other workflows

These do not publish release artifacts:

- [`ci-compose.yml`](../../.github/workflows/ci-compose.yml) renders Compose
  config. It does not start the stack.
- [`ci-dashboard.yml`](../../.github/workflows/ci-dashboard.yml) runs the
  dashboard's node tests.
- [`ci-kafka-alerts.yml`](../../.github/workflows/ci-kafka-alerts.yml) runs
  the alert-rule tests.
- [`ci-observability-offline.yml`](../../.github/workflows/ci-observability-offline.yml)
  runs an offline observability test on pull requests.

[`.github/release.yml`](../../.github/release.yml) only groups generated
release notes. It does not attach attestations.
[RELEASING.md](../RELEASING.md) documents the keyless image signature and the
SBOM artifact. It states that the signature is not a provenance statement.
It does not describe a SLSA Provenance predicate or a builder id.

## Gap checklist

Mapped to the v1.2 Build requirements. Every item is open.

### Producer

- [ ] **Choose a build platform that can reach Build L3.** The producer has to
  adopt a builder that generates unforgeable signed provenance. The current
  job is a tenant workflow on `ubuntu-latest` with no SLSA provenance step.
  Candidates to evaluate at implementation time, not selected here: GitHub
  artifact attestations (`actions/attest-build-provenance`) on GitHub-hosted
  runners, or the SLSA GitHub generator container workflow. Whichever is
  chosen has to document its own level. This note does not pre-certify
  `ubuntu-latest`.
- [ ] **Keep one consistent build definition.** Verifiers need a stable
  expectation of how a release image is built. Today the recipe is
  `cd-release.yml` plus `Scraping_project/Dockerfile`, but almost every
  action is a floating tag (listed above). Trivy is pinned to a commit
  because those tags were force-pushed. The Cosign installer is pinned to
  a commit so its tag cannot move either. A moving action tag changes the
  build with no commit in this repo. Base images are moving tags too:
  `python:3.11-slim`, `rust:1-slim-bookworm`, and `debian:bookworm-slim`.
  Language deps are locked
  ([`requirements.txt`](../../Scraping_project/requirements.txt),
  [`kafka-delta-ingest/Cargo.lock`](../../Scraping_project/kafka-delta-ingest/Cargo.lock));
  the base image digests are not.
- [ ] **Distribute provenance with the artifact.** Nothing writes a consumer-
  facing attestation, and the release checklist does not ask for one.
  Attestations have to be bound to image digests. A git tag or a GitHub
  Release can point at several artifacts; the statement belongs to each
  digest. An Actions artifact that expires does not count as distribution
  for the life of the image.

### Provenance exists (required from Build L1 up)

The build must emit provenance that identifies the output by cryptographic
digest and describes how that package was produced, in a form consumers
accept. Unsigned and slightly incomplete provenance can satisfy L1's
authenticity bar. This repo still does not meet L1, for the reasons below.
Do not mark L1 met off the BuildKit default described above.

- [ ] **Emit a SLSA Provenance v1 statement whose subject is the pushed
  manifest digest.** There is no such step. The job has `id-token: write`
  so keyless cosign can obtain an OIDC token for the image signature. It
  does not have `attestations: write`, which GitHub attestation actions
  need, and no step emits a SLSA Provenance v1 statement.
- [ ] **Attest the image that was actually gated.** Smoke and Trivy run
  against `local/<image>:ci` from the first build. The push step builds
  again. Those are two BuildKit invocations. Provenance for the second
  build does not describe the image the gates ran on, and the gates did not
  run on the digest consumers pull. One build should be identified by
  digest, scanned, and published, or the published digest should be what is
  scanned. That change belongs in a later workflow edit, not in this note.
- [ ] **Do not count the unset-`provenance` BuildKit default as the
  statement.** It is not checked, it is not signed, it is not the v1
  predicate from a control plane, and it is absent from the loaded image
  that was tested. Leave it off the "level met" column. When a signed
  statement is added, decide explicitly whether the BuildKit attestation
  stays. `mode=max` can record more of the build than a reviewer intends,
  including material captured from the network fetch. Do not flip it to
  `mode=max` in order to claim a level.
- [ ] **Name subjects by digest, never only by tag.** The SBOM and the
  cosign signature both use the digest the push step reports
  (`steps.push.outputs.digest`). A tag can still move. The provenance
  subject, which does not exist yet, has to be that same digest.
- [ ] **Publish path and retention.** Provenance is only meaningful for an
  image that was pushed. Push depends on a tag event and a non-empty
  `GHCR_PAT`. No GitHub release has been published yet. The SPDX file is a
  workflow artifact (`actions/upload-artifact`), with that action's default
  retention, and the SBOM action is told `upload-artifact: false`. The
  release checklist tells a person to attach that file when they create the
  release. The workflow does not create the release, and the SBOM is not an
  OCI referrer on GHCR.

### SBOM and vulnerability scanning are not provenance

- [ ] **Keep the SBOM, and do not call it provenance.** Syft SPDX JSON
  describes packages found in an image. SLSA provenance describes the
  builder, the build steps, and the top-level inputs of a specific digest.
  The file is generated from the pushed digest, which is the second build,
  so it may not match the image Trivy scanned. It is still not provenance.
  Consumers can fetch it from the Actions artifact until that artifact
  expires, and the release checklist says to attach it to the GitHub
  release. It is not an OCI referrer and not attached by BuildKit.
  `sbom:` is unset on both `docker/build-push-action` steps.
- [ ] **Keep the Trivy gate, and do not call it provenance.** Failing the
  job on a fixable CRITICAL CVE is a vulnerability control. It says nothing
  about which source or builder produced the image. The gate is documented
  in [`Scraping_project/trivy-reports/README.md`](../../Scraping_project/trivy-reports/README.md).

### Provenance is authentic (required from Build L2 up)

Consumers must be able to check that the statement was not altered after the
build and to see which platform they have to trust. The platform, not the
tenant's build steps, generates it. A signature is the usual way; an
equivalent authenticity mechanism is allowed. There is no minimum strength
at L2 beyond deterring someone who would have to bypass a control on
purpose.

- [ ] **Have the platform sign the statement.** Keyless cosign signs the
  image digest from a step in this job. That certificate is an image
  signature, not a SLSA Provenance predicate, and the tenant step chooses
  the bytes. `GHCR_PAT` authorizes a push to GHCR. It is not a signature
  over how the image was built, and it is not held by a provenance control
  plane.
- [ ] **Give verifiers a trust anchor.** [RELEASING.md](../RELEASING.md) now
  says how to check the image signature: source
  `https://github.com/BenjaminSRussell/Scrapy`, workflow path
  `.github/workflows/cd-release.yml`, issuer
  `https://token.actions.githubusercontent.com`, and the three image names
  by digest. That is the image-signature check. It is not a SLSA builder id,
  because no provenance statement exists. Do not treat the cosign identity
  as a SLSA level.
- [ ] **Do not treat "it ran on GitHub" as L2.** `runs-on: ubuntu-latest`
  means the compute is hosted, which L2 also requires. Hosted compute
  without a platform-generated signature is not Build L2.

### Provenance is unforgeable, and builds are isolated (Build L3)

L3 adds two platform properties. Signing material is not available to the
user-defined steps. Builds do not influence each other, including through a
poisoned cache, leftover workspace, or a service the provenance does not
record. External parameters are fully listed. Resolved dependencies stay
best-effort; hermeticity is not required.

- [ ] **Do not put a provenance signing key in the job.** A cosign key, or
  any other signing secret, mounted from Actions secrets into these steps
  would fail the unforgeable requirement. The key has to stay in the
  platform's control plane (for example a GitHub attestation identity).
  Cosign is used keyless for the image digest. No signing key and no key
  password are in the job. That meets the "no key in the job" constraint
  for the image signature. The gap that remains is the missing
  control-plane signature over a SLSA Provenance statement.
- [ ] **Separate the registry credential from the provenance trust root.**
  The job that runs the Dockerfile also receives `GHCR_PAT` when it pushes.
  A long-lived PAT that a build step can read is a poor publish credential
  and is not what makes provenance authentic. Prefer the job token for
  GHCR if that token is allowed to push these packages. If the PAT remains,
  it still must not be described as the signer of provenance.
- [ ] **Stop persisting the checkout credential.** `actions/checkout@v7`
  keeps a git credential for later steps unless told not to. No later step
  in this job pushes commits. An L3-shaped job sets
  `persist-credentials: false`.
- [ ] **Account for the shared GHA cache.** Cache scope is the image name,
  shared across runs, with `mode=max` on the load build. L3 requires that
  one build cannot inject cache entries that change another build's output,
  or that the output is identical when the cache is off. This workflow does
  not show either. GitHub-hosted runners do not close this item by
  themselves.
- [ ] **Record every external parameter.** The build's external inputs
  include the git ref, the workflow file, the Dockerfile target, and the
  decision to push. `workflow_dispatch` does not push today. If a later
  edit publishes from a manual run, every dispatch input has to show up in
  the provenance. Do not add that publish path without them.
- [ ] **Confirm isolation from the builder's own documentation at
  implementation time.** L3 isolation (ephemeral environment, no cross-build
  influence, no access to the signing material) is a property of the
  platform. This note does not grade `ubuntu-latest` as meeting it. Do not
  move these builds to a self-hosted runner and still call them the L3
  target unless that runner is what the provenance says, and unless it
  meets the same bar.

### Outside the image target, and still unattested

- [ ] **`main.yml` `deploy` is not a SLSA release.** It builds an unattested
  wheel and, only when deploy secrets are set, copies that wheel to a host.
  The image target does not cover it. Do not cite a green `deploy` job, or
  a skipped one, as supply-chain provenance. Helm charts and the unpushed
  Dockerfile stages are likewise uncovered.
- [ ] **CI workflows need no release attestation.** `ci-compose.yml`,
  `ci-dashboard.yml`, `ci-kafka-alerts.yml`,
  `ci-observability-offline.yml`, and `package-smoke` do not publish. No
  provenance gap is filed against them beyond that.

## Order to close the gaps

Not an implementation. A later change should do these in order, because a
signature over the wrong bytes does not help:

1. Build the release image once, take its manifest digest, and run smoke
   and Trivy against that digest.
2. Have the platform sign a SLSA Provenance v1 statement for that digest,
   with `id-token` permissions and no signing key in the job. Publish the
   statement where a consumer can fetch it for as long as the image exists.
3. Pin the actions that define the build, drop unused credentials, and
   settle the GHA cache question.
4. Image-signature verification now lives in `docs/RELEASING.md`.
   Provenance verifier expectations are still unwritten, because there is
   no provenance statement.
5. Only then revisit whether the chosen platform's documented isolation
   supports calling the result Build L3. Until that review, the level
   remains a target, not a label on the images.

## Non-goals

- Rewriting the Dockerfile so `apt`, `pip`, and `cargo` have no network.
  That is hermeticity. v1.2 Build L3 does not require it.
- Bit-for-bit reproducible rebuilds.
- A Source track level. v1.2's Source track runs from version control
  through history, technical controls, and two-party review. None of
  those levels are scored here.
- Signing or attesting the wheel deploy path, the Helm chart, or local
  `docker compose` builds.
- Editing this note in order to claim a SLSA level. A workflow change that
  adds an image signature must update the facts above and must leave the
  level claim at "none".
