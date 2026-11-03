// 24-bit RGB AXI4-Stream image processing block.
// enable=0 is a transparent pass-through. TUSER and TLAST are preserved.
`timescale 1ns/1ps
module image_process #(
    parameter integer X_WIDTH = 12,
    parameter integer Y_WIDTH = 12,
    parameter [7:0] DEFAULT_THRESHOLD = 8'd120
)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *) input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *) input wire aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *) input wire [23:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TUSER" *) input wire s_axis_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [23:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TUSER" *) output wire m_axis_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,
    input wire enable,
    input wire threshold_enable,
    input wire overlay_enable,
    input wire overlay_mode,
    input wire [7:0] threshold,
    output reg [X_WIDTH-1:0] target_x,
    output reg [Y_WIDTH-1:0] target_y,
    output reg [31:0] target_area,
    output reg [31:0] frame_count,
    output reg stats_valid
);
    wire transfer = s_axis_tvalid && m_axis_tready;
    wire [7:0] red = s_axis_tdata[23:16];
    wire [7:0] green = s_axis_tdata[15:8];
    wire [7:0] blue = s_axis_tdata[7:0];
    wire [9:0] rgb_sum = red + green + blue;
    wire [7:0] gray = rgb_sum / 3;
    wire [7:0] active_threshold = (threshold == 8'd0) ? DEFAULT_THRESHOLD : threshold;
    wire target_pixel = enable && (gray >= active_threshold);
    reg [X_WIDTH-1:0] x_count;
    reg [Y_WIDTH-1:0] y_count;
    reg [X_WIDTH-1:0] min_x, max_x;
    reg [Y_WIDTH-1:0] min_y, max_y;
    reg [31:0] area_work;
    reg bbox_valid, frame_seen;
    wire [X_WIDTH-1:0] pixel_x = s_axis_tuser ? {X_WIDTH{1'b0}} : x_count;
    wire [Y_WIDTH-1:0] pixel_y = s_axis_tuser ? {Y_WIDTH{1'b0}} : y_count;
    wire rectangle_edge = bbox_valid && ((pixel_x == min_x) || (pixel_x == max_x) || (pixel_y == min_y) || (pixel_y == max_y)) && (pixel_x >= min_x) && (pixel_x <= max_x) && (pixel_y >= min_y) && (pixel_y <= max_y);
    wire centre_cross = bbox_valid && ((pixel_x == ((min_x + max_x) >> 1)) || (pixel_y == ((min_y + max_y) >> 1)));
    wire overlay_pixel = overlay_enable && (overlay_mode ? centre_cross : rectangle_edge);
    assign s_axis_tready = m_axis_tready;
    assign m_axis_tvalid = s_axis_tvalid;
    assign m_axis_tuser = s_axis_tuser;
    assign m_axis_tlast = s_axis_tlast;
    assign m_axis_tdata = !enable ? s_axis_tdata : threshold_enable ? (target_pixel ? 24'hFFFFFF : 24'h000000) : overlay_pixel ? 24'hFF0000 : s_axis_tdata;
    always @(posedge aclk) begin
        if (!aresetn) begin
            x_count <= 0; y_count <= 0; min_x <= {X_WIDTH{1'b1}}; max_x <= 0; min_y <= {Y_WIDTH{1'b1}}; max_y <= 0; area_work <= 0; bbox_valid <= 0; frame_seen <= 0; target_x <= 0; target_y <= 0; target_area <= 0; frame_count <= 0; stats_valid <= 0;
        end else begin
            stats_valid <= 1'b0;
            if (transfer) begin
                if (s_axis_tuser) begin
                    if (frame_seen) begin target_area <= area_work; target_x <= bbox_valid ? ((min_x + max_x) >> 1) : 0; target_y <= bbox_valid ? ((min_y + max_y) >> 1) : 0; stats_valid <= 1'b1; end
                    frame_seen <= 1'b1; frame_count <= frame_count + 1'b1; area_work <= target_pixel ? 1 : 0; bbox_valid <= target_pixel; min_x <= pixel_x; max_x <= pixel_x; min_y <= pixel_y; max_y <= pixel_y; x_count <= s_axis_tlast ? 0 : 1; y_count <= s_axis_tlast ? y_count + 1'b1 : 0;
                end else begin
                    if (target_pixel) begin area_work <= area_work + 1'b1; if (!bbox_valid) begin bbox_valid <= 1'b1; min_x <= pixel_x; max_x <= pixel_x; min_y <= pixel_y; max_y <= pixel_y; end else begin if (pixel_x < min_x) min_x <= pixel_x; if (pixel_x > max_x) max_x <= pixel_x; if (pixel_y < min_y) min_y <= pixel_y; if (pixel_y > max_y) max_y <= pixel_y; end end
                    if (s_axis_tlast) begin x_count <= 0; y_count <= y_count + 1'b1; end else x_count <= x_count + 1'b1;
                end
            end
        end
    end
endmodule
