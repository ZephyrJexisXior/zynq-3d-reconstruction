`timescale 1ns/1ps
// Release the native video/FIFO reset as soon as the camera clock domain is
// alive. The capture IP performs its own frame warm-up; waiting for VSYNC in
// this separate reset block can deadlock the AXI video bridge when the sensor
// starts with a different VSYNC phase or polarity.
module camera_video_reset(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 cam_pclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME cam_pclk, FREQ_HZ 72000000, ASSOCIATED_RESET vid_reset" *)
    input wire cam_pclk,
    input wire cam_vsync,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME resetn, POLARITY ACTIVE_LOW" *)
    input wire resetn,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 vid_reset RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME vid_reset, POLARITY ACTIVE_HIGH" *)
    output wire vid_reset
);
    (* ASYNC_REG = "TRUE" *) reg [1:0] reset_sync = 0;
    always @(posedge cam_pclk or negedge resetn) begin
        if (!resetn) reset_sync <= 0;
        else reset_sync <= {reset_sync[0], 1'b1};
    end
    assign vid_reset = !reset_sync[1];
endmodule
