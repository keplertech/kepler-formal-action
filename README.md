# kepler-formal-action

Reusable GitHub Action for running kepler-formal against two caller-prepared netlists in CI.

The action does not decide what latest and reference mean. The calling workflow prepares both netlists in the checked-out workspace, writes a kepler-formal YAML config that points at them, and then invokes this action with that config path.

## What it does

- Builds a pinned kepler-formal release inside the action image with the upstream CMake flow.
- Runs kepler-formal in `--config` mode.
- Captures the tool log, copies the config file into an artifact directory, and exposes that directory as an action output.
- Fails the step when kepler-formal reports a mismatch or another execution error.

## Constraints

- Docker container actions run on Linux runners. Use `ubuntu-latest` or another Linux GitHub-hosted runner.
- The action expects a kepler-formal YAML config file inside the checked-out workspace.
- The action is config-driven. It does not infer formats, discover liberty files, or check out a reference revision for you.
- Python primitive loaders are not validated in v1. The supported and tested path is plain config-driven execution with Verilog fixtures and optional `.lib` / `.lib.gz` support files.

## Inputs

### `config-path`

Required. Path to a kepler-formal YAML config file inside the workspace.

### Input `artifact-dir`

Optional. Workspace-relative directory where the action writes logs and copied inputs.

Default: `.kepler-formal-action`

### `write-step-summary`

Optional. Whether to append a short Markdown summary to the GitHub step summary.

Default: `true`

## Outputs

### `artifact-dir`

Workspace-relative path containing the generated artifacts.

### `exit-code`

Exit code returned by kepler-formal.

### `status`

`success` or `failure`.

## Artifact contents

The action writes these files into the artifact directory:

- `kepler-formal.log`: combined stdout and stderr from the action and the tool.
- `config.yaml`: a copy of the config file that was executed.
- `result.txt`: status, exit code, config path, artifact path, and the pinned upstream ref.
- `summary.md`: the same short summary that can be appended to the GitHub step summary.

If your kepler-formal config writes additional files such as CNF exports, those remain in the workspace at the paths defined by your config. Upload them separately if you need them preserved.

## Example usage

This example assumes your workflow can generate or collect two netlists, one from the current checkout and one from a reference checkout.

```yaml
name: compare-netlists

on:
  pull_request:
  workflow_dispatch:

jobs:
  compare:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout latest
        uses: actions/checkout@v4
        with:
          path: latest

      - name: Checkout reference
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          path: reference

      - name: Generate kepler-formal config
        run: |
          mkdir -p ci
          cat > ci/kepler-formal.yaml <<'EOF'
          format: verilog
          input_paths:
            - latest/out/top.netlist.v
            - reference/out/top.netlist.v
          liberty_files:
            - latest/lib/stdcells.lib.gz
          EOF

      - name: Run kepler-formal
        id: kepler
        uses: keplertech/kepler-formal-action@v1
        with:
          config-path: ci/kepler-formal.yaml
          artifact-dir: .artifacts/kepler-formal

      - name: Upload action artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: kepler-formal
          path: ${{ steps.kepler.outputs.artifact-dir }}
          if-no-files-found: warn
```

## Config expectations

The action passes your config directly to kepler-formal:

```yaml
format: verilog
input_paths:
  - latest/out/top.netlist.v
  - reference/out/top.netlist.v
```

For multi-file designs, use nested `input_paths` lists exactly as kepler-formal expects.

## Runtime strategy

The action image builds kepler-formal from the upstream git ref configured in the Dockerfile. The runtime image bundles the installed binary and required Naja shared libraries, and keeps the remaining runtime dependencies explicit in the image.

When the action is consumed from another repository, the wrapper pulls the published GHCR image that matches the action ref:

- `uses: keplertech/kepler-formal-action@main` pulls `ghcr.io/keplertech/kepler-formal-action:main`
- `uses: keplertech/kepler-formal-action@v1` pulls `ghcr.io/keplertech/kepler-formal-action:v1`
- `uses: keplertech/kepler-formal-action@v1.2.3` pulls `ghcr.io/keplertech/kepler-formal-action:v1.2.3`
- `uses: keplertech/kepler-formal-action@<published commit sha>` pulls `ghcr.io/keplertech/kepler-formal-action:sha-<first12>`

Remote consumption should use `main`, a published release tag, or a published commit SHA. Arbitrary branch refs still do not have matching GHCR tags.

If you want other repositories to pull the GHCR image anonymously, set the package visibility to `Public` in GitHub Packages. GHCR packages are private on first publish by default. If you keep the package private, grant the caller repository read access to the package and ensure its workflow token has `packages: read`.

When the action is used locally through `uses: ./`, it builds the repository Dockerfile on the runner and executes that local image. That keeps pull-request smoke tests validating the current Dockerfile instead of the last published release image.

## Candidate validation and GHCR release

This repository now uses two container validation lanes in addition to the normal action smoke test:

- `.github/workflows/smoke-test.yml` validates the action contract through `uses: ./`, which makes the composite wrapper build and run the repository Dockerfile locally on the runner.
- `.github/workflows/candidate-image.yml` builds a temporary image directly from the repository Dockerfile for pull requests and manual dispatch. Manual runs can override `KEPLER_FORMAL_COMMIT` with a specific upstream git ref or commit hash without editing the Dockerfile.
- `.github/workflows/publish-image.yml` rebuilds and smoke-tests the committed image, then publishes it to `ghcr.io/<owner>/kepler-formal-action` on pushes to `main`, on semantic version tags, or by manual dispatch.

Published images always receive these tags:

- the requested publish tag, for example `main` or `v1.2.3`
- a commit-derived tag, for example `sha-abcdef123456`

Stable semantic-version releases also receive floating compatibility tags:

- the major tag, for example `v1`
- the minor tag, for example `v1.2`

The workflow does not publish a mutable `latest` tag.

## Local validation in this repo

This repository organizes smoke fixtures by input format and keeps the netlist bodies under format-specific `cases/` directories with explicit case names:

- `testdata/verilog/cases/equivalent-basic/`: basic Verilog equivalence case
- `testdata/verilog/cases/mismatch-basic/`: basic Verilog mismatch case
- `testdata/systemverilog/cases/dff-sync/`: synchronous D flip-flop equivalence case
- `testdata/systemverilog/cases/dff-async-reset/`: asynchronous reset D flip-flop equivalence case
- `testdata/systemverilog/cases/operator-mismatch-basic/`: SystemVerilog operator-style `|` versus `&` mismatch case
- `testdata/systemverilog/cases/complex-comb-mismatch/`: richer SystemVerilog combinational datapath mismatch with multiple intermediate signals and conditional logic

See `testdata/` for the fixture netlists and configs.

## Updating the embedded kepler-formal version

1. Change `KEPLER_FORMAL_COMMIT` in the Dockerfile to the upstream git ref or commit hash you want to build.
2. Re-run `.github/workflows/smoke-test.yml` to confirm the action still behaves correctly through `uses: ./`.
3. Optionally run `.github/workflows/candidate-image.yml` with a `kepler-formal-commit` override if you want to validate a candidate upstream ref before changing the Dockerfile.
4. After the change is merged, publish the release image through a semantic version tag or `.github/workflows/publish-image.yml` manual dispatch.
5. For reproducible releases, replace branch names with a commit SHA before publishing.
6. Update this README if runtime assumptions or supported formats changed.
