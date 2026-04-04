# Unsupported SystemVerilog repro cases

These fixtures are intentionally not part of the repository smoke workflows.
They capture SystemVerilog constructs that still fail in the current
kepler-formal and Naja stack during local action validation.

Each config points at two identical designs so the current failure is isolated
to parsing or lowering support rather than an intentional logic mismatch.

The former DFF repros were promoted into the normal SystemVerilog smoke suite
after validation against the current `origin/fix4` default kepler-formal ref.

## Cases

- `configs/procedural-comb.yaml`
  - Repro for procedural combinational logic written with `always @*`.
  - Observed failure during local validation: unsupported sequential timing
    control / unsupported sequential block in module `top`: unable to resolve
    a single-bit clock net.

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
