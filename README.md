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

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `config-path` | | kepler-formal YAML config inside the workspace. Use either this or `design1`/`design2`. |
| `design1` | | Reference design: files or globs, separated by spaces or newlines. |
| `design2` | | Implementation design, same form. |
| `format` | `systemverilog` | `systemverilog`, `verilog` (gate-level), `vhdl` or `naja_if`. |
| `top` | | Top module of both designs; inferred when empty. |
| `design1-top`, `design2-top` | | Per-design top, when the names differ. |
| `mode` | `lec` | `lec` or `sec`. |
| `sec-engine` | | `pdr`, `k_induction` or `imc`. |
| `sec-encoding` | | `binary` or `dual_rail_steady`. |
| `max-k` | | SEC bound. |
| `reset-ports` | | SEC reset bootstrap: `<port>=<active value>` pairs, e.g. `arst_i=1 cke_i=0`. |
| `reset-cycles` | `4` | Reset bootstrap length when `reset-ports` is set. |
| `liberty-files` | | Liberty files for gate-level Verilog. |
| `artifact-dir` | `.kepler-formal-action` | Workspace-relative directory for logs and results. |
| `write-step-summary` | `true` | Append the verdict (and the counterexample) to the step summary. |
| `kepler-formal-version` | `1.0.0` | PyPI release, or any pip requirement. |
| `python-version` | `3.12` | Set up with `actions/setup-python`; empty uses `python3` from `PATH`. |

## Outputs

| Output | Description |
| --- | --- |
| `verdict` | `equivalent`, `different`, `partially_proved`, `inconclusive`, `unsupported`, `invalid_input` or `error`. |
| `exit-code` | `0` equivalent, `1` different (LEC) or partially proved, `2` inconclusive/unsupported, `3` counterexample (SEC), `64` invalid input, `70` error. |
| `status` | `success` when proved equivalent, `failure` otherwise. |
| `artifact-dir` | Workspace-relative artifact directory. |
| `kepler-formal-version` | Installed kepler-formal version. |

To expect a difference (for example to document an intended behavior change),
use `continue-on-error: true` and check `steps.<id>.outputs.verdict`.

## Artifacts

- `kepler-formal.log`: complete output, including the counterexample trace.
- `kepler-formal-engine.log`: the engine's own log file.
- `summary.md`: the step summary.
- `result.txt` / `result.json`: verdict, exit code, output coverage and timing.
- `request.json`: the resolved designs and options.
- `config.yaml`: a copy of the config, when `config-path` is used.
- `work/`: working files of the engine and frontends (diagnostics, miter logs).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install kepler-formal==1.0.0 PyYAML
PYTHON=.venv/bin/python ci/smoke.sh
```

`ci/smoke.sh` is the canonical fixture matrix (`testdata/`); the
`smoke-test` workflow runs it on Linux x86_64, Linux arm64 and macOS, and
exercises the action contract through `uses: ./`.

To move to a new kepler-formal release, change the `kepler-formal-version`
default in `action.yml` (the workflow reads it from there), run the smoke
matrix, and tag a release (`v1.x.y`, then move `v1`).
