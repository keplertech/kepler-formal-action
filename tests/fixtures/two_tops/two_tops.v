// Two unrelated modules: neither instantiates the other, so there is no
// single top to pick.
module alpha (
    input  wire a_i,
    input  wire b_i,
    output wire y_o
);
  assign y_o = a_i & b_i;
endmodule

module beta (
    input  wire a_i,
    input  wire b_i,
    output wire y_o
);
  assign y_o = a_i | b_i;
endmodule
