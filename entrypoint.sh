#!/usr/bin/env bash

set -euo pipefail

workspace="${GITHUB_WORKSPACE:-/github/workspace}"
config_input="${1:-}"
artifact_input="${2:-.kepler-formal-action}"
write_step_summary="${3:-true}"
tool_bin="/opt/kepler-formal/kepler-formal"

artifact_dir=""
resolved_config=""
log_file=""
status="failure"
exit_code=1

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

resolve_workspace_path() {
  local raw_path="$1"
  local resolved_path

  if [[ -z "$raw_path" ]]; then
    echo "Path input is empty." >&2
    return 1
  fi

  if [[ "$raw_path" = /* ]]; then
    resolved_path="$(realpath -m "$raw_path")"
  else
    resolved_path="$(realpath -m "$workspace/$raw_path")"
  fi

  case "$resolved_path" in
    "$workspace"|"$workspace"/*)
      printf '%s\n' "$resolved_path"
      ;;
    *)
      echo "Path must stay inside $workspace: $raw_path" >&2
      return 1
      ;;
  esac
}

to_workspace_relative() {
  local absolute_path="$1"

  if [[ "$absolute_path" == "$workspace" ]]; then
    printf '.\n'
  else
    printf '%s\n' "${absolute_path#$workspace/}"
  fi
}

write_output() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

finalize() {
  local artifact_relative=""
  local config_relative=""
  local upstream_ref="unknown"

  if [[ -n "$artifact_dir" ]]; then
    artifact_relative="$(to_workspace_relative "$artifact_dir")"
  fi

  if [[ -n "$resolved_config" ]]; then
    config_relative="$(to_workspace_relative "$resolved_config")"
  fi

  if [[ -f /opt/kepler-formal/meta/upstream-ref ]]; then
    upstream_ref="$(cat /opt/kepler-formal/meta/upstream-ref)"
  fi

  if [[ -n "$artifact_dir" ]]; then
    if [[ -n "$resolved_config" && -f "$resolved_config" ]]; then
      cp "$resolved_config" "$artifact_dir/config.yaml"
    fi

    cat > "$artifact_dir/result.txt" <<EOF
status=$status
exit_code=$exit_code
artifact_dir=$artifact_relative
config_path=$config_relative
kepler_formal_ref=$upstream_ref
EOF

    cat > "$artifact_dir/summary.md" <<EOF
### kepler-formal action

- Status: $status
- Exit code: $exit_code
- Config: ${config_relative:-unresolved}
- Artifacts: ${artifact_relative:-unresolved}
- Upstream ref: $upstream_ref
EOF
  fi

  if [[ -n "$artifact_relative" ]]; then
    write_output artifact-dir "$artifact_relative"
  fi
  write_output exit-code "$exit_code"
  write_output status "$status"

  if [[ "$(lower "$write_step_summary")" == "true" && -n "${GITHUB_STEP_SUMMARY:-}" && -n "$artifact_dir" ]]; then
    cat "$artifact_dir/summary.md" >> "$GITHUB_STEP_SUMMARY"
  fi
}

trap finalize EXIT

artifact_dir="$(resolve_workspace_path "$artifact_input")"
mkdir -p "$artifact_dir"
log_file="$artifact_dir/kepler-formal.log"
touch "$log_file"
exec > >(tee -a "$log_file") 2>&1

echo "Workspace: $workspace"
echo "Artifact directory: $artifact_dir"

resolved_config="$(resolve_workspace_path "$config_input")"

if [[ ! -f "$resolved_config" ]]; then
  echo "Config file not found: $resolved_config" >&2
  exit_code=2
  exit "$exit_code"
fi

echo "Config file: $resolved_config"
echo "Running kepler-formal..."

set +e
"$tool_bin" --config "$resolved_config"
exit_code=$?
set -e

if [[ "$exit_code" -eq 0 ]]; then
  status="success"
else
  status="failure"
fi

echo "kepler-formal finished with exit code: $exit_code"
exit "$exit_code"