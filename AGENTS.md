# Kepler Formal Action — Agent Guide

This repository publishes the reusable GitHub Action that proves two versions of a hardware design equivalent with Kepler Formal. Preserve the public contract in `action.yml`: callers prepare both designs in the workspace and pass either a kepler-formal YAML config (`config-path`) or direct inputs (`design1`, `design2`, ...); artifacts are written inside the workspace; the step fails unless the designs are proved equivalent.

## Runtime architecture

- The action is a composite action. It installs the `kepler-formal` package from PyPI (pinned by the `kepler-formal-version` input) into a virtual environment under `$RUNNER_TEMP`, then runs `kepler_formal_action.py`.
- The PyPI package exposes the engine as a Python API (`kepler_formal.verify_designs`) and does not ship the `kepler-formal` executable. `kepler_formal_action.py` therefore owns the CLI-compatible behavior: config parsing, design loading through NajaEDA (`loadSystemVerilog`, `loadGateLevelVerilog`, `loadVHDL`, `loadNajaIF`, `loadLibertyPrimitives`), verdict-to-exit-code mapping, logs, outputs and step summary.
- Design loading must follow the executable (`src/bin/KeplerFormal.cpp` upstream): one NLDB per design, Liberty primitives loaded into that DB first, SystemVerilog top selection through a slang command file (`--top`, `--single-unit`, `-f`).
- Unsupported config keys must fail with a clear message (exit 64), never be silently ignored.
- Top modules are checked or inferred in the parent before the engine runs (`resolve_tops`: a lexical scan of module declarations and instantiations, following `` `include``). A missing top fails with verdict `top_not_found`, several candidates with `top_ambiguous` (exit 64). Every failure writes its message to the summary and `result.json`.
- `status` follows the GitHub Actions step outcome: `success` (equivalent) or `failure` (anything else). Whether verification ran is told by `verdict` and `exit-code` (64/70), never by a third status value. Existing `result.json` fields and verdict values must not change; only add new ones.
- Verification runs in a child process so native crashes are reported; its working directory is `<artifact-dir>/work` so engine files never land in the caller's workspace root.
- There is no Docker image, Nix flake or GHCR package any more. Do not reintroduce a second packaging path unless the PyPI package cannot serve a required feature.

## Exit codes

`0` equivalent, `1` different (LEC) or partially proved, `2` inconclusive/unsupported/no result, `3` counterexample (SEC), `64` invalid input, `70` loading or internal error. Keep these stable; callers and `ci/smoke.sh` depend on them.

## Updating Kepler Formal

1. Publish the kepler-formal release to PyPI (wheels for Linux x86_64/arm64, macOS arm64 and Windows).
2. Change the `kepler-formal-version` default in `action.yml`; `.github/workflows/smoke-test.yml` reads it from there.
3. Run `PYTHON=.venv/bin/python ci/smoke.sh` locally and let the workflow run on all runner types.
4. Tag `v1.x.y` and move the `v1` tag.

To try an unreleased build, pass any pip requirement as `kepler-formal-version` (a wheel artifact path or a `git+https://...` URL; a git build compiles from source and is slow).

## Validation

```bash
bash -n ci/smoke.sh
python3 -m py_compile kepler_formal_action.py
python3 -m venv .venv && .venv/bin/pip install kepler-formal==1.0.0 PyYAML pytest
PYTHON=.venv/bin/python ci/smoke.sh
```

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests -v
```

`ci/smoke.sh` is the canonical exit-code matrix of `testdata/`. `tests/cases.json` (fixtures in `tests/fixtures/`) is the action self-test: each case checks outputs, `result.json` and the summary, locally through `tests/test_cases.py` and in CI through `uses: ./` (`.github/workflows/test.yml`, required check `Action self-test`). Add a case there for every new input or verdict, and write the test before the fix.

## Knowledge graph

`graphify-out/` is local and gitignored. If `graphify-out/graph.json` exists and the Graphify skill is available, query it before broad codebase searches, then verify conclusions against the source. After architectural changes, refresh an existing graph with `graphify . --update`. Do not create a full graph merely for a routine lookup unless the user asks for one.
