# SPDX-FileCopyrightText: 2026 keplertech.io
#
# SPDX-License-Identifier: Apache-2.0

"""Run every case of tests/cases.json through kepler_formal_action.py.

This is the local equivalent of the self-test workflow (which runs the same
cases through `uses: ./`). It needs the kepler-formal package in the Python
running pytest; the cases are skipped otherwise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from check_case import check, load_cases

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "kepler_formal_action.py"

# Every input the self-test workflow passes; unset ones are empty strings.
ACTION_INPUTS = (
    "config-path", "design1", "design2", "format", "top", "design1-top",
    "design2-top", "mode", "sec-engine", "sec-encoding", "max-k",
    "reset-ports", "reset-cycles", "liberty-files",
)

try:
    import kepler_formal  # noqa: F401

    HAVE_ENGINE = True
except ImportError:
    HAVE_ENGINE = False


def parse_github_output(text: str) -> dict:
    outputs: dict = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            name, marker = line.split("<<", 1)
            value = []
            for inner in lines:
                if inner == marker:
                    break
                value.append(inner)
            outputs[name] = "\n".join(value)
        elif "=" in line:
            name, value = line.split("=", 1)
            outputs[name] = value
    return outputs


def run_action(case: dict, tmp_path: Path) -> tuple[int, dict]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    for name in ACTION_INPUTS:
        env["INPUT_" + name.upper().replace("-", "_")] = case["inputs"].get(name, "")
    artifact_dir = f".artifacts/pytest/{case['name']}"
    # A stale result.json from an earlier run must not hide a missing one.
    shutil.rmtree(REPO / artifact_dir, ignore_errors=True)
    env.update(
        GITHUB_WORKSPACE=str(REPO),
        GITHUB_OUTPUT=str(tmp_path / "output"),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary"),
        INPUT_ARTIFACT_DIR=artifact_dir,
        INPUT_WRITE_STEP_SUMMARY="true",
    )
    completed = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=600,
    )
    outputs = parse_github_output((tmp_path / "output").read_text())
    return completed.returncode, outputs


@pytest.mark.skipif(not HAVE_ENGINE, reason="kepler-formal is not installed")
@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["name"])
def test_case(case: dict, tmp_path: Path) -> None:
    returncode, outputs = run_action(case, tmp_path)
    outcome = "success" if returncode == 0 else "failure"
    problems = check(case, outputs, outcome, REPO)
    assert str(returncode) == outputs.get("exit-code"), "exit code and output differ"
    assert not problems, "\n".join(problems)
