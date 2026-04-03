# Unsupported SystemVerilog repro cases

These fixtures are intentionally not part of the repository smoke workflows.
They capture SystemVerilog constructs that were observed to fail in the current
kepler-formal and Naja stack during local action validation.

Each config points at two identical designs so the current failure is isolated
to parsing or lowering support rather than an intentional logic mismatch.

## Cases

- `configs/procedural-comb.yaml`
  - Repro for procedural combinational logic written with `always @*`.
  - Observed failure during local validation: unsupported sequential timing
    control / unsupported statement pattern for sequential lowering.
- `configs/dff-sync.yaml`
  - Repro for synchronous flip-flops written with `always @(posedge clk)`.
  - Observed failure during local validation: unsupported primitive type
    `PRIMITIVES.naja_dff`.
- `configs/dff-async-reset.yaml`
  - Repro for asynchronous reset flip-flops written with
    `always @(posedge clk or negedge rst_n)`.
  - Observed failure during local validation: unsupported primitive type
    `PRIMITIVES.naja_dffrn`.

## Suggested local invocation

```sh
docker run --rm \
  -v "$PWD":/github/workspace \
  -e GITHUB_WORKSPACE=/github/workspace \
  -e GITHUB_OUTPUT=/github/workspace/.tmp/github_output \
  -e GITHUB_STEP_SUMMARY=/github/workspace/.tmp/github_summary \
  kepler-formal-action-test \
  testdata/systemverilog/unsupported/configs/procedural-comb.yaml \
  .artifacts/unsupported-procedural-comb \
  true
```
