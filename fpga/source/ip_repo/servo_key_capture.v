// Debounced active-low KEY0 trigger, timed in whole 20 ms PWM periods.
// All state is synchronous to the calibrated 100 MHz PWM clock.
`timescale 1ns/1ps
module servo_key_capture #(
    parameter integer ACLK_FREQ_HZ = 100000000,
    parameter integer DEBOUNCE_CYCLES = ACLK_FREQ_HZ / 50
)(
    input wire clk, resetn, key_n, armed, period_boundary,
    input wire software_only, software_start,
    input wire [11:0] pulse_us,
    input wire [11:0] duration_periods,
    input wire [31:0] frame_count,
    output wire pwm_enable,
    output wire [11:0] pwm_pulse_us,
    output wire [3:0] status,
    output reg [31:0] trigger_count,
    output reg [31:0] trigger_frame
);
    (* ASYNC_REG = "TRUE" *) reg key_meta, key_sync;
    reg key_candidate, key_level;
    localparam integer DB_WIDTH = (DEBOUNCE_CYCLES < 2) ? 1 : $clog2(DEBOUNCE_CYCLES + 1);
    reg [DB_WIDTH-1:0] stable_cycles;
    reg ready, pending, active;
    reg [11:0] periods_left;
    wire released = key_level && key_candidate && (stable_cycles == DEBOUNCE_CYCLES);
    wire start_now = armed && pending && !active && period_boundary;
    wire stop_now = active && period_boundary && (periods_left == 1);
    // Include the boundary event so the PWM latches this command on the
    // same edge as trigger_count, with no one-period delay.
    wire run_period = armed && (start_now || (active && !stop_now));
    wire [11:0] safe_pulse = (pulse_us < 1551) ? 12'd1551 :
                            (pulse_us > 2000) ? 12'd2000 : pulse_us;
    assign pwm_enable = armed;
    assign pwm_pulse_us = run_period ? safe_pulse : 12'd1500;
    assign status = {active, pending, ready, !key_level};
    always @(posedge clk) begin
        if (!resetn) begin
            key_meta <= 1; key_sync <= 1;
            key_candidate <= 1; key_level <= 1; stable_cycles <= 0;
        end else begin
            key_meta <= key_n; key_sync <= key_meta;
            if (key_sync != key_candidate) begin
                key_candidate <= key_sync;
                stable_cycles <= 0;
            end else if (stable_cycles < DEBOUNCE_CYCLES) begin
                stable_cycles <= stable_cycles + 1'b1;
                if (stable_cycles == DEBOUNCE_CYCLES - 1)
                    key_level <= key_candidate;
            end
        end
    end
    always @(posedge clk) begin
        if (!resetn) begin
            ready <= 0; pending <= 0; active <= 0; periods_left <= 0;
            trigger_count <= 0; trigger_frame <= 0;
        end else if (!armed) begin
            ready <= 0; pending <= 0; active <= 0; periods_left <= 0;
        end else if (active) begin
            ready <= 0;
            if (period_boundary) begin
                if (periods_left == 1) begin
                    active <= 0; periods_left <= 0;
                end else periods_left <= periods_left - 1'b1;
            end
        end else if (start_now) begin
            pending <= 0; active <= 1; ready <= 0;
            periods_left <= (duration_periods == 0) ? 12'd1 : duration_periods;
            trigger_count <= trigger_count + 1'b1;
            trigger_frame <= frame_count;
        end else if (software_only) begin
            ready <= 0;
            if (software_start && !pending) pending <= 1;
        end else if (!pending) begin
            if (released) ready <= 1;
            if (ready && !key_level) begin
                ready <= 0; pending <= 1;
            end
        end
    end
endmodule
