module top(
  input logic clk,
  input logic rst_n,
  input logic req,
  input logic mode,
  input logic [3:0] data,
  output logic [3:0] result,
  output logic valid
);
  logic [3:0] state_q;

  always @(posedge clk) begin
    if (!rst_n) begin
      state_q <= 4'b0000;
      valid <= 1'b0;
    end else begin
      state_q <= req ? (mode ? (state_q ^ data) : (state_q + data)) : state_q;
      valid <= req;
    end
  end

  assign result = state_q;
endmodule