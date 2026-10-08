# Shell script standard (#822)

Applies to every tracked `*.sh` under `Scraping_project/` except `temp_scripts/`.
`tests/unit/test_shell_strict_mode_822.py` enforces it.

## Header

```bash
#!/usr/bin/env bash   # or #!/bin/bash
set -euo pipefail
```

| Script kind | Header | Why |
|---|---|---|
| Actions (entrypoints, resets, deploys, rebuilds) | `set -euo pipefail` | Stop on the first failure; a half-applied reset is worse than none. |
| Diagnostics (`diagnose.sh`, `scripts/diagnose_issues.sh`, `scripts/smoke_local.sh`) | `set -uo pipefail` | No `-e`: report one failing probe and keep running the rest. These scripts track failures explicitly. |
| Sourced libraries (`scripts/compose_lib.sh`) | none | A library must not change the caller's shell options. Write it to work under the caller's strict mode. |

## Pitfalls under strict mode

- **`cmd | grep -q PATTERN`**: `grep -q` exits on the first match. If `cmd` is still writing, it gets SIGPIPE, and `pipefail` reports the whole pipeline as failed, so the check reads "not found" on a match. Capture first:
  ```bash
  out="$(kubectl get pods 2>/dev/null || true)"
  if grep -q Running <<<"$out"; then ...
  ```
- **Counting**: `grep PAT | wc -l || echo 0` prints `0` twice under `pipefail` when nothing matches. Use `grep -c PAT || true` (it prints `0` and exits 1 on no match).
- **No match must not abort**: under `-e` plus `pipefail`, `ls | grep X | while read ...` aborts the script when nothing matches. Add `|| true` to pipelines where an empty result is fine.
- **Optional env vars under `-u`**: write `${VAR:-}` (or `${VAR:-default}`). Use `: "${VAR:?message}"` for required ones.
- **`cd` in diagnostics** (no `-e`): `cd dir || exit 1`.
- Log the arguments with `"$*"`, and execute them with `"$@"`.
