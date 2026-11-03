// Centered 3x3 luminance unsharp mask, replicated borders, elastic AXIS.
// Two grayscale rows and one RGB row; no frame buffer or software processing.
// The last column and last row are flushed before accepting the next frame.
// Fixed-size video: SOF resets coordinates; TLAST is regenerated from width.
// The upstream video bridge must supply IMAGE_WIDTH x IMAGE_HEIGHT frames.
`timescale 1ns/1ps
module rgb_sharpen_axis #(
    parameter integer IMAGE_WIDTH = 800,
    parameter integer IMAGE_HEIGHT = 480
)(
    input wire clk, resetn,
    input wire enable,
    input wire [23:0] s_data,
    input wire s_valid, s_user, s_last,
    output wire s_ready,
    output reg [23:0] m_data,
    output reg m_valid, m_user, m_last,
    input wire m_ready,
    output wire busy
);
    localparam integer X_BITS = $clog2(IMAGE_WIDTH);
    localparam integer Y_BITS = $clog2(IMAGE_HEIGHT);
    localparam LOAD = 2'd0, RIGHT_EDGE = 2'd1, BOTTOM_ROW = 2'd2;
    reg [1:0] state;
    reg [X_BITS-1:0] x;
    reg [Y_BITS-1:0] y;
    reg frame_seen, active_enable, bottom_flush;
    wire [X_BITS-1:0] px = (state == LOAD && s_user) ? 0 : x;
    wire [Y_BITS-1:0] py = (state == LOAD && s_user) ? 0 : y;

    (* ram_style = "distributed" *) reg [7:0] line1 [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line2 [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [23:0] rgb_line [0:IMAGE_WIDTH-1];
    wire [15:0] luma_sum = s_data[23:16] * 16'd77 +
                               s_data[15:8] * 16'd150 + s_data[7:0] * 16'd29;
    wire [7:0] center_now = line1[px];
    wire [7:0] top_now = (state == LOAD && py == 1) ? center_now : line2[px];
    wire [7:0] bottom_now = (state == BOTTOM_ROW) ? center_now : luma_sum[15:8];
    wire [23:0] rgb_now = rgb_line[px];
    reg [7:0] top_d1, top_d2, mid_d1, mid_d2, bot_d1, bot_d2;
    reg [23:0] rgb_d1;
    // At x=1 the left neighbour of output x=0 is replicated.
    wire [7:0] top_left = (px == 1) ? top_d1 : top_d2;
    wire [7:0] mid_left = (px == 1) ? mid_d1 : mid_d2;
    wire [7:0] bot_left = (px == 1) ? bot_d1 : bot_d2;
    wire [11:0] gaussian_sum =
        {4'd0, top_left} + ({4'd0, top_d1} << 1) + {4'd0, top_now} +
        ({4'd0, mid_left} << 1) + ({4'd0, mid_d1} << 2) + ({4'd0, center_now} << 1) +
        {4'd0, bot_left} + ({4'd0, bot_d1} << 1) + {4'd0, bottom_now};
    wire [11:0] right_sum =
        {4'd0, top_d2} + ({4'd0, top_d1} << 1) + {4'd0, top_d1} +
        ({4'd0, mid_d2} << 1) + ({4'd0, mid_d1} << 2) + ({4'd0, mid_d1} << 1) +
        {4'd0, bot_d2} + ({4'd0, bot_d1} << 1) + {4'd0, bot_d1};

    reg w_valid, w_user, w_last, w_enable;
    reg [23:0] w_rgb;
    reg [7:0] w_luma;
    reg [11:0] w_sum;
    // Separate shared color-offset selection from per-channel clipping.
    reg p_valid, p_user, p_last, p_enable;
    reg [23:0] p_rgb;
    reg signed [10:0] p_color_delta;
    wire out_ready = !m_valid || m_ready;
    wire p_ready = !p_valid || out_ready;
    wire w_ready = !w_valid || p_ready;
    assign s_ready = resetn && state == LOAD && w_ready;
    assign busy = frame_seen || state != LOAD || w_valid || p_valid || m_valid;
    wire [11:0] rounded_sum = w_sum + 12'd8;
    wire signed [10:0] detail = $signed({3'd0, w_luma}) -
                                     $signed({3'd0, rounded_sum[11:4]});
    wire signed [10:0] half_detail = detail >>> 1;
    wire signed [10:0] delta = (detail >= -11'sd2 && detail <= 11'sd2) ? 11'sd0 :
        (half_detail > 11'sd12) ? 11'sd12 :
        (half_detail < -11'sd12) ? -11'sd12 : half_detail;
    wire [7:0] high_rg = (w_rgb[23:16] > w_rgb[15:8]) ? w_rgb[23:16] : w_rgb[15:8];
    wire [7:0] low_rg = (w_rgb[23:16] < w_rgb[15:8]) ? w_rgb[23:16] : w_rgb[15:8];
    wire [7:0] high_channel = (high_rg > w_rgb[7:0]) ? high_rg : w_rgb[7:0];
    wire [7:0] low_channel = (low_rg < w_rgb[7:0]) ? low_rg : w_rgb[7:0];
    wire signed [10:0] positive_room = $signed({3'd0, (8'd255 - high_channel)});
    wire signed [10:0] negative_room = -$signed({3'd0, low_channel});
    // A shared bounded offset preserves RGB differences even at bright edges.
    wire signed [10:0] color_delta = (delta > positive_room) ? positive_room :
                                     (delta < negative_room) ? negative_room : delta;

    function [7:0] add_clip;
        input [7:0] channel;
        input signed [10:0] adjustment;
        reg signed [11:0] value;
        begin
            value = $signed({4'd0, channel}) + adjustment;
            add_clip = (value < 0) ? 8'd0 : (value > 255) ? 8'd255 : value[7:0];
        end
    endfunction

    always @(posedge clk) begin
        if (!resetn) begin
            state <= LOAD; x <= 0; y <= 0;
            frame_seen <= 0; active_enable <= 0; bottom_flush <= 0;
            top_d1 <= 0; top_d2 <= 0; mid_d1 <= 0; mid_d2 <= 0;
            bot_d1 <= 0; bot_d2 <= 0; rgb_d1 <= 0;
            w_valid <= 0; w_user <= 0; w_last <= 0; w_enable <= 0;
            w_rgb <= 0; w_luma <= 0; w_sum <= 0;
            p_valid <= 0; p_user <= 0; p_last <= 0; p_enable <= 0;
            p_rgb <= 0; p_color_delta <= 0;
            m_valid <= 0; m_data <= 0; m_user <= 0; m_last <= 0;
        end else begin
            if (out_ready) begin
                m_valid <= p_valid;
                if (p_valid) begin
                    m_data <= p_enable ? {add_clip(p_rgb[23:16], p_color_delta),
                        add_clip(p_rgb[15:8], p_color_delta),
                        add_clip(p_rgb[7:0], p_color_delta)} : p_rgb;
                    m_user <= p_user; m_last <= p_last;
                end
            end
            if (p_ready) begin
                p_valid <= w_valid;
                if (w_valid) begin
                    p_rgb <= w_rgb; p_enable <= w_enable;
                    p_color_delta <= color_delta;
                    p_user <= w_user; p_last <= w_last;
                end
            end
            if (w_ready) begin
                w_valid <= 0;
                if ((state == LOAD && s_valid && s_ready && (frame_seen || s_user)) ||
                    state == BOTTOM_ROW) begin
                    if (state == LOAD) begin
                        line2[px] <= center_now;
                        line1[px] <= bottom_now;
                        rgb_line[px] <= s_data;
                        if (s_user) begin
                            frame_seen <= 1;
                            active_enable <= enable;
                        end
                    end
                    if (px == 0) begin
                        top_d2 <= top_now; mid_d2 <= center_now; bot_d2 <= bottom_now;
                    end else begin
                        top_d2 <= top_d1; mid_d2 <= mid_d1; bot_d2 <= bot_d1;
                    end
                    top_d1 <= top_now; mid_d1 <= center_now; bot_d1 <= bottom_now;
                    rgb_d1 <= rgb_now;
                    if (px != 0 && (py != 0 || state == BOTTOM_ROW)) begin
                        w_valid <= 1; w_rgb <= rgb_d1; w_luma <= mid_d1;
                        w_sum <= gaussian_sum; w_enable <= active_enable;
                        w_user <= (state == LOAD && py == 1 && px == 1);
                        w_last <= 0;
                    end
                    if (px == IMAGE_WIDTH-1) begin
                        x <= 0;
                        if (state == LOAD && py == 0) begin
                            y <= 1; // first input row only fills the line stores
                        end else begin
                            state <= RIGHT_EDGE;
                            bottom_flush <= (state == BOTTOM_ROW);
                            y <= py;
                        end
                    end else begin
                        x <= px + 1'b1; y <= py;
                    end
                end else if (state == RIGHT_EDGE) begin
                    w_valid <= 1; w_rgb <= rgb_d1; w_luma <= mid_d1;
                    w_sum <= right_sum; w_enable <= active_enable;
                    w_user <= 0; w_last <= 1;
                    x <= 0;
                    if (bottom_flush) begin
                        state <= LOAD; y <= 0; frame_seen <= 0;
                    end else if (y == IMAGE_HEIGHT-1) begin
                        state <= BOTTOM_ROW;
                    end else begin
                        state <= LOAD; y <= y + 1'b1;
                    end
                end
            end
        end
    end
endmodule
