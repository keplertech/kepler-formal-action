#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 keplertech.io
#
# SPDX-License-Identifier: Apache-2.0

# Canonical smoke matrix: runs kepler_formal_action.py on every supported
# fixture and checks each exit code. The workflow runs the same cases through
# `uses: ./`; add new fixtures here first.
#
# Usage: ci/smoke.sh [artifact-root]
# Environment: PYTHON, a Python with kepler-formal and PyYAML installed
#              (default: python3).

set -uo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
artifact_root=${1:-.artifacts/smoke}
python=${PYTHON:-python3}
failures=0

run_case() {
  local name=$1 expected=$2
  shift 2
  local status
  echo "== $name (expect exit $expected)"
  env -u INPUT_CONFIG_PATH -u INPUT_DESIGN1 -u INPUT_DESIGN2 \
    GITHUB_WORKSPACE="$repo" GITHUB_OUTPUT= GITHUB_STEP_SUMMARY= \
    INPUT_ARTIFACT_DIR="$artifact_root/$name" "$@" \
    "$python" -I "$repo/kepler_formal_action.py" >/dev/null 2>&1
  status=$?
  if [[ $status -eq $expected ]]; then
    echo "   PASS ($(sed -n 's/^verdict=//p' "$repo/$artifact_root/$name/result.txt"))"
  else
    echo "   FAIL: exit $status; log: $artifact_root/$name/kepler-formal.log"
    failures=$((failures + 1))
  fi
}

cd "$repo"

cfg=testdata/verilog/configs
run_case verilog-equivalent 0 INPUT_CONFIG_PATH=$cfg/equivalent.yaml
run_case verilog-mismatch 1 INPUT_CONFIG_PATH=$cfg/mismatch.yaml

cfg=testdata/systemverilog/configs
run_case systemverilog-operator-mismatch 3 INPUT_CONFIG_PATH=$cfg/operator-mismatch.yaml
run_case systemverilog-dff-sync 0 INPUT_CONFIG_PATH=$cfg/dff-sync.yaml
run_case systemverilog-dff-async-reset 0 INPUT_CONFIG_PATH=$cfg/dff-async-reset.yaml
run_case systemverilog-complex-comb-mismatch 3 INPUT_CONFIG_PATH=$cfg/complex-comb-mismatch.yaml
run_case systemverilog-procedural-comb 0 INPUT_CONFIG_PATH=$cfg/procedural-comb.yaml

cases=testdata/systemverilog/cases
run_case inputs-dff-async-reset-sec 0 \
  INPUT_DESIGN1=$cases/dff-async-reset/reference/top.sv \
  INPUT_DESIGN2=$cases/dff-async-reset/latest/top.sv \
  INPUT_TOP=top INPUT_MODE=sec INPUT_SEC_ENGINE=pdr \
  INPUT_RESET_PORTS=rst_n=0 INPUT_RESET_CYCLES=2
run_case inputs-operator-mismatch-sec 3 \
  "INPUT_DESIGN1=$cases/operator-mismatch-basic/reference/*.sv" \
  "INPUT_DESIGN2=$cases/operator-mismatch-basic/latest/*.sv" \
  INPUT_MODE=sec INPUT_SEC_ENGINE=k_induction INPUT_MAX_K=4
run_case inputs-missing-design 64 INPUT_DESIGN1=$cases/dff-sync/reference/top.sv
run_case config-outside-workspace 64 INPUT_CONFIG_PATH=../outside.yaml

if [[ $failures -ne 0 ]]; then
  echo "$failures smoke case(s) failed." >&2
  exit 1
fi
echo "All smoke cases matched their expected exit codes."
