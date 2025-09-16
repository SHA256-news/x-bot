#!/usr/bin/env bash
set -euo pipefail

ensure_secret() {
  local primary="$1"
  shift
  local value="${!primary:-}"
  if [[ -n "$value" ]]; then
    printf '  - %s length: %s\n' "$primary" "${#value}"
    return 0
  fi

  local alt alt_value
  for alt in "$@"; do
    alt_value="${!alt:-}"
    if [[ -n "$alt_value" ]]; then
      printf '  - %s not set, using %s (length: %s)\n' \
        "$primary" "$alt" "${#alt_value}"
      export "$primary"="$alt_value"
      return 0
    fi
  done

  printf '  - %s is missing.\n' "$primary" >&2
  return 1
}

missing=0
printf 'Checking required secrets...\n'

ensure_secret EVENT_REGISTRY_API_KEY NEWSAPI_API_KEY || missing=1
ensure_secret TWITTER_API_KEY || missing=1
ensure_secret TWITTER_API_SECRET || missing=1
ensure_secret TWITTER_ACCESS_TOKEN || missing=1
ensure_secret TWITTER_ACCESS_TOKEN_SECRET TWITTER_ACCESS_SECRET || missing=1

if (( missing )); then
  printf '\nOne or more required secrets are unavailable. Configure them in the Codex environment and retry.\n' >&2
  exit 1
fi

if (( $# )); then
  printf '\nExecuting command with injected secrets:'
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
  printf '\n\n'
  exec "$@"
fi

printf '\nAll required secrets are available.\n'
