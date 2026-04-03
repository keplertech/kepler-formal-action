module top(
  input logic req,
  input logic mode,
  input logic [3:0] data,
  output logic [3:0] result,
  output logic valid
);
  logic [3:0] next_value;

  always @* begin
    next_value = data;

    if (req) begin
      if (mode) begin
        next_value = data ^ 4'b1010;
      end else begin
        next_value = data + 4'b0001;
      end
    end
  end

  assign result = next_value;
  assign valid = req | mode;
endmodule