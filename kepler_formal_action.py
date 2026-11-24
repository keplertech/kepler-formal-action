#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 keplertech.io
#
# SPDX-License-Identifier: Apache-2.0

"""Run Kepler Formal for the GitHub Action through the kepler-formal Python package.

The PyPI package exposes the verification engine as a Python API and does not
ship the ``kepler-formal`` executable. This driver gives the action the same
contract the executable gives: it reads either a kepler-formal YAML config or
the action's direct inputs, loads both designs with NajaEDA, verifies them,
and maps the verdict to an exit code.

The script runs in two processes. The parent validates paths, tees all output
of the child to ``kepler-formal.log``, and writes the action outputs, the
artifacts and the step summary. The child loads the designs and runs the
verification, so a crash in native code is still reported as a failed step.

Exit codes:
  0   equivalent (proved)
  1   different (LEC) or partially proved (SEC)
  2   inconclusive, unsupported or no result
  3   counterexample (SEC)
  64  invalid action inputs or config
  70  design loading or internal error
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXIT_EQUIVALENT = 0
EXIT_DIFFERENT_LEC = 1
EXIT_INCONCLUSIVE = 2
EXIT_COUNTEREXAMPLE_SEC = 3
EXIT_USAGE = 64
EXIT_INTERNAL = 70

# The step summary has a 1 MiB limit; the full trace stays in kepler-formal.log.
SUMMARY_REASON_LIMIT = 30000

FORMAT_ALIASES = {
    "verilog": "verilog",
    "v": "verilog",
    "systemverilog": "systemverilog",
    "sv": "systemverilog",
    "vhdl": "vhdl",
    "vhd": "vhdl",
    "naja_if": "naja_if",
}

# Config keys the action maps onto the Python API. The executable accepts more;
# those are rejected with a clear message instead of being silently ignored.
SUPPORTED_CONFIG_KEYS = {
    "format",
    "design1_format",
    "design2_format",
    "verification",
    "max_k",
    "sec_engine",
    "sec_encoding",
    "solver",
    "sec_reset",
    "input_paths",
    "liberty_files",
    "verilog_preprocessing",
    "verilog_design1_top",
    "verilog_design2_top",
    "sv_design1_top",
    "sv_design2_top",
    "sv_design1_flist",
    "sv_design2_flist",
    "sv_single_unit",
    "vhdl_design1_top",
    "vhdl_design2_top",
    "set_as_boundary",
    "allow-boundary-mismatch",
    "report_skipped_pos",
    "learn_internal_relations",
    "learn_ineternal_relations",
    "allow_x_equality_in_internal_relations",
    "binary_relation_round",
    "latch_abstraction",
    "data_latch_model",
    "btor2_export_path",
    "dump_only",
    "log_level",
    "log_file",
}


class UsageError(Exception):
    """Invalid action inputs or config; reported with exit code 64."""


# --------------------------------------------------------------------------
# Request building (parent process)
# --------------------------------------------------------------------------


def _bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    raise UsageError(f"{label} must be true or false, got {value!r}")


def _positive_int(value: object, label: str) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        raise UsageError(f"{label} must be an integer, got {value!r}") from None
    if number <= 0:
        raise UsageError(f"{label} must be greater than zero, got {number}")
    return number


def _format(value: object, label: str) -> str:
    key = str(value).strip().lower()
    if key in {"python", "py", "sv2v"}:
        raise UsageError(
            f"{label} `{value}` needs the kepler-formal executable and is not "
            "supported by this action"
        )
    if key not in FORMAT_ALIASES:
        raise UsageError(
            f"{label} must be one of verilog, systemverilog, vhdl or naja_if, got {value!r}"
        )
    return FORMAT_ALIASES[key]


def resolve_in_workspace(workspace: Path, raw: str, label: str) -> Path:
    if not raw:
        raise UsageError(f"{label} is empty")
    path = Path(raw)
    resolved = (path if path.is_absolute() else workspace / path).resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise UsageError(f"{label} must stay inside {workspace}: {raw}")
    return resolved


def _design_path(base: Path, raw: str) -> str:
    path = Path(raw)
    return str((path if path.is_absolute() else base / path).resolve())


def _expand_globs(base: Path, text: str, label: str) -> list[str]:
    files: list[str] = []
    for token in shlex.split(text):
        pattern = token if Path(token).is_absolute() else str(base / token)
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches:
            raise UsageError(f"{label}: no file matches `{token}`")
        files.extend(str(Path(match).resolve()) for match in matches)
    return files


def _reset_ports(text: str) -> list[tuple[str, bool]]:
    ports = []
    for token in text.split():
        name, sep, value = token.partition("=")
        if not sep or not name:
            raise UsageError(f"reset-ports entries must be <port>=<0|1>, got {token!r}")
        ports.append((name, _bool(value, f"reset-ports `{name}`")))
    return ports


def request_from_config(config_path: Path, workspace: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise UsageError("PyYAML is required to read config-path") from None

    try:
        cfg = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as error:
        raise UsageError(f"Invalid YAML in {config_path}: {error}") from None
    if not isinstance(cfg, dict):
        raise UsageError(f"{config_path} must contain a YAML map")

    unknown = sorted(set(cfg) - SUPPORTED_CONFIG_KEYS)
    if unknown:
        raise UsageError(
            "Config options not supported by this action: " + ", ".join(unknown)
        )

    # Relative paths are resolved against the workspace, the directory the
    # executable is run from in a --config workflow.
    paths = cfg.get("input_paths")
    if not isinstance(paths, list) or len(paths) != 2:
        raise UsageError("input_paths must contain exactly two entries")
    designs = []
    for entry in paths:
        entries = entry if isinstance(entry, list) else [entry]
        if not entries or not all(isinstance(item, str) for item in entries):
            raise UsageError("input_paths entries must be file paths")
        designs.append([_design_path(workspace, item) for item in entries])

    shared_format = cfg.get("format")
    formats = []
    for index in (1, 2):
        per_design = cfg.get(f"design{index}_format")
        if shared_format is not None and per_design is not None:
            raise UsageError("Use either format or design1_format/design2_format")
        value = per_design if per_design is not None else shared_format
        formats.append(_format(value, "format") if value is not None else "verilog")

    reset_cycles = None
    reset_ports: list[tuple[str, bool]] = []
    if "sec_reset" in cfg:
        spec = cfg["sec_reset"]
        if not isinstance(spec, dict) or not isinstance(spec.get("ports"), list):
            raise UsageError("sec_reset must be a map with cycles and ports")
        reset_cycles = _positive_int(spec.get("cycles"), "sec_reset.cycles")
        for index, port in enumerate(spec["ports"]):
            if not isinstance(port, dict) or "name" not in port or "active_value" not in port:
                raise UsageError(f"sec_reset.ports[{index}] needs name and active_value")
            reset_ports.append(
                (str(port["name"]), _bool(port["active_value"], f"sec_reset.ports[{index}]"))
            )

    boundary = cfg.get("set_as_boundary", [])
    if not isinstance(boundary, list) or not all(
        isinstance(pair, list) and len(pair) == 2 for pair in boundary
    ):
        raise UsageError("set_as_boundary must be a list of [design1_path, design2_path] pairs")

    learn = cfg.get("learn_internal_relations", cfg.get("learn_ineternal_relations", True))

    def optional_path(key: str) -> str | None:
        return _design_path(workspace, cfg[key]) if cfg.get(key) else None

    tops = []
    flists = []
    for index in (1, 2):
        fmt = formats[index - 1]
        key = {"verilog": "verilog", "systemverilog": "sv", "vhdl": "vhdl"}.get(fmt)
        tops.append(cfg.get(f"{key}_design{index}_top") if key else None)
        flists.append(optional_path(f"sv_design{index}_flist"))

    return {
        "designs": designs,
        "formats": formats,
        "tops": tops,
        "sv_flists": flists,
        "sv_single_unit": _bool(cfg.get("sv_single_unit", False), "sv_single_unit"),
        "verilog_preprocessing": _bool(
            cfg.get("verilog_preprocessing", False), "verilog_preprocessing"
        ),
        "liberty_files": [_design_path(workspace, f) for f in cfg.get("liberty_files") or []],
        "options": {
            "mode": str(cfg.get("verification", "lec")).lower(),
            "solver": cfg.get("solver", "kissat"),
            "max_k": _positive_int(cfg["max_k"], "max_k") if "max_k" in cfg else None,
            "sec_engine": cfg.get("sec_engine"),
            "sec_encoding": cfg.get("sec_encoding"),
            "sec_reset_cycles": reset_cycles,
            "sec_reset_ports": reset_ports,
            "allow_boundary_mismatch": _bool(
                cfg.get("allow-boundary-mismatch", False), "allow-boundary-mismatch"
            ),
            "report_skipped_outputs": _bool(
                cfg.get("report_skipped_pos", False), "report_skipped_pos"
            ),
            "set_as_boundary": [tuple(pair) for pair in boundary],
            "learn_internal_relations": _bool(learn, "learn_internal_relations"),
            "allow_x_equality_in_internal_relations": _bool(
                cfg.get("allow_x_equality_in_internal_relations", True),
                "allow_x_equality_in_internal_relations",
            ),
            "binary_relation_round": _bool(
                cfg.get("binary_relation_round", True), "binary_relation_round"
            ),
            "latch_abstraction": _bool(cfg.get("latch_abstraction", True), "latch_abstraction"),
            "data_latch_model": _bool(cfg.get("data_latch_model", True), "data_latch_model"),
            "btor2_export_path": optional_path("btor2_export_path"),
            "btor2_dump_only": _bool(cfg.get("dump_only", False), "dump_only"),
            "log_level": cfg.get("log_level"),
        },
    }


def request_from_inputs(env: dict, workspace: Path) -> dict:
    design1 = env.get("INPUT_DESIGN1", "").strip()
    design2 = env.get("INPUT_DESIGN2", "").strip()
    if not design1 or not design2:
        raise UsageError("Give config-path, or both design1 and design2")

    fmt = _format(env.get("INPUT_FORMAT") or "systemverilog", "format")
    top = env.get("INPUT_TOP", "").strip() or None
    tops = [
        env.get("INPUT_DESIGN1_TOP", "").strip() or top,
        env.get("INPUT_DESIGN2_TOP", "").strip() or top,
    ]

    mode = (env.get("INPUT_MODE") or "lec").strip().lower()
    reset_ports = _reset_ports(env.get("INPUT_RESET_PORTS", ""))
    reset_cycles = None
    if reset_ports:
        reset_cycles = _positive_int(env.get("INPUT_RESET_CYCLES") or "4", "reset-cycles")

    max_k = env.get("INPUT_MAX_K", "").strip()
    liberty = env.get("INPUT_LIBERTY_FILES", "").strip()

    return {
        "designs": [
            _expand_globs(workspace, design1, "design1"),
            _expand_globs(workspace, design2, "design2"),
        ],
        "formats": [fmt, fmt],
        "tops": tops,
        "sv_flists": [None, None],
        "sv_single_unit": False,
        "verilog_preprocessing": False,
        "liberty_files": _expand_globs(workspace, liberty, "liberty-files") if liberty else [],
        "options": {
            "mode": mode,
            "max_k": _positive_int(max_k, "max-k") if max_k else None,
            "sec_engine": env.get("INPUT_SEC_ENGINE", "").strip() or None,
            "sec_encoding": env.get("INPUT_SEC_ENCODING", "").strip() or None,
            "sec_reset_cycles": reset_cycles,
            "sec_reset_ports": reset_ports,
        },
    }


# --------------------------------------------------------------------------
# Verification (child process)
# --------------------------------------------------------------------------


def _slang_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _load_design(universe, naja, request: dict, index: int):
    files = request["designs"][index]
    fmt = request["formats"][index]
    top = request["tops"][index]
    liberty = request["liberty_files"]
    label = f"design {index + 1}"

    if fmt == "naja_if":
        if len(files) != 1:
            raise UsageError(f"{label}: naja_if takes exactly one path")
        db = naja.NLDB.loadNajaIF(files[0])
        return db.getTopDesign()

    db = naja.NLDB.create(universe)
    if liberty:
        if fmt == "systemverilog":
            raise UsageError(
                "liberty_files with SystemVerilog input needs the kepler-formal "
                "executable and is not supported by this action"
            )
        db.loadLibertyPrimitives(liberty)

    print(f"Parsing {len(files)} {fmt} file(s) for {label}", flush=True)
    if fmt == "systemverilog":
        flist = request["sv_flists"][index]
        if top or flist or request["sv_single_unit"]:
            # Same slang command file the executable writes for --top.
            lines = []
            if top:
                lines.append(f"--top {top}")
            if request["sv_single_unit"]:
                lines.append("--single-unit")
            if flist:
                lines.append(f"-f {_slang_quote(flist)}")
            lines.extend(_slang_quote(f) for f in files)
            handle = tempfile.NamedTemporaryFile("w", suffix=".f", delete=False)
            with handle:
                handle.write("\n".join(lines) + "\n")
            db.loadSystemVerilog([], flist=handle.name)
        else:
            db.loadSystemVerilog(files)
    elif fmt == "vhdl":
        db.loadVHDL(files, top=top)
    else:
        db.loadGateLevelVerilog(
            files, preprocess_enabled=request["verilog_preprocessing"]
        )
        if top:
            library = db.getLibrary("DESIGN")
            design = library.getSNLDesign(top) if library else None
            if design is None:
                raise UsageError(f"Top module `{top}` was not found in {label}")
            return design

    design = db.getTopDesign()
    if design is None:
        raise UsageError(f"No top design was found after parsing {label}")
    return design


def run_child(request_path: str, result_path: str) -> int:
    request = json.loads(Path(request_path).read_text())
    try:
        from najaeda import naja
        from kepler_formal import VerificationOptions, verify_designs
    except ImportError as error:
        print(f"kepler-formal Python package is not installed: {error}", file=sys.stderr)
        return EXIT_INTERNAL

    options = {k: v for k, v in request["options"].items() if v is not None}
    options["sec_reset_ports"] = [tuple(p) for p in options.get("sec_reset_ports", [])]
    options["set_as_boundary"] = [tuple(p) for p in options.get("set_as_boundary", [])]
    options["log_file"] = request["engine_log"]

    try:
        settings = VerificationOptions(**options)
        universe = naja.NLUniverse.create()
        designs = [_load_design(universe, naja, request, i) for i in (0, 1)]
        for index, design in enumerate(designs):
            print(f"Top design {index + 1}: {design.getName()}", flush=True)
        started = time.monotonic()
        result = verify_designs(designs[0], designs[1], options=settings)
        elapsed = time.monotonic() - started
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except (TypeError, ValueError) as error:
        print(f"error: invalid verification option: {error}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL

    status = result.status.value
    if status == "equivalent":
        exit_code = EXIT_EQUIVALENT
    elif status == "different":
        exit_code = (
            EXIT_COUNTEREXAMPLE_SEC if result.verification == "sec" else EXIT_DIFFERENT_LEC
        )
    elif status == "partially_proved":
        exit_code = 1
    else:
        exit_code = EXIT_INCONCLUSIVE

    Path(result_path).write_text(
        json.dumps(
            {
                "verdict": status,
                "exit_code": exit_code,
                "verification": result.verification,
                "bound": result.bound,
                "reason": result.reason,
                "covered_outputs": result.covered_outputs,
                "total_outputs": result.total_outputs,
                "proven_outputs": result.proven_outputs,
                "elapsed_seconds": round(elapsed, 2),
            },
            indent=2,
        )
    )
    print(f"Verdict: {status} (exit {exit_code}) in {elapsed:.1f}s", flush=True)
    if result.reason:
        print(result.reason, flush=True)
    return exit_code


# --------------------------------------------------------------------------
# Orchestration (parent process)
# --------------------------------------------------------------------------


def _relative(workspace: Path, path: Path) -> str:
    return "." if path == workspace else str(path.relative_to(workspace))


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            if "\n" in value:
                handle.write(f"{name}<<__KF_EOF__\n{value}\n__KF_EOF__\n")
            else:
                handle.write(f"{name}={value}\n")


def _package_versions() -> dict:
    try:
        from importlib.metadata import version

        return {name: version(name) for name in ("kepler-formal", "najaeda")}
    except Exception:  # noqa: BLE001
        return {"kepler-formal": "unknown", "najaeda": "unknown"}


def _summary(state: dict) -> str:
    verdict = state.get("verdict", "error")
    icon = {"equivalent": "✅"}.get(verdict, "❌")
    lines = [
        "### Kepler Formal",
        "",
        f"{icon} **{verdict}** (exit code {state['exit_code']})",
        "",
        f"- Mode: {state.get('mode', 'unknown')}",
        f"- Design 1: {state.get('design1', 'unresolved')}",
        f"- Design 2: {state.get('design2', 'unresolved')}",
    ]
    if state.get("config"):
        lines.append(f"- Config: `{state['config']}`")
    if state.get("total_outputs"):
        lines.append(
            f"- Checked outputs: {state['covered_outputs']}/{state['total_outputs']}"
        )
    if "elapsed_seconds" in state:
        lines.append(f"- Verification time: {state['elapsed_seconds']} s")
    lines.append(
        f"- kepler-formal {state['versions']['kepler-formal']}, "
        f"najaeda {state['versions']['najaeda']}"
    )
    lines.append(f"- Artifacts: `{state.get('artifact_dir', 'unresolved')}`")
    if state.get("reason"):
        reason = state["reason"].rstrip()
        if len(reason) > SUMMARY_REASON_LIMIT:
            reason = (
                reason[:SUMMARY_REASON_LIMIT]
                + "\n... truncated, see kepler-formal.log in the artifacts"
            )
        lines += ["", "<details><summary>Details</summary>", "", "```", reason, "```", "", "</details>"]
    if state.get("error"):
        lines += ["", "```", state["error"].rstrip(), "```"]
    return "\n".join(lines) + "\n"


def _describe(files: list[str], workspace: Path) -> str:
    shown = []
    for name in files[:3]:
        path = Path(name)
        shown.append(
            f"`{_relative(workspace, path)}`" if workspace in path.parents else f"`{name}`"
        )
    more = f" and {len(files) - 3} more" if len(files) > 3 else ""
    return ", ".join(shown) + more


def run_parent() -> int:
    env = os.environ
    workspace = Path(env.get("GITHUB_WORKSPACE") or os.getcwd()).resolve()
    write_summary = (env.get("INPUT_WRITE_STEP_SUMMARY") or "true").lower() == "true"
    state: dict = {"exit_code": EXIT_USAGE, "versions": _package_versions()}

    artifact_dir: Path | None = None
    log_handle = None

    def log(message: str) -> None:
        print(message, flush=True)
        if log_handle:
            log_handle.write(message + "\n")
            log_handle.flush()

    try:
        artifact_dir = resolve_in_workspace(
            workspace, env.get("INPUT_ARTIFACT_DIR") or ".kepler-formal-action", "artifact-dir"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        state["artifact_dir"] = _relative(workspace, artifact_dir)
        log_handle = open(artifact_dir / "kepler-formal.log", "w", encoding="utf-8")
        log(f"Workspace: {workspace}")
        log(f"Artifact directory: {artifact_dir}")
        log(
            f"kepler-formal {state['versions']['kepler-formal']}, "
            f"najaeda {state['versions']['najaeda']}"
        )

        config_input = env.get("INPUT_CONFIG_PATH", "").strip()
        if config_input:
            if env.get("INPUT_DESIGN1", "").strip() or env.get("INPUT_DESIGN2", "").strip():
                raise UsageError("Give either config-path or design1/design2, not both")
            config_path = resolve_in_workspace(workspace, config_input, "config-path")
            if not config_path.is_file():
                raise UsageError(f"Config file not found: {config_path}")
            state["config"] = _relative(workspace, config_path)
            log(f"Config file: {config_path}")
            (artifact_dir / "config.yaml").write_text(config_path.read_text())
            request = request_from_config(config_path, workspace)
        else:
            request = request_from_inputs(env, workspace)

        state["mode"] = request["options"]["mode"]
        state["design1"] = _describe(request["designs"][0], workspace)
        state["design2"] = _describe(request["designs"][1], workspace)

        # The engine and the frontends write working files (miter logs,
        # diagnostics) to the current directory: keep them in the artifacts.
        work_dir = artifact_dir / "work"
        work_dir.mkdir(exist_ok=True)
        request["engine_log"] = str(artifact_dir / "kepler-formal-engine.log")
        request_path = artifact_dir / "request.json"
        result_path = artifact_dir / "result.json"
        result_path.unlink(missing_ok=True)
        request_path.write_text(json.dumps(request, indent=2))

        log("Running kepler-formal...")
        child = subprocess.Popen(
            [sys.executable, "-I", "-u", os.path.abspath(__file__), "--child",
             str(request_path), str(result_path)],
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        assert child.stdout is not None
        for line in child.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_handle.write(line)
        exit_code = child.wait()
        log_handle.flush()

        if exit_code < 0 or (exit_code not in (0, 1, 2, 3, EXIT_USAGE, EXIT_INTERNAL)):
            log(f"kepler-formal terminated abnormally (code {exit_code})")
            exit_code = EXIT_INTERNAL
        state["exit_code"] = exit_code
        if result_path.is_file():
            state.update(json.loads(result_path.read_text()))
        elif exit_code == EXIT_USAGE:
            state["verdict"] = "invalid_input"
        else:
            state["verdict"] = "error"
    except UsageError as error:
        state["exit_code"] = EXIT_USAGE
        state["verdict"] = "invalid_input"
        state["error"] = str(error)
        log(f"error: {error}")
    except Exception as error:  # noqa: BLE001
        state["exit_code"] = EXIT_INTERNAL
        state["verdict"] = "error"
        state["error"] = f"{type(error).__name__}: {error}"
        log(f"error: {state['error']}")
    finally:
        exit_code = state["exit_code"]
        status = "success" if exit_code == 0 else "failure"
        log(f"kepler-formal finished: {state.get('verdict')} (exit code {exit_code})")
        if log_handle:
            log_handle.close()

        summary = _summary(state)
        if artifact_dir is not None and artifact_dir.is_dir():
            (artifact_dir / "summary.md").write_text(summary)
            (artifact_dir / "result.txt").write_text(
                "\n".join(
                    [
                        f"status={status}",
                        f"verdict={state.get('verdict')}",
                        f"exit_code={exit_code}",
                        f"artifact_dir={state.get('artifact_dir', '')}",
                        f"config_path={state.get('config', '')}",
                        f"kepler_formal_version={state['versions']['kepler-formal']}",
                        f"najaeda_version={state['versions']['najaeda']}",
                    ]
                )
                + "\n"
            )
            _write_output("artifact-dir", state.get("artifact_dir", ""))
        _write_output("exit-code", str(exit_code))
        _write_output("status", status)
        _write_output("verdict", str(state.get("verdict")))
        _write_output("kepler-formal-version", state["versions"]["kepler-formal"])
        if write_summary and env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
                handle.write(summary)

    return state["exit_code"]


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--child":
        return run_child(argv[2], argv[3])
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return EXIT_USAGE
    return run_parent()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
