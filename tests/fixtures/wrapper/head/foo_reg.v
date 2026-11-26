// Register with asynchronous reset and clock enable.
module foo_reg (
    input  wire       clk_i,
    input  wire       arst_i,
    input  wire       cke_i,
    input  wire [3:0] data_i,
    output reg  [3:0] data_o
);
  always @(posedge clk_i or posedge arst_i) begin
    if (arst_i) data_o <= 4'd0;
    else if (cke_i) data_o <= data_i;
  end
endmodule
