# kepler-formal-action

GitHub Action that proves two versions of a hardware design equivalent with
[Kepler Formal](https://github.com/keplertech/kepler-formal): combinational
(LEC) or sequential (SEC) equivalence checking of SystemVerilog/Verilog RTL,
gate-level Verilog netlists or VHDL.

The action does not decide what the two versions are. The calling workflow
prepares both designs in the workspace (for example by checking out the base
and head of a pull request and generating the RTL), then passes them to the
action. The step passes only when the designs are proved equivalent.

## How it runs

The action installs the [`kepler-formal`](https://pypi.org/project/kepler-formal/)
package from PyPI into a virtual environment and runs
`kepler_formal_action.py`, which loads both designs with NajaEDA and verifies
them with the Kepler engine in process.

- No Docker, no Nix and no compilation: the install takes a few seconds.
- Runs on Linux x86_64 and arm64, and on macOS arm64 runners (any platform
  with a kepler-formal wheel).
- The kepler-formal version is pinned by the `kepler-formal-version` input
  (default `1.0.0`). Any pip requirement works there too, for example a wheel
  built by the kepler-formal CI, to try an unreleased build.

## Usage

### Direct inputs

```yaml
- uses: keplertech/kepler-formal-action@v1
  with:
    design1: base/build/hardware/src/*.v
    design2: head/build/hardware/src/*.v
    top: iob_axi2iob
    mode: sec
    sec-engine: pdr
    reset-ports: arst_i=1
    reset-cycles: '4'
```

### kepler-formal YAML config

Existing kepler-formal configs keep working:

```yaml
- uses: keplertech/kepler-formal-action@v1
  with:
    config-path: ci/kepler-formal.yaml
```

```yaml
format: systemverilog
verification: sec
sec_engine: k_induction
max_k: 8
sec_reset:
  cycles: 4
  ports:
    - {name: arst_i, active_value: 1}
sv_design1_top: iob_axi2iob
sv_design2_top: iob_axi2iob
input_paths:
  - [base/src/a.v, base/src/b.v]
  - [head/src/a.v, head/src/b.v]
```

A config without `verification` runs SEC in the action, while the kepler-formal
executable runs LEC. Set `verification: lec` to keep LEC.

Relative paths in a config are resolved against the workspace. The action
supports these config keys: `format`, `design1_format`, `design2_format`,
`verification`, `max_k`, `sec_engine`, `sec_encoding`, `solver`, `sec_reset`,
`input_paths`, `liberty_files`, `verilog_preprocessing`, `*_design{1,2}_top`
for Verilog, SystemVerilog and VHDL, `sv_design{1,2}_flist`, `sv_single_unit`,
`set_as_boundary`, `allow-boundary-mismatch`, `report_skipped_pos`,
`learn_internal_relations`, `allow_x_equality_in_internal_relations`,
`binary_relation_round`, `latch_abstraction`, `data_latch_model`,
`btor2_export_path`, `dump_only`, `log_level` and `log_file` (ignored: the log
always goes to the artifact directory). Other keys of the executable (Python
designs or primitives, `sv2v`, CNF exports, scopes) are rejected with an error,
and so is a Liberty library combined with SystemVerilog input.

### Choosing a mode

The action runs SEC (sequential equivalence) unless `mode` or `verification`
says otherwise.

- **SEC** compares the two designs cycle by cycle from reset. It tolerates
  renamed, retimed or re-encoded registers, so it fits comparing RTL revisions
  (pull requests, refactors, history replays). Give the reset ports with
  `reset-ports` (or `sec_reset`) so both designs start from a known state.
  Outputs whose logic spans several clock domains are skipped. The run then
  ends `partially_proved` (exit 1); set `report_skipped_pos: true` in a config
  to list the skipped outputs.
- **LEC** (combinational equivalence) pairs registers by name and proves the
  logic between them. It is faster, but it suits only designs whose registers
  correspond one to one, for example a netlist against the RTL it came from.
  Between RTL revisions it often stops on a boundary mismatch.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `config-path` | | kepler-formal YAML config inside the workspace. Use either this or `design1`/`design2`. |
| `design1` | | Reference design: files or globs, separated by spaces or newlines. |
| `design2` | | Implementation design, same form. |
| `format` | `systemverilog` | `systemverilog`, `verilog` (gate-level), `vhdl` or `naja_if`. |
| `top` | | Top module of both designs; inferred when empty (see [Top module](#top-module)). |
| `design1-top`, `design2-top` | | Per-design top, when the names differ; overrides `top`. |
| `mode` | `sec` | `sec` or `lec`; see [Choosing a mode](#choosing-a-mode). |
| `sec-engine` | | `pdr`, `k_induction` or `imc`. |
| `sec-encoding` | | `binary` or `dual_rail_steady`. |
| `max-k` | | SEC bound. |
| `reset-ports` | | SEC reset bootstrap: `<port>=<active value>` pairs, e.g. `arst_i=1 cke_i=0`. Empty (`''`) means no bootstrap. |
| `reset-cycles` | `4` | Reset bootstrap length when `reset-ports` is set. |
| `liberty-files` | | Liberty files for gate-level Verilog. |
| `artifact-dir` | `.kepler-formal-action` | Workspace-relative directory for logs and results. |
| `write-step-summary` | `true` | Append the verdict (and the counterexample) to the step summary. |
| `kepler-formal-version` | `1.0.0` | PyPI release, or any pip requirement. |
| `python-version` | `3.12` | Set up with `actions/setup-python`; empty uses `python3` from `PATH`. |

## Outputs

| Output | Description |
| --- | --- |
| `verdict` | See [Verdicts](#verdicts). |
| `exit-code` | `0` equivalent, `1` different (LEC) or partially proved, `2` inconclusive/unsupported, `3` counterexample (SEC), `64` invalid input (including `top_not_found`, `top_ambiguous`), `70` error. |
| `status` | `success` when proved equivalent, `failure` otherwise, like the step outcome. Use `verdict` or `exit-code` to tell a result from a run that could not verify. |
| `design1-top`, `design2-top` | Top module used for each design, given or inferred; empty when it could not be resolved. |
| `artifact-dir` | Workspace-relative artifact directory. |
| `kepler-formal-version` | Installed kepler-formal version. |

### Verdicts

| Verdict | Status | Exit code | Meaning |
| --- | --- | --- | --- |
| `equivalent` | `success` | 0 | Proved equivalent. |
| `different` | `failure` | 1 (LEC), 3 (SEC) | Not equivalent; the counterexample is in the summary and the log. |
| `partially_proved` | `failure` | 1 | Some outputs proved, no difference found. |
| `inconclusive`, `unsupported` | `failure` | 2 | No verdict from the engine; the reason is in the summary. |
| `top_not_found` | `failure` | 64 | The top module given (or the only candidate) is not defined in the design files. The message names the missing module and lists the modules found. |
| `top_ambiguous` | `failure` | 64 | No top given and several modules are not instantiated by any other one. The message lists the candidates. |
| `invalid_input` | `failure` | 64 | Invalid input or config, for example a `design1`/`design2` glob that matches no file. The message names the input. |
| `error` | `failure` | 70 | Design loading or internal error. The message is in the summary and `result.json`. |

`status` follows the GitHub Actions step outcome: `success` or `failure`.
The last four verdicts (exit codes 64 and 70) mean that no verification ran:
branch on `verdict` or `exit-code` to report them differently from a
counterexample.

## Top module

For Verilog and SystemVerilog sources, the action reads the module
declarations and instantiations of each design (following `` `include``
files) before the engine runs:

- With `top` (or `design1-top` / `design2-top`), the module must be defined in
  that design's files. Otherwise the step fails with verdict `top_not_found`,
  for example: ``design1: top module `iob_reg` (from `top`) is not defined in
  the design1 files. Modules found: `iob_reg_ca`, ...`` (with the
  top-level candidates).
- Without a top, each design's top is the one module that no other module of
  that design instantiates. None or several fail with `top_not_found` or
  `top_ambiguous`, listing the candidates.
- The two designs may end up with different top names: the check still runs
  (ports are matched by name) and the summary notes both names.

The chosen tops are in the `design1-top` / `design2-top` outputs, the summary
and `result.json`. VHDL, `naja_if`, file lists (`sv_design*_flist`) and
sources whose module names come from macros are left to the frontend, as
before.

To expect a difference (for example to document an intended behavior change),
use `continue-on-error: true` and check `steps.<id>.outputs.verdict`.

## Artifacts

- `kepler-formal.log`: complete output, including the counterexample trace.
- `kepler-formal-engine.log`: the engine's own log file.
- `summary.md`: the step summary.
- `result.txt` / `result.json`: verdict, exit code, output coverage and timing.
  `result.json` is written for every run, including errors, with the fields
  `verdict`, `exit_code`, `verification`, `bound`, `reason`,
  `covered_outputs`, `total_outputs`, `proven_outputs`, `elapsed_seconds`
  (unchanged; `null` when no verification ran) and `status`, `mode`,
  `design1_top`, `design2_top`, `top_source` (`input`, `inferred` or
  `frontend`), `reset_ports`, `reset_cycles`, `error`, `error_details` (the
  modules and candidates for top errors), `kepler_formal_version`,
  `najaeda_version`.
- `request.json`: the resolved designs and options.
- `config.yaml`: a copy of the config, when `config-path` is used.
- `work/`: working files of the engine and frontends (diagnostics, miter logs).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install kepler-formal==1.0.0 PyYAML pytest
.venv/bin/python -m pytest tests -v       # unit tests + every self-test case
PYTHON=.venv/bin/python ci/smoke.sh       # exit-code smoke matrix
```

Tests:

- `tests/test_unit.py`: plain logic, no engine needed (top selection, glob
  expansion, argument building, `result.json`, summary).
  `pytest tests/test_unit.py` runs without kepler-formal installed.
- `tests/cases.json`: the self-test cases, with small Verilog fixtures under
  `tests/fixtures/`. Each case lists the action inputs and the expected
  `status`, `verdict`, `exit-code`, `design1-top`, `design2-top`, `result.json`
  fields and summary/error phrases. `tests/test_cases.py` runs them locally
  through `kepler_formal_action.py`; the `test` workflow runs them through
  `uses: ./` and checks them with `tests/check_case.py`.
- `ci/smoke.sh`: exit codes of the `testdata/` fixtures; the `smoke-test`
  workflow runs it on Linux x86_64, Linux arm64 and macOS.

The `test` workflow runs on every pull request and every push to `main`.
Require its `Action self-test` check in the branch protection of `main` so a
regression blocks merging. To add a case, add fixtures under `tests/fixtures/`
and an entry to `tests/cases.json`; both runners pick it up.

To move to a new kepler-formal release, change the `kepler-formal-version`
default in `action.yml` (the workflow reads it from there), run the smoke
matrix, and tag a release (`v1.x.y`, then move `v1`).
