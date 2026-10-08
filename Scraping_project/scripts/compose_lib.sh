#!/usr/bin/env bash
# Shared helpers so ops scripts only touch services that the active Compose file
# defines (#326, #403). Source it from a script:
#
#   . "$(dirname "${BASH_SOURCE[0]}")/compose_lib.sh"     # adjust the relative path
#
# Service discovery is `docker compose config --services`, so profiles and
# override files (COMPOSE_FILE / COMPOSE_PROFILES) are honoured automatically.
# Tests (and dry runs without Docker) can set COMPOSE_SERVICES="a b c" instead.
#
# Library: sets no shell options (it must not change the caller's), but every
# function is safe under the caller's `set -euo pipefail` (#822), including
# bash 3.2 (macOS) empty-array rules.

# compose ARGS... : `docker compose` (v2) when available, else legacy `docker-compose`.
compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

# compose_services : one defined service name per line.
compose_services() {
    if [ -n "${COMPOSE_SERVICES:-}" ]; then
        printf '%s\n' ${COMPOSE_SERVICES}
        return 0
    fi
    compose config --services 2>/dev/null
}

# compose_has SERVICE : exit 0 if SERVICE is defined.
compose_has() {
    # Here-string, not `compose_services | grep -q`: under pipefail, grep -q
    # exiting on the first match can SIGPIPE the producer and report "missing".
    local defined
    defined="$(compose_services)"
    grep -qx -- "$1" <<<"$defined"
}

# compose_filter SERVICE... : print (space separated) the services that exist,
# and warn on stderr about the ones that do not.
compose_filter() {
    local defined present=() missing=() s
    defined="$(compose_services)"
    for s in "$@"; do
        if grep -qx -- "$s" <<<"$defined"; then
            present+=("$s")
        else
            missing+=("$s")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "[compose] not defined in this Compose file, skipping: ${missing[*]}" \
             "(the Kafka/exporter stack needs the full-stack compose, see #145)" >&2
    fi
    echo "${present[*]:-}"  # empty array: no "unbound variable" under set -u (bash 3.2)
}

# compose_first SERVICE... : print the first service that exists (fallback chains
# such as "scraper scrapy-app"). Returns 1 if none exist.
compose_first() {
    local defined s
    defined="$(compose_services)"
    for s in "$@"; do
        if grep -qx -- "$s" <<<"$defined"; then
            echo "$s"
            return 0
        fi
    done
    return 1
}
