module top(
  input logic req,
  input logic mode,
  input logic enable,
  input logic [2:0] data,
  output logic [2:0] result,
  output logic valid,
  output logic parity
);
  logic left_term;
  logic right_term;
  logic mix_term;

  assign left_term = (data[0] ^ req) & enable;
  assign right_term = (data[2] | mode) ^ data[1];
  assign mix_term = left_term | right_term;

  assign result[0] = mix_term ^ data[0];
  assign result[1] = (data[1] & enable) | (req ^ mode);
  assign result[2] = (data[2] ^ req) | (left_term & mode);
  assign valid = mix_term & (enable | req);
  assign parity = ^{result, valid};
endmodule