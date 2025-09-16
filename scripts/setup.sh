#!/usr/bin/env bash
set -euo pipefail

REQUIRED_SECRETS=(
  EVENT_REGISTRY_API_KEY
  TWITTER_API_KEY
  TWITTER_API_SECRET
  TWITTER_ACCESS_TOKEN
  TWITTER_ACCESS_SECRET
)

missing=0
printf 'Checking required secrets...\n'
for var in "${REQUIRED_SECRETS[@]}"; do
  value="${!var:-}"
  if [[ -z "$value" ]]; then
    printf '  - %s is missing.\n' "$var" >&2
    missing=1
  else
    printf '  - %s length: %s\n' "$var" "${#value}"
  fi
  unset value
done

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
