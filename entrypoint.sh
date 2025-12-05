#!/bin/bash
set -e

ROOT="/github/workspace"
DESIGN_HOME="$1"
DESIGN_CONFIG="$2"
#LIBRARY="$4"

export YOSYS_EXE="/yosys/bin/yosys"
export KEPLER_FORMAL_EXE="/kepler-formal/bin/kepler_formal"
export DESIGN_HOME="$ROOT/$DESIGN_HOME"
export DESIGN_CONFIG="$ROOT/$DESIGN_CONFIG"

echo "::group::Naja-Direct-Yosys"
echo "Naja Direct Yosys mode selected"
echo "Yosys executable: $YOSYS_EXE"
echo "Yosys version: $($YOSYS_EXE -V)"
echo "Kepler Formal executable: $KEPLER_FORMAL_EXE"
echo "Design config: $DESIGN_CONFIG"
mkdir naja-run
cd naja-run
#export SYNTH_ROOT="$ROOT/najaeda-or/flow"
#export SYNTH_ROOT="$ROOT/$SYNTH_ROOT"
$YOSYS_EXE -c "$DESIGN_CONFIG"
VERILOG_FILE="naja_netlist.v"
echo "Verilog file: $VERILOG_FILE"
echo "::endgroup::"

echo "::group::Launching-Najaeda"
python3 /najaeda_scripts/count_leaves.py --primitives_mode="xilinx" --verilog "$VERILOG_FILE"
cp design.stats /github/workspace/design.stats
echo "::endgroup::"

echo "Done!"
