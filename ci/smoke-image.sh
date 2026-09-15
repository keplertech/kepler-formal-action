#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <image-tag> <artifact-directory> <temporary-directory>" >&2
  exit 2
fi

image_tag="$1"
artifact_root="$2"
temporary_root="$3"

mkdir -p "$artifact_root" "$temporary_root"
: > "$temporary_root/github_output"
: > "$temporary_root/github_summary"

run_case() {
  local case_name="$1"
  local expected_exit_code="$2"
  local config_path="$3"
  local exit_code

  set +e
  docker run --rm --platform linux/amd64 \
    -v "$PWD":/github/workspace \
    -e GITHUB_WORKSPACE=/github/workspace \
    -e GITHUB_OUTPUT=/github/workspace/"$temporary_root"/github_output \
    -e GITHUB_STEP_SUMMARY=/github/workspace/"$temporary_root"/github_summary \
    "$image_tag" \
    "$config_path" \
    "$artifact_root/$case_name" \
    true
  exit_code=$?
  set -e

  if [[ "$exit_code" -ne "$expected_exit_code" ]]; then
    echo "Case $case_name was expected to exit with $expected_exit_code but exited with $exit_code." >&2
    return 1
  fi
}

run_case verilog-equivalent 0 testdata/verilog/configs/equivalent.yaml
run_case verilog-mismatch 1 testdata/verilog/configs/mismatch.yaml
run_case systemverilog-operator-mismatch 3 testdata/systemverilog/configs/operator-mismatch.yaml
run_case systemverilog-dff-sync 0 testdata/systemverilog/configs/dff-sync.yaml
run_case systemverilog-dff-async-reset 0 testdata/systemverilog/configs/dff-async-reset.yaml
run_case systemverilog-complex-comb-mismatch 3 testdata/systemverilog/configs/complex-comb-mismatch.yaml

echo "All image smoke cases matched their expected exit codes."
