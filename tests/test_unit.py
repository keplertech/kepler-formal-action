# SPDX-FileCopyrightText: 2026 keplertech.io
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests of the plain logic of kepler_formal_action.py.

They need neither kepler-formal nor najaeda: top selection, glob expansion,
argument building, result.json and the step summary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kepler_formal_action as kfa

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


def sources(*parts: str) -> list[str]:
    return sorted(str(p) for part in parts for p in (FIXTURES / part).glob("*.v"))


def inputs(**values: str) -> dict:
    env = {}
    for name, value in values.items():
        env["INPUT_" + name.upper()] = value
    return env


# --------------------------------------------------------------------------
# Finding the top module
# --------------------------------------------------------------------------


class TestScan:
    def test_wrapper_hierarchy(self):
        scan = kfa.scan_verilog(sources("wrapper/base"))
        assert sorted(scan["modules"]) == ["foo_ca", "foo_reg"]
        assert scan["children"]["foo_ca"] == ["foo_reg"]
        assert kfa.top_candidates(scan) == ["foo_ca"]

    def test_two_unrelated_modules(self):
        scan = kfa.scan_verilog(sources("two_tops"))
        assert kfa.top_candidates(scan) == ["alpha", "beta"]

    def test_parameterized_instance_comments_and_strings(self, tmp_path):
        path = write(
            tmp_path / "a.v",
            """
            // module fake_in_comment (input a);
            /* leaf l0 (.a(a)); */
            module leaf #(parameter W = 2) (input [W-1:0] a, output [W-1:0] y);
              assign y = ~a;
            endmodule
            module mid (input [3:0] a, output [3:0] y);
              initial $display("leaf fake (");
              leaf #(.W(4), .X((1+2))) u_leaf [1:0] (.a(a), .y(y));
            endmodule
            module root (input [3:0] a, output [3:0] y);
              always @(*) begin end
              (* keep *) mid m (.a(a), .y(y));
            endmodule
            """,
        )
        scan = kfa.scan_verilog([path])
        assert scan["modules"] == ["leaf", "mid", "root"]
        assert kfa.top_candidates(scan) == ["root"]

    def test_instance_in_included_file(self, tmp_path):
        write(tmp_path / "inc" / "leaf_inst.vs", "leaf u0 (.a(a), .y(y));\n")
        leaf = write(tmp_path / "leaf.v", "module leaf(input a, output y); assign y = a; endmodule\n")
        top = write(
            tmp_path / "top.v",
            'module top(input a, output y);\n`include "inc/leaf_inst.vs"\nendmodule\n',
        )
        scan = kfa.scan_verilog([leaf, top])
        assert kfa.top_candidates(scan) == ["top"]

    def test_macro_module_name_makes_scan_opaque(self, tmp_path):
        path = write(tmp_path / "m.v", "module `NAME (input a); endmodule\n")
        assert kfa.scan_verilog([path])["opaque"] is True

    def test_self_instantiation_ignored_and_no_module(self, tmp_path):
        path = write(tmp_path / "e.v", "// nothing here\n")
        scan = kfa.scan_verilog([path])
        assert scan["modules"] == []
        assert kfa.top_candidates(scan) == []


class TestSelectTop:
    def test_requested_top_found(self):
        top, source = kfa.select_top(sources("wrapper/base"), "systemverilog", "foo_ca", "design1", "top")
        assert (top, source) == ("foo_ca", "input")

    def test_requested_top_missing(self):
        with pytest.raises(kfa.TopError) as raised:
            kfa.select_top(sources("wrapper/base"), "systemverilog", "foo", "design1", "top")
        error = raised.value
        assert error.verdict == "top_not_found"
        message = str(error)
        assert "`foo`" in message and "design1" in message and "`top`" in message
        assert "foo_ca" in message and "foo_reg" in message
        assert error.details["modules"] == ["foo_ca", "foo_reg"]
        assert error.details["candidates"] == ["foo_ca"]

    def test_inferred(self):
        assert kfa.select_top(sources("wrapper/head"), "systemverilog", None, "design2", "top") == (
            "foo_ca",
            "inferred",
        )

    def test_ambiguous(self):
        with pytest.raises(kfa.TopError) as raised:
            kfa.select_top(sources("two_tops"), "systemverilog", None, "design1", "top")
        assert raised.value.verdict == "top_ambiguous"
        assert "alpha" in str(raised.value) and "beta" in str(raised.value)
        assert raised.value.details["candidates"] == ["alpha", "beta"]

    def test_no_module(self, tmp_path):
        path = write(tmp_path / "e.v", "\n")
        with pytest.raises(kfa.TopError) as raised:
            kfa.select_top([path], "verilog", None, "design1", "top")
        assert raised.value.verdict == "top_not_found"
        assert "no module" in str(raised.value)

    def test_vhdl_is_left_to_the_frontend(self, tmp_path):
        path = write(tmp_path / "a.vhd", "entity a is end;\n")
        assert kfa.select_top([path], "vhdl", None, "design1", "top") == (None, "frontend")
        assert kfa.select_top([path], "vhdl", "a", "design1", "top") == ("a", "input")

    def test_opaque_source_is_left_to_the_frontend(self, tmp_path):
        path = write(tmp_path / "m.v", "module `NAME (input a); endmodule\n")
        assert kfa.select_top([path], "systemverilog", "x", "design1", "top") == ("x", "input")
        assert kfa.select_top([path], "systemverilog", None, "design1", "top") == (None, "frontend")

    def test_resolve_tops_reports_both_designs(self):
        request = {
            "designs": [sources("wrapper/base"), sources("wrapper/head")],
            "formats": ["systemverilog", "systemverilog"],
            "tops": ["foo", "foo"],
            "top_inputs": ["top", "top"],
            "sv_flists": [None, None],
        }
        with pytest.raises(kfa.TopError) as raised:
            kfa.resolve_tops(request)
        assert "design1" in str(raised.value) and "design2" in str(raised.value)

    def test_resolve_tops_infers_each_side(self):
        request = {
            "designs": [sources("renamed/base"), sources("renamed/head")],
            "formats": ["systemverilog", "systemverilog"],
            "tops": [None, None],
            "top_inputs": ["top", "top"],
            "sv_flists": [None, None],
        }
        kfa.resolve_tops(request)
        assert request["tops"] == ["bar_old", "bar_new"]
        assert request["top_sources"] == ["inferred", "inferred"]

    def test_resolve_tops_skips_flist(self):
        request = {
            "designs": [sources("two_tops"), sources("two_tops")],
            "formats": ["systemverilog", "systemverilog"],
            "tops": [None, None],
            "top_inputs": ["sv_design1_top", "sv_design2_top"],
            "sv_flists": ["/some/files.f", "/some/files.f"],
        }
        kfa.resolve_tops(request)
        assert request["tops"] == [None, None]
        assert request["top_sources"] == ["frontend", "frontend"]


# --------------------------------------------------------------------------
# Globs and argument building
# --------------------------------------------------------------------------


class TestInputs:
    def test_glob_no_match_names_the_input(self):
        with pytest.raises(kfa.UsageError) as raised:
            kfa._expand_globs(REPO, "tests/fixtures/nothing/*.v", "design1")
        message = str(raised.value)
        assert message.startswith("design1:")
        assert "tests/fixtures/nothing/*.v" in message

    def test_glob_matching_only_directories(self):
        with pytest.raises(kfa.UsageError) as raised:
            kfa._expand_globs(REPO, "tests/fixtures/wrapper/*", "design2")
        assert "design2" in str(raised.value) and "director" in str(raised.value)

    def test_glob_expands_sorted_files(self):
        files = kfa._expand_globs(REPO, "tests/fixtures/wrapper/base/*.v", "design1")
        assert [Path(f).name for f in files] == ["foo_ca.v", "foo_reg.v"]

    def test_empty_design_input_is_named(self):
        env = inputs(design1="", design2="tests/fixtures/wrapper/head/*.v")
        with pytest.raises(kfa.UsageError, match="design1 is empty"):
            kfa.request_from_inputs(env, REPO)

    @pytest.mark.parametrize("value", ["", "   ", "\n"])
    def test_empty_reset_ports_means_no_bootstrap(self, value):
        assert kfa._reset_ports(value) == []
        env = inputs(
            design1="tests/fixtures/wrapper/base/*.v",
            design2="tests/fixtures/wrapper/head/*.v",
            mode="sec",
            reset_ports=value,
            reset_cycles="4",
        )
        options = kfa.request_from_inputs(env, REPO)["options"]
        assert options["sec_reset_ports"] == []
        assert options["sec_reset_cycles"] is None

    def test_reset_ports_parsed(self):
        env = inputs(
            design1="tests/fixtures/wrapper/base/*.v",
            design2="tests/fixtures/wrapper/head/*.v",
            reset_ports="arst_i=1 cke_i=0",
            reset_cycles="4",
        )
        options = kfa.request_from_inputs(env, REPO)["options"]
        assert options["sec_reset_ports"] == [("arst_i", True), ("cke_i", False)]
        assert options["sec_reset_cycles"] == 4

    def test_bad_reset_port_entry(self):
        with pytest.raises(kfa.UsageError, match="reset-ports"):
            kfa._reset_ports("arst_i")

    def test_top_inputs_recorded(self):
        env = inputs(
            design1="tests/fixtures/wrapper/base/*.v",
            design2="tests/fixtures/wrapper/head/*.v",
            top="foo",
            design2_top="foo_ca",
        )
        request = kfa.request_from_inputs(env, REPO)
        assert request["tops"] == ["foo", "foo_ca"]
        assert request["top_inputs"] == ["top", "design2-top"]


# --------------------------------------------------------------------------
# result.json and summary
# --------------------------------------------------------------------------


def verified_state(**extra) -> dict:
    state = {
        "exit_code": 0,
        "versions": {"kepler-formal": "1.0.0", "najaeda": "0.7.33"},
        "artifact_dir": ".kepler-formal-action",
        "mode": "sec",
        "design1": "`a.v`",
        "design2": "`b.v`",
        "verdict": "equivalent",
        "verification": "sec",
        "bound": 1,
        "reason": None,
        "covered_outputs": 4,
        "total_outputs": 4,
        "proven_outputs": 4,
        "elapsed_seconds": 0.02,
        "tops": ["foo_ca", "foo_ca"],
        "top_sources": ["inferred", "inferred"],
        "reset_ports": [("arst_i", True)],
        "reset_cycles": 4,
    }
    state.update(extra)
    return state


class TestResult:
    def test_existing_fields_kept(self):
        result = kfa.build_result(verified_state())
        for key, value in {
            "verdict": "equivalent",
            "exit_code": 0,
            "verification": "sec",
            "bound": 1,
            "reason": None,
            "covered_outputs": 4,
            "total_outputs": 4,
            "proven_outputs": 4,
            "elapsed_seconds": 0.02,
        }.items():
            assert result[key] == value

    def test_new_fields(self):
        result = kfa.build_result(verified_state())
        assert result["status"] == "success"
        assert result["design1_top"] == "foo_ca"
        assert result["design2_top"] == "foo_ca"
        assert result["top_source"] == "inferred"
        assert result["reset_ports"] == [{"name": "arst_i", "active_value": True}]
        assert result["reset_cycles"] == 4
        assert result["error"] is None
        json.dumps(result)

    def test_error_result(self):
        state = {
            "exit_code": 64,
            "versions": {"kepler-formal": "1.0.0", "najaeda": "0.7.33"},
            "verdict": "top_not_found",
            "error": "design1: top module `foo` ...",
            "error_details": [{"design": "design1", "modules": ["foo_ca"]}],
        }
        result = kfa.build_result(state)
        assert result["status"] == "failure"
        assert result["verdict"] == "top_not_found"
        assert result["exit_code"] == 64
        assert result["error"].startswith("design1")
        assert result["error_details"][0]["modules"] == ["foo_ca"]
        assert result["design1_top"] is None
        assert result["covered_outputs"] is None

    @pytest.mark.parametrize(
        "code,status",
        [(0, "success"), (1, "failure"), (2, "failure"), (3, "failure"), (64, "failure"), (70, "failure")],
    )
    def test_status(self, code, status):
        assert kfa.status_for(code) == status

    def test_summary_tops_and_reset(self):
        summary = kfa._summary(verified_state())
        assert "`foo_ca`" in summary and "inferred" in summary
        assert "Reset bootstrap: arst_i=1 for 4 cycles" in summary

    def test_summary_no_reset(self):
        summary = kfa._summary(verified_state(reset_ports=[], reset_cycles=None))
        assert "Reset bootstrap: none" in summary

    def test_summary_different_top_names(self):
        summary = kfa._summary(verified_state(tops=["bar_old", "bar_new"]))
        assert "`bar_old`" in summary and "`bar_new`" in summary and "differ" in summary
