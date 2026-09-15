# Kepler Formal Action — Agent Guide

This repository publishes the reusable GitHub Action that runs Kepler Formal against a caller-provided configuration. Preserve the public Action contract in `action.yml`: callers prepare their inputs, the action runs `kepler-formal --config`, artifacts are written inside the workspace, and mismatches fail the step.

## Packaging architecture

Nix is the single source of truth for the runtime contents; Docker/GHCR is the public GitHub Action delivery boundary.

- `flake.nix` consumes the exact `kepler-formal` revision recorded in `flake.lock` and creates the `x86_64-linux` OCI image with `dockerTools.buildLayeredImage`.
- Do not reintroduce a Dockerfile that independently installs apt packages or rebuilds Kepler Formal with CMake. That would create a second packaging definition that can drift from the upstream Nix package.
- Published-action consumers need Docker but do not need Nix. Remote refs are mapped to prebuilt GHCR tags by `run-action.sh`.
- Local `uses: ./` execution requires Docker and an `x86_64-linux` Nix builder. `build-image.sh` builds and loads the locked image, and `run-action.sh` reuses the image for repeated invocations at the same commit. A macOS host needs a configured Linux remote builder or must run the build in a Linux VM/container.
- `entrypoint.sh` owns the stable runtime contract: workspace path validation, log capture, outputs, step summary, mismatch handling, and upstream revision reporting.

## Reproducibility and release invariants

- `flake.lock`, not a branch name in a Dockerfile, selects the released Kepler Formal revision.
- The Kepler Formal flake's `nixpkgs` input is followed so the package and image use one dependency universe.
- Keep the public `keplertech` Cachix substituter and signing key in `flake.nix`; CI should download the upstream package closure instead of rebuilding it when the locked revision is published.
- A published GHCR image must be the exact local image that passed `ci/smoke-image.sh`. Never rebuild the image between smoke testing and `docker push`.
- Preserve immutable `sha-<12>` tags and semantic-version tags. Do not add a mutable `latest` tag.
- Keep publish runs globally serialized so concurrent releases cannot race while updating mutable `main`, major, or minor tags.
- Keep the GHCR package public so callers can pull it anonymously. Private-package support in `run-action.sh` is a fallback, not the intended public experience.
- Keep OCI source, action revision, upstream Kepler Formal revision, and release attestation metadata intact.

## Updating Kepler Formal

Use a candidate workflow override to test a branch, tag, or full commit without changing the lock. Once accepted:

```bash
nix flake update kepler-formal
nix flake metadata --json | jq -r '.locks.nodes."kepler-formal".locked.rev'
./build-image.sh kepler-formal-action:local
./ci/smoke-image.sh kepler-formal-action:local .artifacts/local .tmp/local
```

Commit `flake.lock` with the update. A release is not ready until the pinned Kepler Formal closure is available from the public Cachix cache; otherwise CI may fall back to a long source build.

## Workflow responsibilities

- `.github/workflows/smoke-test.yml`: verifies the composite Action contract via local `uses: ./` calls.
- `.github/workflows/candidate-image.yml`: builds a locked or temporarily overridden Nix image and runs the shared smoke suite.
- `.github/workflows/publish-image.yml`: builds once, runs the shared smoke suite, pushes that same image under all required tags, and attests its digest.
- `ci/smoke-image.sh`: canonical image-level smoke matrix. Add supported fixtures here so candidate and release coverage cannot diverge.

## Validation

Run the cheapest relevant checks first:

```bash
bash -n build-image.sh run-action.sh entrypoint.sh ci/smoke-image.sh
nix flake check --accept-flake-config --no-update-lock-file
./build-image.sh kepler-formal-action:local
./ci/smoke-image.sh kepler-formal-action:local .artifacts/local .tmp/local
```

The full image build currently targets `x86_64-linux`; the published Action remains Linux-only even though the upstream Kepler Formal flake also packages `aarch64-darwin`.

## Knowledge graph

`graphify-out/` is local and gitignored. If `graphify-out/graph.json` exists and the Graphify skill is available, query it before broad codebase searches, then verify conclusions against the source. After architectural changes, refresh an existing graph with `graphify . --update`. Do not create a full graph merely for a routine lookup unless the user asks for one.
