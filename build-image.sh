#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
image_ref="${1:-kepler-formal-action:local}"
flake_override="${KEPLER_FORMAL_FLAKE_OVERRIDE:-}"

if ! command -v nix >/dev/null 2>&1; then
  echo "nix with an x86_64-linux builder is required to build the local action image." >&2
  echo "Install Nix, or consume a published action ref so the wrapper can pull its GHCR image." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to load and run the action image." >&2
  exit 1
fi

nix_args=(
  build
  "$script_dir#packages.x86_64-linux.container"
  --accept-flake-config
  --no-link
  --no-update-lock-file
  --print-build-logs
  --print-out-paths
)

if [[ -n "$flake_override" ]]; then
  nix_args+=(--override-input kepler-formal "$flake_override")
fi

echo "Building the action image from the locked Nix inputs" >&2
image_archive=$(nix "${nix_args[@]}")

echo "Loading Nix-built image from $image_archive" >&2
docker load --input "$image_archive" >&2
docker tag kepler-formal-action:nix "$image_ref"

printf '%s\n' "$image_ref"
