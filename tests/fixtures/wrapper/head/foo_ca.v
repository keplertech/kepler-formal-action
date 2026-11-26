// Same behavior as base, written with an intermediate wire.
module foo_ca (
    input  wire       clk_i,
    input  wire       arst_i,
    input  wire       cke_i,
    input  wire [3:0] data_i,
    output wire [3:0] data_o
);
  wire [3:0] q;
  foo_reg reg0 (
      .clk_i (clk_i),
      .arst_i(arst_i),
      .cke_i (cke_i),
      .data_i(data_i),
      .data_o(q)
  );
  assign data_o = q;
endmodule
