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

Before the engine runs, the parent checks the top module of each Verilog or
SystemVerilog design (or infers it when none is given) from the module
declarations and instantiations of the sources, so a wrong or ambiguous top is
reported as verdict top_not_found or top_ambiguous with the modules found.

Exit codes:
  0   equivalent (proved)
  1   different (LEC) or partially proved (SEC)
  2   inconclusive, unsupported or no result
  3   counterexample (SEC)
  64  invalid action inputs or config, top_not_found, top_ambiguous
  70  design loading or internal error
"""

from __future__ import annotations

import glob
import json
import os
import re
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

    verdict = "invalid_input"
    details: list | None = None


class TopError(UsageError):
    """The top module cannot be selected; verdict top_not_found or top_ambiguous."""

    def __init__(self, verdict: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.verdict = verdict
        self.details = details or {}


# --------------------------------------------------------------------------
# Top module selection (parent process, before the engine runs)
# --------------------------------------------------------------------------

_IDENT = r"(?:\\\S+|[A-Za-z_][\w$]*)"
_STRIP = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)
_ATTRIBUTE = re.compile(r"\(\*(?!\s*\))[^;]*?\*\)", re.S)
_INCLUDE = re.compile(r'`include\s+"([^"]+)"')
_DEFINE = re.compile(r"^[ \t]*`define\b(?:[^\n]*\\\n)*[^\n]*", re.M)
_MODULE = re.compile(
    r"\b(?:module|macromodule)\s+(?:(?:automatic|static)\s+)?(`?" + _IDENT + r")"
)
_ENDMODULE = re.compile(r"\bendmodule\b")
_TOKEN = re.compile(_IDENT)


def _strip_comments(text: str, keep_strings: bool = False) -> str:
    def replace(match: re.Match) -> str:
        if match.group(0).startswith('"'):
            return match.group(0) if keep_strings else '""'
        return " "

    return _STRIP.sub(replace, text)


def _read_with_includes(path: Path, search: list[Path], seen: set[Path]) -> str:
    """Return the text of a source file with its `include files inlined.

    Includes are looked up next to the including file, then in the
    directories of the design files, like the frontend's default include
    path. Missing includes are ignored: the frontend reports them.
    """
    seen.add(path)
    text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"), True)

    def inline(match: re.Match) -> str:
        name = match.group(1)
        for directory in [path.parent, *search]:
            candidate = (directory / name).resolve()
            if candidate.is_file() and candidate not in seen:
                return "\n" + _read_with_includes(candidate, search, seen) + "\n"
        return " "

    # Inline the includes, then drop the string literals.
    return _strip_comments(_INCLUDE.sub(inline, text))


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _skip_group(text: str, pos: int, opening: str, closing: str) -> int:
    """Skip a balanced group starting at text[pos] == opening; -1 if unbalanced."""
    depth = 0
    while pos < len(text):
        char = text[pos]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return -1


def _is_instance(body: str, pos: int) -> bool:
    """True when a module name ending at body[pos] starts an instantiation:
    `name [#(params)] instance [ranges] (`.
    """
    pos = _skip_space(body, pos)
    if body.startswith("#", pos):
        pos = _skip_space(body, pos + 1)
        if body.startswith("(", pos):
            pos = _skip_group(body, pos, "(", ")")
            if pos < 0:
                return False
        else:
            token = _TOKEN.match(body, pos) or re.compile(r"[\w.']+").match(body, pos)
            if not token:
                return False
            pos = token.end()
        pos = _skip_space(body, pos)
    token = _TOKEN.match(body, pos)
    if not token:
        return False
    pos = _skip_space(body, token.end())
    while body.startswith("[", pos):
        pos = _skip_group(body, pos, "[", "]")
        if pos < 0:
            return False
        pos = _skip_space(body, pos)
    return body.startswith("(", pos)


def _name(token: str) -> str:
    return token[1:] if token.startswith("\\") else token


def scan_verilog(files: list[str]) -> dict:
    """Find the modules of a Verilog/SystemVerilog source set and what they instantiate.

    Returns {"modules": [names in declaration order], "children": {module:
    [instantiated modules of the set]}, "opaque": bool}. The scan is
    lexical: comments, strings, attributes and `define bodies are skipped and
    `include files are inlined. It is opaque when a module name comes from a
    macro, in which case the caller leaves top selection to the frontend.
    """
    search = list(dict.fromkeys(Path(f).resolve().parent for f in files))
    bodies: dict[str, str] = {}
    opaque = False
    for name in files:
        text = _read_with_includes(Path(name).resolve(), search, set())
        text = _DEFINE.sub(" ", text)
        text = _ATTRIBUTE.sub(" ", text)
        for match in _MODULE.finditer(text):
            token = match.group(1)
            if token.startswith("`"):
                opaque = True
                continue
            end = _ENDMODULE.search(text, match.end())
            body = text[match.end(): end.start() if end else len(text)]
            bodies.setdefault(_name(token), body)

    names = set(bodies)
    children: dict[str, list[str]] = {}
    for module, body in bodies.items():
        found: list[str] = []
        for token in _TOKEN.finditer(body):
            child = _name(token.group(0))
            if child == module or child not in names or child in found:
                continue
            before = body[token.start() - 1] if token.start() else " "
            if before in ".$`'" or before.isalnum() or before == "_":
                continue
            if _is_instance(body, token.end()):
                found.append(child)
        children[module] = found
    return {"modules": list(bodies), "children": children, "opaque": opaque}


def top_candidates(scan: dict) -> list[str]:
    """Modules that no other module of the set instantiates, in declaration order."""
    instantiated = {child for kids in scan["children"].values() for child in kids}
    return [m for m in scan["modules"] if m not in instantiated]


def _names(items: list[str]) -> str:
    return ", ".join(f"`{item}`" for item in items) if items else "none"


def select_top(
    files: list[str], fmt: str, requested: str | None, label: str, input_name: str
) -> tuple[str | None, str]:
    """Check or infer the top module of one design.

    Returns (top, source) where source is "input" (given and found),
    "inferred" (the single module nobody instantiates) or "frontend" (the
    format or the sources cannot be scanned; the frontend decides).
    """
    if fmt not in ("verilog", "systemverilog"):
        return (requested, "input") if requested else (None, "frontend")
    scan = scan_verilog(files)
    if scan["opaque"]:
        return (requested, "input") if requested else (None, "frontend")
    modules = scan["modules"]
    candidates = top_candidates(scan)
    details = {
        "design": label,
        "input": input_name,
        "requested_top": requested,
        "modules": modules,
        "candidates": candidates,
    }
    if requested:
        if requested in modules:
            return requested, "input"
        raise TopError(
            "top_not_found",
            f"{label}: top module `{requested}` (from `{input_name}`) is not defined in "
            f"the {label} files. Modules found: {_names(modules)}; top-level "
            f"candidates (not instantiated by another module): {_names(candidates)}.",
            details,
        )
    if len(candidates) == 1:
        return candidates[0], "inferred"
    if not modules:
        raise TopError(
            "top_not_found",
            f"{label}: no module is defined in the {label} files, so no top module "
            f"can be selected.",
            details,
        )
    if not candidates:
        raise TopError(
            "top_not_found",
            f"{label}: every module is instantiated by another one (modules: "
            f"{_names(modules)}), so no top module can be inferred. Set `{input_name}`.",
            details,
        )
    raise TopError(
        "top_ambiguous",
        f"{label}: several modules are not instantiated by any other module: "
        f"{_names(candidates)}. Set `{input_name}` to choose the top module.",
        details,
    )


def resolve_tops(request: dict) -> None:
    """Check the requested tops and infer the missing ones, for both designs.

    Sets request["tops"] and request["top_sources"]; raises one TopError
    covering both designs when either cannot be resolved.
    """
    errors: list[TopError] = []
    sources: list[str] = []
    for index in (0, 1):
        label = f"design{index + 1}"
        if request["sv_flists"][index]:
            # The file list may add files and options the scan cannot see.
            sources.append("input" if request["tops"][index] else "frontend")
            continue
        try:
            top, source = select_top(
                request["designs"][index],
                request["formats"][index],
                request["tops"][index],
                label,
                request["top_inputs"][index],
            )
        except TopError as error:
            errors.append(error)
            sources.append("error")
            continue
        request["tops"][index] = top
        sources.append(source)
    request["top_sources"] = sources
    if errors:
        error = TopError(
            errors[0].verdict,
            "\n".join(str(e) for e in errors),
            None,
        )
        error.details = [e.details for e in errors]
        raise error


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
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        raise UsageError(f"{label}: cannot split `{text}` into paths: {error}") from None
    if not tokens:
        raise UsageError(f"{label} is empty")
    files: list[str] = []
    for token in tokens:
        pattern = token if Path(token).is_absolute() else str(base / token)
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches:
            raise UsageError(
                f"{label}: no file matches `{token}` (relative to the workspace {base})"
            )
        regular = [match for match in matches if Path(match).is_file()]
        if not regular:
            raise UsageError(f"{label}: `{token}` matches only directories, no file")
        files.extend(str(Path(match).resolve()) for match in regular)
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
    top_inputs = []
    flists = []
    for index in (1, 2):
        fmt = formats[index - 1]
        key = {"verilog": "verilog", "systemverilog": "sv", "vhdl": "vhdl"}.get(fmt)
        tops.append(cfg.get(f"{key}_design{index}_top") if key else None)
        top_inputs.append(f"{key}_design{index}_top" if key else "top")
        flists.append(optional_path(f"sv_design{index}_flist"))

    return {
        "designs": designs,
        "formats": formats,
        "tops": tops,
        "top_inputs": top_inputs,
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
    if not design1 and not design2:
        raise UsageError("Give config-path, or both design1 and design2")
    for name, value in (("design1", design1), ("design2", design2)):
        if not value:
            raise UsageError(f"{name} is empty: give the files or globs of both designs")

    fmt = _format(env.get("INPUT_FORMAT") or "systemverilog", "format")
    top = env.get("INPUT_TOP", "").strip() or None
    tops = []
    top_inputs = []
    for index in (1, 2):
        own = env.get(f"INPUT_DESIGN{index}_TOP", "").strip()
        tops.append(own or top)
        top_inputs.append(f"design{index}-top" if own else "top")

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
        "top_inputs": top_inputs,
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
                raise TopError(
                    "top_not_found", f"design{index + 1}: top module `{top}` was not found"
                )
            return design

    design = db.getTopDesign()
    if design is None:
        raise TopError(
            "top_not_found",
            f"design{index + 1}: no top module was found after parsing; set the top input",
        )
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

    loaded_tops: list[str] = []

    def fail(exit_code: int, verdict: str, message: str) -> int:
        # The parent reports this message in the summary and result.json.
        print(f"error: {message}", file=sys.stderr, flush=True)
        Path(result_path).write_text(
            json.dumps(
                {"verdict": verdict, "exit_code": exit_code, "error": message,
                 "loaded_tops": loaded_tops},
                indent=2,
            )
        )
        return exit_code

    try:
        settings = VerificationOptions(**options)
        universe = naja.NLUniverse.create()
        designs = []
        for index in (0, 1):
            designs.append(_load_design(universe, naja, request, index))
            loaded_tops.append(designs[-1].getName())
            print(f"Top design {index + 1}: {loaded_tops[-1]}", flush=True)
        started = time.monotonic()
        result = verify_designs(designs[0], designs[1], options=settings)
        elapsed = time.monotonic() - started
    except UsageError as error:
        return fail(EXIT_USAGE, error.verdict, str(error))
    except (TypeError, ValueError) as error:
        return fail(EXIT_USAGE, "invalid_input", f"invalid verification option: {error}")
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        stage = "loading design " + str(len(loaded_tops) + 1) if len(loaded_tops) < 2 else "verification"
        return fail(EXIT_INTERNAL, "error", f"{stage} failed: {type(error).__name__}: {error}")

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
                "loaded_tops": loaded_tops,
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


def status_for(exit_code: int) -> str:
    """success or failure, like a GitHub Actions step outcome.

    Whether the engine ran is told by the verdict and the exit code (64 and
    70: no verification), not by the status.
    """
    return "success" if exit_code == EXIT_EQUIVALENT else "failure"


# Fields written by every version of the action; values are unchanged.
RESULT_FIELDS = (
    "verdict",
    "exit_code",
    "verification",
    "bound",
    "reason",
    "covered_outputs",
    "total_outputs",
    "proven_outputs",
    "elapsed_seconds",
)


def _tops(state: dict) -> list:
    tops = state.get("tops") or [None, None]
    return [top or None for top in tops]


def build_result(state: dict) -> dict:
    """The content of result.json for a finished run, verified or not."""
    exit_code = state["exit_code"]
    tops = _tops(state)
    sources = state.get("top_sources") or [None, None]
    result = {key: state.get(key) for key in RESULT_FIELDS}
    result["verdict"] = state.get("verdict")
    result["exit_code"] = exit_code
    result.update(
        {
            "status": status_for(exit_code),
            "mode": state.get("mode"),
            "design1_top": tops[0],
            "design2_top": tops[1],
            "top_source": sources[0] if sources[0] == sources[1] else list(sources),
            "reset_ports": [
                {"name": name, "active_value": bool(value)}
                for name, value in state.get("reset_ports") or []
            ],
            "reset_cycles": state.get("reset_cycles"),
            "error": state.get("error"),
            "error_details": state.get("error_details"),
            "kepler_formal_version": state["versions"]["kepler-formal"],
            "najaeda_version": state["versions"]["najaeda"],
        }
    )
    return result


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
    tops = _tops(state)
    sources = state.get("top_sources") or [None, None]
    if any(tops):
        shown = []
        for index in (0, 1):
            name = f"`{tops[index]}`" if tops[index] else "unresolved"
            note = " (inferred)" if sources[index] == "inferred" else ""
            shown.append(f"design{index + 1} {name}{note}")
        lines.append("- Top modules: " + ", ".join(shown))
        if tops[0] and tops[1] and tops[0] != tops[1]:
            lines.append(
                f"- Note: the top module names differ (`{tops[0]}` and `{tops[1]}`); "
                "ports are matched by name"
            )
    if state.get("mode") == "sec" or state.get("reset_ports"):
        ports = state.get("reset_ports") or []
        if ports:
            pairs = " ".join(f"{name}={int(bool(value))}" for name, value in ports)
            lines.append(
                f"- Reset bootstrap: {pairs} for {state.get('reset_cycles')} cycles"
            )
        else:
            lines.append("- Reset bootstrap: none")
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
        # Never leave the result of an earlier run in place.
        for stale in ("result.json", "result.txt", "summary.md"):
            (artifact_dir / stale).unlink(missing_ok=True)
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
        state["reset_ports"] = request["options"].get("sec_reset_ports") or []
        state["reset_cycles"] = request["options"].get("sec_reset_cycles")

        # Check or infer the top modules before the engine runs, so a wrong
        # top is reported as such instead of as a frontend failure.
        resolve_tops(request)
        state["tops"] = list(request["tops"])
        state["top_sources"] = request["top_sources"]
        for index, (top, source) in enumerate(zip(state["tops"], state["top_sources"])):
            log(f"Top module of design{index + 1}: {top or 'chosen by the frontend'}"
                + (" (inferred)" if source == "inferred" else ""))

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
        child_result = json.loads(result_path.read_text()) if result_path.is_file() else {}
        loaded = child_result.pop("loaded_tops", [])
        for index, name in enumerate(loaded[:2]):
            if not state["tops"][index]:
                state["tops"][index] = name
        if child_result and exit_code == child_result.get("exit_code"):
            state.update(child_result)
        elif exit_code == EXIT_USAGE:
            state["verdict"] = "invalid_input"
        else:
            state["verdict"] = "error"
            state["error"] = (
                f"kepler-formal stopped without a result (code {exit_code}); "
                "see kepler-formal.log in the artifacts"
            )
    except UsageError as error:
        state["exit_code"] = EXIT_USAGE
        state["verdict"] = error.verdict
        state["error"] = str(error)
        if error.details:
            state["error_details"] = error.details
        log(f"error: {error}")
    except Exception as error:  # noqa: BLE001
        state["exit_code"] = EXIT_INTERNAL
        state["verdict"] = "error"
        state["error"] = f"{type(error).__name__}: {error}"
        log(f"error: {state['error']}")
    finally:
        exit_code = state["exit_code"]
        status = status_for(exit_code)
        log(f"kepler-formal finished: {state.get('verdict')} (exit code {exit_code})")
        if log_handle:
            log_handle.close()

        summary = _summary(state)
        tops = _tops(state)
        if artifact_dir is not None and artifact_dir.is_dir():
            (artifact_dir / "summary.md").write_text(summary)
            (artifact_dir / "result.json").write_text(
                json.dumps(build_result(state), indent=2) + "\n"
            )
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
        _write_output("design1-top", tops[0] or "")
        _write_output("design2-top", tops[1] or "")
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
