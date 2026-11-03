// Two chroma passes give radius-two support without full-frame storage.
`timescale 1ns/1ps
module rgb_isp_ycocg_axis #(
    parameter integer IMAGE_WIDTH=800,IMAGE_HEIGHT=480
)(
    input wire clk,resetn,enable,denoise_enable,tone_enable,chroma_enable,
    input wire [23:0] s_data,
    input wire s_valid,s_user,s_last,
    output wire s_ready,
    output wire [23:0] m_data,
    output wire m_valid,m_user,m_last,
    input wire m_ready,
    output wire busy
);
    wire [23:0] a_data,b_data;
    wire a_valid,a_user,a_last,a_ready,b_valid,b_user,b_last,b_ready;
    wire [3:0] a_flags,b_flags;
    wire busy_a,busy_b,busy_c;
    rgb_chroma_axis #(.IMAGE_WIDTH(IMAGE_WIDTH),.IMAGE_HEIGHT(IMAGE_HEIGHT),.LUMA_UPDATE(1)) first_pass(
        .clk(clk),.resetn(resetn),.s_flags({chroma_enable,tone_enable,denoise_enable,enable}),
        .s_data(s_data),.s_valid(s_valid),.s_user(s_user),.s_last(s_last),.s_ready(s_ready),
        .m_data(a_data),.m_valid(a_valid),.m_user(a_user),.m_last(a_last),.m_flags(a_flags),.m_ready(a_ready),.busy(busy_a));
    rgb_chroma_axis #(.IMAGE_WIDTH(IMAGE_WIDTH),.IMAGE_HEIGHT(IMAGE_HEIGHT),.LUMA_UPDATE(0)) second_pass(
        .clk(clk),.resetn(resetn),.s_flags(a_flags),
        .s_data(a_data),.s_valid(a_valid),.s_user(a_user),.s_last(a_last),.s_ready(a_ready),
        .m_data(b_data),.m_valid(b_valid),.m_user(b_user),.m_last(b_last),.m_flags(b_flags),.m_ready(b_ready),.busy(busy_b));
    rgb_isp_axis #(.IMAGE_WIDTH(IMAGE_WIDTH),.IMAGE_HEIGHT(IMAGE_HEIGHT)) detail_tone(
        .clk(clk),.resetn(resetn),.enable(b_flags[0]),.denoise_enable(b_flags[1]),.tone_enable(b_flags[2]),.chroma_enable(b_flags[3]),
        .s_data(b_data),.s_valid(b_valid),.s_user(b_user),.s_last(b_last),.s_ready(b_ready),
        .m_data(m_data),.m_valid(m_valid),.m_user(m_user),.m_last(m_last),.m_ready(m_ready),.busy(busy_c));
    assign busy=busy_a || busy_b || busy_c;
endmodule
