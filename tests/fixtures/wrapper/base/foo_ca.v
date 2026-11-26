// Wrapper named like the py2hwsw "_ca" variants: the top is foo_ca, not foo.
module foo_ca (
    input  wire       clk_i,
    input  wire       arst_i,
    input  wire       cke_i,
    input  wire [3:0] data_i,
    output wire [3:0] data_o
);
  foo_reg reg0 (
      .clk_i (clk_i),
      .arst_i(arst_i),
      .cke_i (cke_i),
      .data_i(data_i),
      .data_o(data_o)
  );
endmodule
