// Base version: top module bar_old.
module bar_old (
    input  wire [3:0] a_i,
    input  wire [3:0] b_i,
    output wire [3:0] y_o
);
  assign y_o = a_i ^ b_i;
endmodule
