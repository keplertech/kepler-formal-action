#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 keplertech.io
#
# SPDX-License-Identifier: Apache-2.0

"""Check the outputs and artifacts of one self-test case against tests/cases.json.

Used by the self-test workflow after each `uses: ./` step, and by
tests/test_cases.py when running the cases locally.

Usage (workflow):
  check_case.py <case-name>
      The step outputs come from OUT_STATUS, OUT_VERDICT, OUT_EXIT_CODE,
      OUT_DESIGN1_TOP, OUT_DESIGN2_TOP, OUT_ARTIFACT_DIR and the step outcome
      from STEP_OUTCOME. Paths are relative to the current directory (the
      workspace).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CASES_FILE = Path(__file__).with_name("cases.json")

OUTPUT_NAMES = ("status", "verdict", "exit-code", "design1-top", "design2-top")


def load_cases() -> list[dict]:
    return json.loads(CASES_FILE.read_text())["cases"]


def find_case(name: str) -> dict:
    for case in load_cases():
        if case["name"] == name:
            return case
    raise KeyError(f"unknown case {name!r}")


def check(case: dict, outputs: dict, outcome: str | None, workspace: Path) -> list[str]:
    """Return the list of mismatches (empty when the case passes)."""
    expect = case["expect"]
    problems: list[str] = []

    for name in OUTPUT_NAMES:
        if name in expect and outputs.get(name, "") != expect[name]:
            problems.append(
                f"output {name}: expected {expect[name]!r}, got {outputs.get(name, '')!r}"
            )

    if outcome is not None:
        wanted = "success" if expect.get("exit-code") == "0" else "failure"
        if outcome != wanted:
            problems.append(f"step outcome: expected {wanted}, got {outcome}")

    artifact_dir = outputs.get("artifact-dir", "")
    if not artifact_dir:
        problems.append("output artifact-dir is empty")
        return problems
    artifacts = workspace / artifact_dir

    result_path = artifacts / "result.json"
    if not result_path.is_file():
        problems.append(f"{result_path} is missing")
        result: dict = {}
    else:
        result = json.loads(result_path.read_text())
    for key, value in expect.get("result", {}).items():
        if key not in result:
            problems.append(f"result.json: {key} is missing")
        elif result[key] != value:
            problems.append(f"result.json {key}: expected {value!r}, got {result[key]!r}")
    for key in expect.get("result_keys", []):
        if key not in result:
            problems.append(f"result.json: {key} is missing")

    if "error_contains" in expect:
        error = result.get("error") or ""
        for phrase in expect["error_contains"]:
            if phrase not in error:
                problems.append(f"result.json error does not contain {phrase!r}: {error!r}")

    summary_path = artifacts / "summary.md"
    summary = summary_path.read_text() if summary_path.is_file() else ""
    if not summary:
        problems.append(f"{summary_path} is missing or empty")
    phrases = list(expect.get("summary_contains", []))
    if "error_contains" in expect:
        phrases += expect["error_contains"]
    for phrase in phrases:
        if phrase not in summary:
            problems.append(f"summary.md does not contain {phrase!r}")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 64
    case = find_case(argv[1])
    outputs = {
        "status": os.environ.get("OUT_STATUS", ""),
        "verdict": os.environ.get("OUT_VERDICT", ""),
        "exit-code": os.environ.get("OUT_EXIT_CODE", ""),
        "design1-top": os.environ.get("OUT_DESIGN1_TOP", ""),
        "design2-top": os.environ.get("OUT_DESIGN2_TOP", ""),
        "artifact-dir": os.environ.get("OUT_ARTIFACT_DIR", ""),
    }
    outcome = os.environ.get("STEP_OUTCOME") or None
    print(f"Case {case['name']}: {case['description']}")
    for name, value in outputs.items():
        print(f"  {name} = {value!r}")
    problems = check(case, outputs, outcome, Path.cwd())
    if problems:
        for problem in problems:
            print(f"::error title={case['name']}::{problem}")
        return 1
    print("  all expectations met")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
