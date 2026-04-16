#!/usr/bin/env bash

set -euo pipefail

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

resolve_remote_image() {
  local action_repository="$1"
  local action_ref="$2"
  local image_tag=""

  if [[ "$action_ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    image_tag="sha-${action_ref:0:12}"
  elif [[ "$action_ref" == *"/"* ]]; then
    echo "Remote use only supports published release tags or published commit SHAs. Ref '$action_ref' cannot be mapped to a GHCR tag." >&2
    return 1
  else
    image_tag="$action_ref"
  fi

  printf 'ghcr.io/%s:%s\n' "$(lower "$action_repository")" "$image_tag"
}

build_local_image() {
  local action_path="$1"
  local image_ref="kepler-formal-action-local:${GITHUB_SHA:-local}"

  echo "Building local action image from $action_path" >&2
  docker build -t "$image_ref" "$action_path" >&2
  printf '%s\n' "$image_ref"
}

main() {
  local workspace="${GITHUB_WORKSPACE:-$PWD}"
  local action_path="${ACTION_PATH:-$PWD}"
  local action_ref="${ACTION_REF:-}"
  local action_repository="${ACTION_REPOSITORY:-}"
  local image_ref=""

  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required to run this action. Use a Linux runner with Docker available." >&2
    return 1
  fi

  local -a docker_args=(
    --rm
    -v "${workspace}:${workspace}"
    -w "$workspace"
    -e "GITHUB_WORKSPACE=$workspace"
  )

  declare -A mounted_dirs=()

  add_file_mount() {
    local env_name="$1"
    local env_value="${!env_name:-}"
    local parent_dir=""

    if [[ -z "$env_value" ]]; then
      return 0
    fi

    parent_dir="$(dirname "$env_value")"
    mkdir -p "$parent_dir"
    touch "$env_value"

    if [[ -z "${mounted_dirs[$parent_dir]+x}" ]]; then
      docker_args+=(-v "${parent_dir}:${parent_dir}")
      mounted_dirs["$parent_dir"]=1
    fi

    docker_args+=(-e "${env_name}=${env_value}")
  }

  add_file_mount GITHUB_OUTPUT
  add_file_mount GITHUB_STEP_SUMMARY

  if [[ -n "$action_repository" && -n "$action_ref" ]]; then
    image_ref="$(resolve_remote_image "$action_repository" "$action_ref")"
    echo "Pulling published action image $image_ref" >&2
    docker pull "$image_ref" >&2
  else
    image_ref="$(build_local_image "$action_path")"
  fi

  docker_args+=(
    "$image_ref"
    "${INPUT_CONFIG_PATH:?INPUT_CONFIG_PATH is required}"
    "${INPUT_ARTIFACT_DIR:-.kepler-formal-action}"
    "${INPUT_WRITE_STEP_SUMMARY:-true}"
  )

  docker run "${docker_args[@]}"
}

main "$@"
