//=============================================================================
// image_process_axi.v
//
// 24-bit RGB AXI4-Stream image processing block with an AXI4-Lite
// control/status register file (so the Zynq PS can drive and read it).
//
// Stream behaviour
//   * Four-stage elastic pipeline followed by a centered 3x3 line filter.
//     Fixed IMAGE_WIDTH x IMAGE_HEIGHT input frames are required.
//     Pixel data and sidebands
//     remain aligned and every stage honours downstream backpressure.
//   * enable = 0  -> TDATA is passed through unchanged (safe default).
//   * enable = 1, threshold_enable = 1 -> black/white threshold picture.
//   * enable = 1, gray_enable = 1      -> grayscale picture.
//   * enable = 1, overlay_enable = 1   -> red bounding box / centre cross
//     of the detected target drawn on the original picture.
//   * Target statistics (bounding-box centre, area, frame counter) are
//     latched at the end of every frame and are readable over AXI4-Lite.
//
// AXI4-Lite register map (byte offsets, 32-bit registers)
//   0x00 CTRL          RW  [0] enable  [1] threshold_enable  [2] overlay_enable
//                          [3] overlay_mode (0 box, 1 cross)
//                          [4] invert (target = gray < threshold)
//                          [5] gray_enable (grayscale picture output)
//                          [6] mean_filter_enable (causal 3x3 mean)
//                          [7] majority_enable (3x3 binary majority denoise)
//                          [8] sharpen_enable (centered color luminance detail)
//                          [9] roi_mask_enable (white inside ROI, black outside)
//                          [10] color edge-preserving denoise
//                          [11] common-gain tone lift (gamma 0.8, max 1.5x)
//                          [12] YCoCg chroma filter (requires [10]); legacy RGB when 0
//   0x04 THRESHOLD     RW  [7:0] threshold (0 -> parameter DEFAULT_THRESHOLD)
//   0x08 STATUS        RO  [0] new_stats (sticky, cleared by reading STATUS)
//                          [1] frame_locked (at least one frame completed)
//                          [2] busy (a frame is currently being received)
//   0x0C TARGET_X      RO  bounding-box centre X of the last frame
//   0x10 TARGET_Y      RO  bounding-box centre Y of the last frame
//   0x14 TARGET_AREA   RO  number of target pixels in the last frame
//   0x18 FRAME_COUNT   RO  number of completed frames
//   0x1C VERSION       RO  0x0005_000A (quality shape/brightness metrics)
//   0x20 SERVO_CTRL    RW  [0] PWM enable (default 0)
//   0x24 SERVO_PULSE   RW  pulse width in us, clamped to 500..2500
//   0x28 SERVO_ACTIVE  RO  pulse width currently active at the PWM output
//   0x2C KEY_CAPTURE   RW  [0] arm, [1] software-only, [2] W1 software start
//                          Software start is ignored while pending/rotating.
//   0x30 CAPTURE_TICKS RW  20 ms periods, 1..3000 (default 750 = 15 s)
//   0x34 KEY_STATUS    RO  [0] pressed [1] ready [2] pending [3] rotating
//   0x38 TRIGGER_COUNT RO  increments at the first rotation PWM edge
//   0x3C TRIGGER_FRAME RO  FRAME_COUNT latched at that edge
//   0x40 ROI_X         RW  [11:0] x0, [27:16] x1 (exclusive)
//   0x44 ROI_Y         RW  [11:0] y0, [27:16] y1 (exclusive)
//   0x48 QUALITY_FRAME RO  frame number of the quality counters
//   0x4C QUALITY_EDGES RO  horizontal luminance steps >= 12 inside ROI
//   0x50 QUALITY_CLIPPED RO  ROI pixels with luma <= 16 or >= 240
//   0x54 QUALITY_PIXELS RO  number of ROI pixels in the completed frame
//   0x58 QUALITY_LUMA_SUM RO sum of ROI luma values in the completed frame
//   0x5C QUALITY_DARK RO     ROI luma <= 16 count
//   0x60 IMAGE_WIDTH RO      configured active width
//   0x64 IMAGE_HEIGHT RO     configured active height
//   0x68 CAPTURE_FEATURE RO  0x53544331: bounded stop-and-capture support
//   0x70 QUALITY_SATURATED RO ROI luma >= 240 count
//   0x74 QUALITY_NOISE RO    adjacent horizontal ROI luma-difference sum
//   0x78 QUALITY_SHAPE RO    [31:16] actual rows, [15:0] shape flags
//   0x7C QUALITY_FRAME_PIXELS RO actual accepted pixels in completed frame
//
// Pure RTL: no vendor primitives and no ILA instance.  A read-only packed
// debug bus is exported so a single-probe ILA can be attached in the block
// design without touching the AXI4-Stream data path.
//=============================================================================
`timescale 1ns/1ps

module image_process_axi #(
    parameter integer X_WIDTH = 12,
    parameter integer Y_WIDTH = 12,
    parameter integer MAX_LINE_WIDTH = 2048,
    parameter integer IMAGE_WIDTH = 800,
    parameter integer IMAGE_HEIGHT = 480,
    parameter [7:0]   DEFAULT_THRESHOLD = 8'd120,
    // The block design and the PYNQ runtime use FCLK0 at 100 MHz.  Keeping
    // this default aligned with the interface metadata also keeps the servo
    // pulse calibration correct when the IP is regenerated outside Vivado.
    parameter integer ACLK_FREQ_HZ = 100000000
)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXIS:M_AXIS:S_AXI, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000, FREQ_TOLERANCE_HZ 0, PHASE 0.000" *)
    input  wire        aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW, INSERT_VIP 0" *)
    input  wire        aresetn,

    // ---- video in (from v_vid_in_axi4s_0/video_out) -----------------------
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input  wire [23:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  wire        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TUSER" *)
    input  wire        s_axis_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  wire        s_axis_tlast,

    // ---- video out (to axi_vdma_0/S_AXIS_S2MM) ---------------------------
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    output wire [23:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire        m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input  wire        m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TUSER" *)
    output wire        m_axis_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire        m_axis_tlast,

    // ---- AXI4-Lite control/status slave (PS) -----------------------------
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, FREQ_HZ 100000000, DATA_WIDTH 32, ADDR_WIDTH 7, ID_WIDTH 0, AWUSER_WIDTH 0, ARUSER_WIDTH 0, WUSER_WIDTH 0, RUSER_WIDTH 0, BUSER_WIDTH 0, READ_WRITE_MODE READ_WRITE, HAS_BURST 0, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, HAS_WSTRB 1, HAS_BRESP 1, HAS_RRESP 1, SUPPORTS_NARROW_BURST 0, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1, MAX_BURST_LENGTH 1, RUSER_BITS_PER_BYTE 0, WUSER_BITS_PER_BYTE 0" *)
    input  wire [6:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output reg  [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output reg         s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  wire [6:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output reg         s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output reg  [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output reg  [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output reg         s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready,

    // 50 Hz continuous-rotation servo signal.  The output is forced low
    // while reset is asserted; after reset it starts at the safe 1500 us
    // stop command.
    output wire         servo_pwm,
    input wire [0:0]    pl_key,

    // Packed, observation-only debug bus for the optional single-probe ILA.
    output wire [159:0] debug_probe
);

    //-------------------------------------------------------------------------
    // Control registers (written through AXI4-Lite)
    //-------------------------------------------------------------------------
    reg        ctl_enable;
    reg        ctl_threshold_enable;
    reg        ctl_overlay_enable;
    reg        ctl_overlay_mode;
    reg        ctl_invert;
    reg        ctl_gray_enable;
    reg        ctl_mean_filter_enable;
    reg        ctl_majority_enable;
    reg        ctl_sharpen_enable;
    reg        ctl_roi_mask_enable;
    reg ctl_denoise_enable, ctl_tone_enable, ctl_chroma_enable;
    reg [7:0]  ctl_threshold;
    reg [X_WIDTH-1:0] roi_x0, roi_x1, active_roi_x0, active_roi_x1;
    reg [Y_WIDTH-1:0] roi_y0, roi_y1, active_roi_y0, active_roi_y1;

    // Continuous-rotation servo command registers.  The command is copied
    // to the active registers only at a 20 ms boundary, so AXI writes cannot
    // shorten the pulse already in progress or create a runt pulse.
    reg        key_capture_armed;
    reg        key_software_only, key_software_start;
    reg [11:0] key_capture_ticks;
    wire key_pwm_enable;
    wire [11:0] key_pwm_pulse_us;
    wire [3:0] key_status;
    wire [31:0] key_trigger_count, key_trigger_frame;
    reg        servo_command_enable;
    reg [11:0] servo_command_pulse_us;
    reg        servo_active_enable;
    reg        servo_has_command;
    reg [11:0] servo_active_pulse_us;

    localparam integer SERVO_CLOCKS_PER_US = ACLK_FREQ_HZ / 1000000;
    localparam integer SERVO_SUB_US_WIDTH =
        (SERVO_CLOCKS_PER_US <= 1) ? 1 : $clog2(SERVO_CLOCKS_PER_US);
    reg [SERVO_SUB_US_WIDTH-1:0] servo_sub_us_count;
    reg [14:0] servo_period_us;
    wire servo_period_boundary =
        (servo_sub_us_count == SERVO_CLOCKS_PER_US - 1) &&
        (servo_period_us == 15'd19999);

    assign servo_pwm = aresetn && servo_active_enable &&
                       (servo_period_us < servo_active_pulse_us);

    always @(posedge aclk) begin
        if (!aresetn) begin
            servo_sub_us_count  <= 0;
            servo_period_us     <= 0;
            servo_active_enable <= 1'b0;
            servo_has_command <= 1'b0;
            servo_active_pulse_us <= 12'd1500;
        end else begin
            // A powered continuous-rotation servo must keep receiving neutral
            // after stop; absence of pulses is not a defined stop command.
            if (key_capture_armed || servo_command_enable)
                servo_has_command <= 1'b1;
            if (servo_sub_us_count == SERVO_CLOCKS_PER_US - 1) begin
            servo_sub_us_count <= 0;
            if (servo_period_us == 15'd19999) begin
                servo_period_us <= 0;
                servo_active_enable <= key_capture_armed ? key_pwm_enable :
                                       (servo_command_enable || servo_has_command);
                servo_active_pulse_us <= key_capture_armed ? key_pwm_pulse_us :
                    (servo_command_enable ? servo_command_pulse_us : 12'd1500);
            end else begin
                servo_period_us <= servo_period_us + 1'b1;
            end
        end else begin
            servo_sub_us_count <= servo_sub_us_count + 1'b1;
            end
        end
    end

    // Shadow settings are written by PS.  They are copied at SOF and then
    // carried with every pixel, so a register write cannot create a frame
    // whose first and last lines use different processing modes.
    reg [12:0]  active_ctrl;
    reg [7:0]  active_threshold;

    // Statistics latched at the end of every frame
    reg [X_WIDTH-1:0] stat_target_x;
    reg [Y_WIDTH-1:0] stat_target_y;
    reg [31:0]        stat_target_area;
    reg [31:0]        stat_frame_count;
    reg               stat_new;          // sticky, cleared when STATUS is read
    reg               stat_frame_locked;
    reg [31:0] quality_frame;
    reg [31:0] quality_edges, quality_clipped, quality_pixels;
    reg [31:0] edges_work, clipped_work, pixels_work;

    // Output-stream shape and photometric metrics.  These counters observe
    // the same accepted beats as the VDMA, so they describe what actually
    // reached the memory writer rather than the sensor configuration alone.
    // Luma, dark, saturated and noise values are restricted to the latched
    // ROI; frame_pixels and rows count every accepted output beat/line.
    reg [31:0] quality_luma_sum, quality_dark, quality_saturated;
    reg [31:0] quality_noise;
    reg [15:0] quality_shape_rows;
    reg [31:0] quality_shape_pixels;
    reg [15:0] quality_shape_flags;
    reg [31:0] metric_luma_work, metric_dark_work, metric_saturated_work;
    reg [31:0] metric_noise_work, metric_shape_pixels_work;
    reg [15:0] metric_rows_work, metric_line_pixels_work;
    reg        metric_active, metric_published, metric_bad_shape;
    reg        metric_seen_sof, metric_seen_last;
    reg [X_WIDTH-1:0] metric_x_count;
    reg [Y_WIDTH-1:0] metric_y_count;
    reg [X_WIDTH-1:0] metric_roi_x0, metric_roi_x1;
    reg [Y_WIDTH-1:0] metric_roi_y0, metric_roi_y1;
    reg [7:0] metric_previous_gray;
    reg        metric_previous_valid, metric_previous_roi;
    // The quality monitor is deliberately one beat behind the output stream.
    // Registering the output pixel before the grayscale delta and wide metric
    // accumulators keeps those arithmetic paths out of one 100 MHz cycle.
    reg        metric_pipe_valid, metric_pipe_sof, metric_pipe_last;
    reg        metric_pipe_frame_end, metric_pipe_line_bad;
    reg        metric_pipe_row_start, metric_pipe_roi;
    reg [7:0]  metric_pipe_gray;
    reg previous_roi;
    reg [7:0] previous_gray;
    reg quality_sample_valid, quality_sample_roi, quality_sample_sof;
    reg quality_sample_end, quality_sample_row_start, quality_sample_clipped;
    reg [7:0] quality_sample_gray;
    reg quality_count_valid, quality_count_sof, quality_count_end;
    reg quality_count_roi, quality_count_clipped, quality_count_edge;
    reg [X_WIDTH-1:0] output_x, quality_roi_x0, quality_roi_x1;
    reg [Y_WIDTH-1:0] output_y, quality_roi_y0, quality_roi_y1;

    servo_key_capture #(.ACLK_FREQ_HZ(ACLK_FREQ_HZ)) key_capture (
        .clk(aclk), .resetn(aresetn), .key_n(pl_key[0]),
        .armed(key_capture_armed), .period_boundary(servo_period_boundary),
        .software_only(key_software_only), .software_start(key_software_start),
        .pulse_us(servo_command_pulse_us), .duration_periods(key_capture_ticks),
        .frame_count(stat_frame_count), .pwm_enable(key_pwm_enable),
        .pwm_pulse_us(key_pwm_pulse_us), .status(key_status),
        .trigger_count(key_trigger_count), .trigger_frame(key_trigger_frame)
    );

    // AXI4-Lite read-channel state.  Declared here (before the statistics
    // block) because STATUS is cleared by a read of offset 0x08.
    localparam R_IDLE = 1'b0;
    localparam R_RESP = 1'b1;
    reg rstate;
    wire status_read = (rstate == R_IDLE) && s_axi_arvalid && s_axi_arready &&
                       (s_axi_araddr[5:2] == 4'h2);

    //-------------------------------------------------------------------------
    // Datapath
    //-------------------------------------------------------------------------
    //  p1: RGB -> gray
    //  p2: causal 3x3 mean window
    //  p3: threshold followed by a second, binary 3x3 majority window
    //  p4: output register and the exact VDMA-accepted statistics event
    // Every stage is elastic and preserves TUSER/TLAST under backpressure.
    wire [12:0] ctl_vector = {ctl_chroma_enable, ctl_tone_enable, ctl_denoise_enable, ctl_roi_mask_enable, ctl_sharpen_enable,
                             ctl_majority_enable, ctl_mean_filter_enable,
                             ctl_gray_enable, ctl_invert, ctl_overlay_mode,
                             ctl_overlay_enable, ctl_threshold_enable,
                             ctl_enable};

    reg        p1_valid, p2_valid, p3_valid, p4_valid;
    reg [23:0] p1_rgb, p2_rgb, p3_rgb, p4_data;
    reg [7:0]  p1_gray, p2_gray, p3_gray;
    reg [12:0]  p1_ctrl, p2_ctrl, p3_ctrl;
    reg p4_sharpen, p4_denoise, p4_tone, p4_chroma;
    wire sharpen_ready;
    wire sharpen_busy;
    reg [7:0]  p1_threshold, p2_threshold;
    reg        p1_user, p1_last, p2_user, p2_last;
    reg        p3_user, p3_last, p4_user, p4_last;
    reg [X_WIDTH-1:0] p2_x, p3_x, p4_x;
    reg [Y_WIDTH-1:0] p2_y, p3_y, p4_y;
    reg [11:0] p2_window_sum;
    reg        p2_window_valid;
    reg [3:0]  p3_hit_count;
    reg        p3_window_valid;
    reg        p3_single_hit, p4_target_pixel;
    reg        p2_roi, p3_roi;

    wire p4_ready = !p4_valid || sharpen_ready;
    wire p3_ready = !p3_valid || p4_ready;
    wire p2_ready = !p2_valid || p3_ready;
    wire p1_ready = !p1_valid || p2_ready;
    wire p1_move  = p1_valid && p2_ready;
    wire p2_move  = p2_valid && p3_ready;
    wire p3_move  = p3_valid && p4_ready;
    wire p4_move  = p4_valid && sharpen_ready;

    wire [7:0] in_red   = s_axis_tdata[23:16];
    wire [7:0] in_green = s_axis_tdata[15:8];
    wire [7:0] in_blue  = s_axis_tdata[7:0];
    wire [15:0] in_gray_sum = (in_red * 16'd77) +
                              (in_green * 16'd150) +
                              (in_blue * 16'd29);
    wire [7:0] in_gray = in_gray_sum[15:8];

    reg [X_WIDTH-1:0] x_count;
    reg [Y_WIDTH-1:0] y_count;
    wire [X_WIDTH-1:0] p1_x = p1_user ? {X_WIDTH{1'b0}} : x_count;
    wire [Y_WIDTH-1:0] p1_y = p1_user ? {Y_WIDTH{1'b0}} : y_count;
    wire [X_WIDTH-1:0] sample_x0 = p1_user ? roi_x0 : active_roi_x0;
    wire [X_WIDTH-1:0] sample_x1 = p1_user ? roi_x1 : active_roi_x1;
    wire [Y_WIDTH-1:0] sample_y0 = p1_user ? roi_y0 : active_roi_y0;
    wire [Y_WIDTH-1:0] sample_y1 = p1_user ? roi_y1 : active_roi_y1;
    wire sample_roi = (p1_x >= sample_x0) && (p1_x < sample_x1) &&
                      (p1_y >= sample_y0) && (p1_y < sample_y1);
    wire [X_WIDTH-1:0] quality_x = m_axis_tuser ? 0 : output_x;
    wire [Y_WIDTH-1:0] quality_y = m_axis_tuser ? 0 : output_y;
    wire [X_WIDTH-1:0] quality_x0 = m_axis_tuser ? roi_x0 : quality_roi_x0;
    wire [X_WIDTH-1:0] quality_x1 = m_axis_tuser ? roi_x1 : quality_roi_x1;
    wire [Y_WIDTH-1:0] quality_y0 = m_axis_tuser ? roi_y0 : quality_roi_y0;
    wire [Y_WIDTH-1:0] quality_y1 = m_axis_tuser ? roi_y1 : quality_roi_y1;
    wire output_roi = (quality_x >= quality_x0) && (quality_x < quality_x1) &&
                      (quality_y >= quality_y0) && (quality_y < quality_y1);
    wire [15:0] output_gray_sum = m_axis_tdata[23:16] * 16'd77 +
                                  m_axis_tdata[15:8] * 16'd150 +
                                  m_axis_tdata[7:0] * 16'd29;
    wire [7:0] output_gray = output_gray_sum[15:8];
    wire [7:0] gray_difference = (quality_sample_gray >= previous_gray) ?
        (quality_sample_gray - previous_gray) :
        (previous_gray - quality_sample_gray);
    wire sample_edge = quality_sample_roi && previous_roi &&
                       !quality_sample_row_start &&
                       (gray_difference >= 8'd12);
    wire sample_clipped = output_roi &&
                          ((output_gray <= 8'd16) || (output_gray >= 8'd240));

    localparam integer EXPECTED_FRAME_PIXELS = IMAGE_WIDTH * IMAGE_HEIGHT;
    wire metric_fire = m_axis_tvalid && m_axis_tready;
    wire [X_WIDTH-1:0] metric_x = m_axis_tuser ? {X_WIDTH{1'b0}} : metric_x_count;
    wire [Y_WIDTH-1:0] metric_y = m_axis_tuser ? {Y_WIDTH{1'b0}} : metric_y_count;
    wire [X_WIDTH-1:0] metric_x0 = m_axis_tuser ? roi_x0 : metric_roi_x0;
    wire [X_WIDTH-1:0] metric_x1 = m_axis_tuser ? roi_x1 : metric_roi_x1;
    wire [Y_WIDTH-1:0] metric_y0 = m_axis_tuser ? roi_y0 : metric_roi_y0;
    wire [Y_WIDTH-1:0] metric_y1 = m_axis_tuser ? roi_y1 : metric_roi_y1;
    wire metric_roi = (metric_x >= metric_x0) && (metric_x < metric_x1) &&
                      (metric_y >= metric_y0) && (metric_y < metric_y1);
    wire [15:0] metric_gray_sum = m_axis_tdata[23:16] * 16'd77 +
                                   m_axis_tdata[15:8] * 16'd150 +
                                   m_axis_tdata[7:0] * 16'd29;
    wire [7:0] metric_gray = metric_gray_sum[15:8];
    wire [7:0] metric_gray_delta = (metric_gray >= metric_previous_gray) ?
        (metric_gray - metric_previous_gray) :
        (metric_previous_gray - metric_gray);
    wire [7:0] metric_noise_sample = metric_roi && metric_previous_valid &&
        metric_previous_roi && (metric_x != 0) ? metric_gray_delta : 0;
    wire [7:0] metric_pipe_gray_delta =
        (metric_pipe_gray >= metric_previous_gray) ?
        (metric_pipe_gray - metric_previous_gray) :
        (metric_previous_gray - metric_pipe_gray);
    wire [7:0] metric_pipe_noise_sample =
        metric_pipe_roi && metric_previous_valid && metric_previous_roi &&
        !metric_pipe_row_start ? metric_pipe_gray_delta : 0;
    // Coordinates belong to the arriving output beat, while the wide
    // accumulators below consume its registered copy one cycle later.
    wire [15:0] metric_line_next = metric_x + 16'd1;
    wire metric_line_bad = metric_fire && m_axis_tlast &&
                           (metric_line_next != IMAGE_WIDTH);
    wire metric_frame_end = metric_fire && m_axis_tlast &&
                            (metric_y == IMAGE_HEIGHT-1);

    // First pair of line stores creates the grayscale mean window.
    localparam integer LINE_ADDR_WIDTH = $clog2(MAX_LINE_WIDTH);
    (* ram_style = "distributed" *) reg [7:0] gray_line_1 [0:MAX_LINE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] gray_line_2 [0:MAX_LINE_WIDTH-1];
    reg [7:0] gray_row0_d1, gray_row0_d2;
    reg [7:0] gray_row1_d1, gray_row1_d2;
    reg [7:0] gray_row2_d1, gray_row2_d2;

    wire [LINE_ADDR_WIDTH-1:0] gray_addr = p1_x[LINE_ADDR_WIDTH-1:0];
    wire [7:0] gray_row1_now = gray_line_1[gray_addr];
    wire [7:0] gray_row2_now = gray_line_2[gray_addr];
    wire gray_window_valid = (p1_x >= 2) && (p1_y >= 2);
    wire [11:0] gray_window_sum =
        gray_row2_d2 + gray_row2_d1 + gray_row2_now +
        gray_row1_d2 + gray_row1_d1 + gray_row1_now +
        gray_row0_d2 + gray_row0_d1 + p1_gray;

    wire [7:0] p2_mean_gray = p2_window_sum / 9;
    wire [7:0] p2_processed_gray =
        (p2_ctrl[6] && p2_window_valid) ? p2_mean_gray : p2_gray;
    wire [7:0] p2_active_threshold = (p2_threshold == 8'd0)
                                        ? DEFAULT_THRESHOLD : p2_threshold;
    wire p2_threshold_single = p2_ctrl[4]
                             ? (p2_processed_gray < p2_active_threshold)
                             : (p2_processed_gray >= p2_active_threshold);

    // Second pair of line stores operates on the threshold result, making the
    // implemented order exactly mean -> threshold -> binary majority.
    (* ram_style = "distributed" *) reg binary_line_1 [0:MAX_LINE_WIDTH-1];
    (* ram_style = "distributed" *) reg binary_line_2 [0:MAX_LINE_WIDTH-1];
    reg bin_row0_d1, bin_row0_d2;
    reg bin_row1_d1, bin_row1_d2;
    reg bin_row2_d1, bin_row2_d2;

    wire [LINE_ADDR_WIDTH-1:0] binary_addr = p2_x[LINE_ADDR_WIDTH-1:0];
    wire bin_row1_now = binary_line_1[binary_addr];
    wire bin_row2_now = binary_line_2[binary_addr];
    wire binary_window_valid = (p2_x >= 2) && (p2_y >= 2);
    wire [3:0] binary_hit_count =
        {3'd0, bin_row2_d2} + {3'd0, bin_row2_d1} + {3'd0, bin_row2_now} +
        {3'd0, bin_row1_d2} + {3'd0, bin_row1_d1} + {3'd0, bin_row1_now} +
        {3'd0, bin_row0_d2} + {3'd0, bin_row0_d1} + {3'd0, p2_threshold_single};

    wire p3_threshold_hit = (p3_ctrl[7] && p3_window_valid)
                          ? (p3_hit_count >= 4'd5) : p3_single_hit;
    wire p3_target_pixel = p3_ctrl[0] && p3_threshold_hit;

    reg [X_WIDTH-1:0] min_x, max_x, overlay_min_x, overlay_max_x;
    reg [Y_WIDTH-1:0] min_y, max_y, overlay_min_y, overlay_max_y;
    reg [31:0] area_work;
    reg bbox_valid, overlay_bbox_valid, frame_seen;

    // During the first beat of a frame the just-finished working box is used;
    // afterwards its latched copy is used.  The displayed box is therefore a
    // complete previous-frame box rather than a partially growing current box.
    wire draw_bbox_valid = p3_user ? bbox_valid : overlay_bbox_valid;
    wire [X_WIDTH-1:0] draw_min_x = p3_user ? min_x : overlay_min_x;
    wire [X_WIDTH-1:0] draw_max_x = p3_user ? max_x : overlay_max_x;
    wire [Y_WIDTH-1:0] draw_min_y = p3_user ? min_y : overlay_min_y;
    wire [Y_WIDTH-1:0] draw_max_y = p3_user ? max_y : overlay_max_y;
    wire [X_WIDTH:0] draw_x_sum = {1'b0, draw_min_x} + {1'b0, draw_max_x};
    wire [Y_WIDTH:0] draw_y_sum = {1'b0, draw_min_y} + {1'b0, draw_max_y};
    wire [X_WIDTH-1:0] draw_centre_x = draw_x_sum[X_WIDTH:1];
    wire [Y_WIDTH-1:0] draw_centre_y = draw_y_sum[Y_WIDTH:1];

    wire rectangle_edge = draw_bbox_valid &&
        ((p3_x == draw_min_x) || (p3_x == draw_max_x) ||
         (p3_y == draw_min_y) || (p3_y == draw_max_y)) &&
        (p3_x >= draw_min_x) && (p3_x <= draw_max_x) &&
        (p3_y >= draw_min_y) && (p3_y <= draw_max_y);
    wire centre_cross = draw_bbox_valid &&
        ((p3_x == draw_centre_x) || (p3_y == draw_centre_y));
    wire overlay_pixel = p3_ctrl[2] &&
                         (p3_ctrl[3] ? centre_cross : rectangle_edge);

    wire [23:0] p3_gray24 = {p3_gray, p3_gray, p3_gray};
    wire [23:0] selected_data = !p3_ctrl[0] ? p3_rgb :
        p3_ctrl[9] ? (p3_roi ? 24'hFFFFFF : 24'h000000) :
        p3_ctrl[1] ? (p3_target_pixel ? 24'hFFFFFF : 24'h000000) :
        p3_ctrl[5] ? p3_gray24 :
        overlay_pixel ? 24'hFF0000 : p3_rgb;

    assign s_axis_tready = p1_ready;
    rgb_isp_ycocg_axis #(.IMAGE_WIDTH(IMAGE_WIDTH), .IMAGE_HEIGHT(IMAGE_HEIGHT)) color_detail (
        .clk(aclk), .resetn(aresetn), .enable(p4_sharpen),
        .denoise_enable(p4_denoise), .tone_enable(p4_tone), .chroma_enable(p4_chroma),
        .s_data(p4_data), .s_valid(p4_valid), .s_ready(sharpen_ready),
        .s_user(p4_user), .s_last(p4_last), .busy(sharpen_busy),
        .m_data(m_axis_tdata), .m_valid(m_axis_tvalid), .m_ready(m_axis_tready),
        .m_user(m_axis_tuser), .m_last(m_axis_tlast)
    );

    always @(posedge aclk) begin
        if (!aresetn) begin
            p1_valid <= 1'b0; p2_valid <= 1'b0;
            p3_valid <= 1'b0; p4_valid <= 1'b0;
            p1_rgb <= 24'd0; p2_rgb <= 24'd0; p3_rgb <= 24'd0; p4_data <= 24'd0;
            p1_gray <= 8'd0; p2_gray <= 8'd0; p3_gray <= 8'd0;
            p1_ctrl <= 13'd0; p2_ctrl <= 13'd0; p3_ctrl <= 13'd0;
            p4_sharpen <= 1'b0; p4_denoise <= 0; p4_tone <= 0; p4_chroma <= 0;
            p1_threshold <= DEFAULT_THRESHOLD; p2_threshold <= DEFAULT_THRESHOLD;
            active_ctrl <= 13'd0; active_threshold <= DEFAULT_THRESHOLD;
            active_roi_x0 <= 0; active_roi_x1 <= IMAGE_WIDTH;
            active_roi_y0 <= 0; active_roi_y1 <= IMAGE_HEIGHT;
            p1_user <= 1'b0; p1_last <= 1'b0;
            p2_user <= 1'b0; p2_last <= 1'b0;
            p3_user <= 1'b0; p3_last <= 1'b0;
            p4_user <= 1'b0; p4_last <= 1'b0;
            p2_x <= 0; p2_y <= 0; p3_x <= 0; p3_y <= 0; p4_x <= 0; p4_y <= 0;
            p2_window_sum <= 12'd0; p2_window_valid <= 1'b0;
            p3_hit_count <= 4'd0; p3_window_valid <= 1'b0;
            p3_single_hit <= 1'b0; p4_target_pixel <= 1'b0;
            p2_roi <= 1'b0; p3_roi <= 1'b0;
            gray_row0_d1 <= 0; gray_row0_d2 <= 0;
            gray_row1_d1 <= 0; gray_row1_d2 <= 0;
            gray_row2_d1 <= 0; gray_row2_d2 <= 0;
            bin_row0_d1 <= 0; bin_row0_d2 <= 0;
            bin_row1_d1 <= 0; bin_row1_d2 <= 0;
            bin_row2_d1 <= 0; bin_row2_d2 <= 0;
        end else begin
            if (p4_ready) begin
                p4_valid <= p3_valid;
                if (p3_valid) begin
                    p4_data <= selected_data;
                    p4_sharpen <= p3_ctrl[0] && p3_ctrl[8] && !p3_ctrl[9] &&
                                  !p3_ctrl[1] && !p3_ctrl[5];
                    p4_denoise <= p3_ctrl[0] && p3_ctrl[10] && !p3_ctrl[9] && !p3_ctrl[1] && !p3_ctrl[5];
                    p4_tone <= p3_ctrl[0] && p3_ctrl[11] && !p3_ctrl[9] && !p3_ctrl[1] && !p3_ctrl[5];
                    p4_chroma <= p3_ctrl[0] && p3_ctrl[12] && !p3_ctrl[9] && !p3_ctrl[1] && !p3_ctrl[5];
                    p4_user <= p3_user; p4_last <= p3_last;
                    p4_x <= p3_x; p4_y <= p3_y;
                    p4_target_pixel <= p3_target_pixel;
                end
            end
            if (p3_ready) begin
                p3_valid <= p2_valid;
                if (p2_valid) begin
                    p3_rgb <= p2_rgb; p3_gray <= p2_processed_gray;
                    p3_ctrl <= p2_ctrl;
                    p3_user <= p2_user; p3_last <= p2_last;
                    p3_x <= p2_x; p3_y <= p2_y;
                    p3_hit_count <= binary_hit_count;
                    p3_window_valid <= binary_window_valid;
                    p3_single_hit <= p2_threshold_single;
                    p3_roi <= p2_roi;
                end
            end
            if (p2_ready) begin
                p2_valid <= p1_valid;
                if (p1_valid) begin
                    p2_rgb <= p1_rgb; p2_gray <= p1_gray;
                    p2_ctrl <= p1_ctrl; p2_threshold <= p1_threshold;
                    p2_user <= p1_user; p2_last <= p1_last;
                    p2_x <= p1_x; p2_y <= p1_y;
                    p2_window_sum <= gray_window_valid
                                     ? gray_window_sum : {4'd0, p1_gray};
                    p2_window_valid <= gray_window_valid;
                    p2_roi <= sample_roi;
                end
            end
            if (p1_ready) begin
                p1_valid <= s_axis_tvalid;
                if (s_axis_tvalid) begin
                    p1_rgb <= s_axis_tdata; p1_gray <= in_gray;
                    p1_user <= s_axis_tuser; p1_last <= s_axis_tlast;
                    p1_ctrl <= s_axis_tuser ? ctl_vector : active_ctrl;
                    p1_threshold <= s_axis_tuser ? ctl_threshold : active_threshold;
                    if (s_axis_tuser) begin
                        active_ctrl <= ctl_vector;
                        active_threshold <= ctl_threshold;
                    end
                end
            end

            if (p1_move) begin
                if (p1_user) begin
                    active_roi_x0 <= roi_x0; active_roi_x1 <= roi_x1;
                    active_roi_y0 <= roi_y0; active_roi_y1 <= roi_y1;
                end
                gray_line_2[gray_addr] <= gray_row1_now;
                gray_line_1[gray_addr] <= p1_gray;
                if (p1_x == 0) begin
                    gray_row0_d2 <= 0; gray_row0_d1 <= p1_gray;
                    gray_row1_d2 <= 0; gray_row1_d1 <= gray_row1_now;
                    gray_row2_d2 <= 0; gray_row2_d1 <= gray_row2_now;
                end else begin
                    gray_row0_d2 <= gray_row0_d1; gray_row0_d1 <= p1_gray;
                    gray_row1_d2 <= gray_row1_d1; gray_row1_d1 <= gray_row1_now;
                    gray_row2_d2 <= gray_row2_d1; gray_row2_d1 <= gray_row2_now;
                end
            end
            if (p2_move) begin
                binary_line_2[binary_addr] <= bin_row1_now;
                binary_line_1[binary_addr] <= p2_threshold_single;
                if (p2_x == 0) begin
                    bin_row0_d2 <= 0; bin_row0_d1 <= p2_threshold_single;
                    bin_row1_d2 <= 0; bin_row1_d1 <= bin_row1_now;
                    bin_row2_d2 <= 0; bin_row2_d1 <= bin_row2_now;
                end else begin
                    bin_row0_d2 <= bin_row0_d1; bin_row0_d1 <= p2_threshold_single;
                    bin_row1_d2 <= bin_row1_d1; bin_row1_d1 <= bin_row1_now;
                    bin_row2_d2 <= bin_row2_d1; bin_row2_d1 <= bin_row2_now;
                end
            end
        end
    end

    // Independent shape/photometric monitor.  It intentionally observes the
    // final output stream rather than the sensor-side counters: a short line,
    // missing line or early frame sync is therefore visible to software even
    // when the camera register table still claims the requested dimensions.
    always @(posedge aclk) begin
        if (!aresetn) begin
            quality_luma_sum <= 0;
            quality_dark <= 0;
            quality_saturated <= 0;
            quality_noise <= 0;
            quality_shape_rows <= 0;
            quality_shape_pixels <= 0;
            quality_shape_flags <= 0;
            metric_luma_work <= 0;
            metric_dark_work <= 0;
            metric_saturated_work <= 0;
            metric_noise_work <= 0;
            metric_shape_pixels_work <= 0;
            metric_rows_work <= 0;
            metric_line_pixels_work <= 0;
            metric_active <= 1'b0;
            metric_published <= 1'b0;
            metric_bad_shape <= 1'b0;
            metric_seen_sof <= 1'b0;
            metric_seen_last <= 1'b0;
            metric_x_count <= 0;
            metric_y_count <= 0;
            metric_roi_x0 <= 0;
            metric_roi_x1 <= IMAGE_WIDTH;
            metric_roi_y0 <= 0;
            metric_roi_y1 <= IMAGE_HEIGHT;
            metric_previous_gray <= 0;
            metric_previous_valid <= 1'b0;
            metric_previous_roi <= 1'b0;
            metric_pipe_valid <= 1'b0;
            metric_pipe_sof <= 1'b0;
            metric_pipe_last <= 1'b0;
            metric_pipe_frame_end <= 1'b0;
            metric_pipe_line_bad <= 1'b0;
            metric_pipe_row_start <= 1'b0;
            metric_pipe_roi <= 1'b0;
            metric_pipe_gray <= 0;
        end else begin
            // Process the previously registered output beat.  All stream
            // sidebands are carried with the pixel, so frame/line checks keep
            // their original meaning while the arithmetic gets a full cycle.
            if (metric_pipe_valid) begin
            if (metric_pipe_sof) begin
                // A new SOF publishes a preceding frame that never reached
                // the expected final line.  The next frame can still be
                // measured independently after this recovery point.
                if (metric_active && !metric_published) begin
                    quality_luma_sum <= metric_luma_work;
                    quality_dark <= metric_dark_work;
                    quality_saturated <= metric_saturated_work;
                    quality_noise <= metric_noise_work;
                    quality_shape_rows <= metric_rows_work;
                    quality_shape_pixels <= metric_shape_pixels_work;
                    quality_shape_flags <= {
                        26'd0,
                        (metric_shape_pixels_work == EXPECTED_FRAME_PIXELS),
                        (metric_rows_work == IMAGE_HEIGHT),
                        (metric_bad_shape || !metric_seen_last ||
                         (metric_shape_pixels_work != EXPECTED_FRAME_PIXELS) ||
                         (metric_rows_work != IMAGE_HEIGHT)),
                        metric_seen_last,
                        metric_seen_sof,
                        1'b0
                    };
                end

                metric_active <= 1'b1;
                metric_published <= 1'b0;
                metric_bad_shape <= 1'b0;
                metric_seen_sof <= 1'b1;
                metric_seen_last <= metric_pipe_last;
                metric_luma_work <= metric_pipe_roi ? metric_pipe_gray : 0;
                metric_dark_work <= metric_pipe_roi && (metric_pipe_gray <= 8'd16) ? 1 : 0;
                metric_saturated_work <= metric_pipe_roi && (metric_pipe_gray >= 8'd240) ? 1 : 0;
                metric_noise_work <= 0;
                metric_shape_pixels_work <= 1;
                metric_rows_work <= metric_pipe_last ? 1 : 0;
                metric_line_pixels_work <= metric_pipe_last ? 0 : 1;
                metric_previous_gray <= metric_pipe_gray;
                metric_previous_valid <= 1'b1;
                metric_previous_roi <= metric_pipe_roi;
                if (metric_pipe_line_bad)
                    metric_bad_shape <= 1'b1;

                // This is only reachable for a one-line parameterization,
                // but keeping it explicit makes the monitor self-consistent
                // in small simulation configurations.
                if (metric_pipe_last && (IMAGE_HEIGHT == 1)) begin
                    quality_luma_sum <= metric_pipe_roi ? metric_pipe_gray : 0;
                    quality_dark <= metric_pipe_roi && (metric_pipe_gray <= 8'd16) ? 1 : 0;
                    quality_saturated <= metric_pipe_roi && (metric_pipe_gray >= 8'd240) ? 1 : 0;
                    quality_noise <= 0;
                    quality_shape_rows <= 1;
                    quality_shape_pixels <= 1;
                    quality_shape_flags <= {
                        26'd0,
                        (1 == EXPECTED_FRAME_PIXELS),
                        (1 == IMAGE_HEIGHT),
                        metric_pipe_line_bad,
                        1'b1,
                        1'b1,
                        !metric_pipe_line_bad && (1 == EXPECTED_FRAME_PIXELS)
                    };
                    metric_active <= 1'b0;
                    metric_published <= 1'b1;
                end
            end else if (metric_active) begin
                metric_luma_work <= metric_luma_work +
                    (metric_pipe_roi ? metric_pipe_gray : 0);
                metric_dark_work <= metric_dark_work +
                    (metric_pipe_roi && (metric_pipe_gray <= 8'd16) ? 1 : 0);
                metric_saturated_work <= metric_saturated_work +
                    (metric_pipe_roi && (metric_pipe_gray >= 8'd240) ? 1 : 0);
                metric_noise_work <= metric_noise_work +
                    (metric_pipe_noise_sample ? metric_pipe_gray_delta : 0);
                metric_shape_pixels_work <= metric_shape_pixels_work + 1'b1;
                metric_previous_gray <= metric_pipe_gray;
                metric_previous_valid <= 1'b1;
                metric_previous_roi <= metric_pipe_roi;
                metric_seen_last <= metric_seen_last || metric_pipe_last;

                if (metric_pipe_last) begin
                    metric_rows_work <= metric_rows_work + 1'b1;
                    metric_line_pixels_work <= 0;
                    metric_bad_shape <= metric_bad_shape || metric_pipe_line_bad;
                end else begin
                    metric_line_pixels_work <= metric_line_pixels_work + 1'b1;
                end

                if (metric_pipe_frame_end) begin
                    quality_luma_sum <= metric_luma_work +
                        (metric_pipe_roi ? metric_pipe_gray : 0);
                    quality_dark <= metric_dark_work +
                        (metric_pipe_roi && (metric_pipe_gray <= 8'd16) ? 1 : 0);
                    quality_saturated <= metric_saturated_work +
                        (metric_pipe_roi && (metric_pipe_gray >= 8'd240) ? 1 : 0);
                    quality_noise <= metric_noise_work +
                        (metric_pipe_noise_sample ? metric_pipe_gray_delta : 0);
                    quality_shape_rows <= metric_rows_work + 1'b1;
                    quality_shape_pixels <= metric_shape_pixels_work + 1'b1;
                    quality_shape_flags <= {
                        26'd0,
                        (metric_shape_pixels_work + 1'b1 == EXPECTED_FRAME_PIXELS),
                        (metric_rows_work + 1'b1 == IMAGE_HEIGHT),
                        (metric_bad_shape || metric_pipe_line_bad ||
                         (metric_shape_pixels_work + 1'b1 != EXPECTED_FRAME_PIXELS) ||
                         (metric_rows_work + 1'b1 != IMAGE_HEIGHT)),
                        1'b1,
                        metric_seen_sof,
                        !metric_bad_shape && !metric_pipe_line_bad &&
                        (metric_shape_pixels_work + 1'b1 == EXPECTED_FRAME_PIXELS) &&
                        (metric_rows_work + 1'b1 == IMAGE_HEIGHT)
                    };
                    metric_active <= 1'b0;
                    metric_published <= 1'b1;
                end
            end
            end

            // Capture the current accepted output beat for the next cycle.
            metric_pipe_valid <= metric_fire;
            if (metric_fire) begin
                // Advance these counters on acceptance, not when the previous
                // registered beat is consumed. Otherwise consecutive beats
                // see stale coordinates at TLAST and falsely fail shape checks.
                metric_x_count <= m_axis_tlast ? 0 : metric_x + 1'b1;
                metric_y_count <= m_axis_tlast ?
                    (metric_frame_end ? 0 : metric_y + 1'b1) : metric_y;
                if (m_axis_tuser) begin
                    metric_roi_x0 <= roi_x0;
                    metric_roi_x1 <= roi_x1;
                    metric_roi_y0 <= roi_y0;
                    metric_roi_y1 <= roi_y1;
                end
                metric_pipe_sof <= m_axis_tuser;
                metric_pipe_last <= m_axis_tlast;
                metric_pipe_frame_end <= metric_frame_end;
                metric_pipe_line_bad <= metric_line_bad;
                metric_pipe_row_start <= (metric_x == 0);
                metric_pipe_roi <= metric_roi;
                metric_pipe_gray <= metric_gray;
            end
        end
    end

    // Coordinates advance at p1; statistics accumulate on line-filter input.
    // The next accepted input SOF publishes the preceding input-frame stats.
    // FRAME_COUNT is not a DDR completion fence; software also checks clean
    // VDMA frame-store advances before returning a captured frame.
    always @(posedge aclk) begin
        if (!aresetn) begin
            x_count <= 0; y_count <= 0;
            min_x <= {X_WIDTH{1'b1}}; max_x <= 0;
            min_y <= {Y_WIDTH{1'b1}}; max_y <= 0;
            overlay_min_x <= 0; overlay_max_x <= 0;
            overlay_min_y <= 0; overlay_max_y <= 0;
            area_work <= 0; bbox_valid <= 1'b0;
            overlay_bbox_valid <= 1'b0; frame_seen <= 1'b0;
            stat_target_x <= 0; stat_target_y <= 0;
            stat_target_area <= 0; stat_frame_count <= 0;
            stat_new <= 1'b0; stat_frame_locked <= 1'b0;
            quality_frame <= 0; quality_edges <= 0;
            quality_clipped <= 0; quality_pixels <= 0;
            edges_work <= 0; clipped_work <= 0; pixels_work <= 0;
            output_x <= 0; output_y <= 0;
            quality_roi_x0 <= 0; quality_roi_x1 <= IMAGE_WIDTH;
            quality_roi_y0 <= 0; quality_roi_y1 <= IMAGE_HEIGHT;
            quality_sample_valid <= 0; quality_sample_roi <= 0;
            quality_sample_sof <= 0; quality_sample_end <= 0;
            quality_sample_row_start <= 0; quality_sample_clipped <= 0;
            quality_sample_gray <= 0;
            quality_count_valid <= 0; quality_count_sof <= 0;
            quality_count_end <= 0; quality_count_roi <= 0;
            quality_count_clipped <= 0; quality_count_edge <= 0;
            previous_gray <= 0; previous_roi <= 1'b0;
        end else begin
            if (status_read) stat_new <= 1'b0;

            quality_sample_valid <= m_axis_tvalid && m_axis_tready;
            if (m_axis_tvalid && m_axis_tready) begin
                quality_sample_gray <= output_gray;
                quality_sample_roi <= output_roi;
                quality_sample_sof <= m_axis_tuser;
                quality_sample_end <= m_axis_tlast &&
                                      (quality_y == IMAGE_HEIGHT-1);
                quality_sample_row_start <= (quality_x == 0);
                quality_sample_clipped <= sample_clipped;
                if (m_axis_tuser) begin
                    quality_roi_x0 <= roi_x0; quality_roi_x1 <= roi_x1;
                    quality_roi_y0 <= roi_y0; quality_roi_y1 <= roi_y1;
                end
                if (m_axis_tlast) begin
                    output_x <= 0;
                    if (quality_y == IMAGE_HEIGHT-1) output_y <= 0;
                    else output_y <= quality_y + 1'b1;
                end else begin
                    output_x <= quality_x + 1'b1;
                    output_y <= quality_y;
                end
            end

            quality_count_valid <= quality_sample_valid;
            if (quality_sample_valid) begin
                previous_gray <= quality_sample_gray;
                previous_roi <= quality_sample_roi;
                quality_count_sof <= quality_sample_sof;
                quality_count_end <= quality_sample_end;
                quality_count_roi <= quality_sample_roi;
                quality_count_clipped <= quality_sample_clipped;
                quality_count_edge <= sample_edge;
            end

            if (quality_count_valid) begin
                if (quality_count_sof) begin
                    edges_work <= 0;
                    clipped_work <= quality_count_clipped ? 32'd1 : 32'd0;
                    pixels_work <= quality_count_roi ? 32'd1 : 32'd0;
                end else begin
                    if (quality_count_edge) edges_work <= edges_work + 1'b1;
                    if (quality_count_clipped) clipped_work <= clipped_work + 1'b1;
                    if (quality_count_roi) pixels_work <= pixels_work + 1'b1;
                end
                if (quality_count_end) begin
                    quality_frame <= quality_frame + 1'b1;
                    quality_edges <= edges_work +
                                     (!quality_count_sof && quality_count_edge);
                    quality_clipped <= clipped_work +
                                       (!quality_count_sof && quality_count_clipped);
                    quality_pixels <= pixels_work +
                                      (!quality_count_sof && quality_count_roi);
                end
            end

            if (p1_move) begin
                if (p1_user) begin
                    x_count <= p1_last ? 0 : 1;
                    y_count <= p1_last ? y_count + 1'b1 : 0;
                end else if (p1_last) begin
                    x_count <= 0; y_count <= y_count + 1'b1;
                end else begin
                    x_count <= x_count + 1'b1;
                end
            end

            if (p4_move) begin
                if (p4_user) begin
                    if (frame_seen) begin
                        stat_target_area <= area_work;
                        stat_target_x <= bbox_valid
                            ? ({1'b0, min_x} + {1'b0, max_x}) >> 1 : 0;
                        stat_target_y <= bbox_valid
                            ? ({1'b0, min_y} + {1'b0, max_y}) >> 1 : 0;
                        stat_frame_count <= stat_frame_count + 1'b1;
                        stat_new <= 1'b1;
                        stat_frame_locked <= 1'b1;
                        overlay_min_x <= min_x; overlay_max_x <= max_x;
                        overlay_min_y <= min_y; overlay_max_y <= max_y;
                        overlay_bbox_valid <= bbox_valid;
                    end
                    frame_seen <= 1'b1;
                    area_work <= p4_target_pixel ? 32'd1 : 32'd0;
                    bbox_valid <= p4_target_pixel;
                    min_x <= p4_x; max_x <= p4_x;
                    min_y <= p4_y; max_y <= p4_y;
                end else if (p4_target_pixel) begin
                    area_work <= area_work + 1'b1;
                    if (!bbox_valid) begin
                        bbox_valid <= 1'b1;
                        min_x <= p4_x; max_x <= p4_x;
                        min_y <= p4_y; max_y <= p4_y;
                    end else begin
                        if (p4_x < min_x) min_x <= p4_x;
                        if (p4_x > max_x) max_x <= p4_x;
                        if (p4_y < min_y) min_y <= p4_y;
                        if (p4_y > max_y) max_y <= p4_y;
                    end
                end
            end
        end
    end

    wire busy = p1_valid || p2_valid || p3_valid || p4_valid || sharpen_busy;

    // Debug bit map (kept stable for Hardware Manager waveform decoding):
    //   [23:0]    input RGB       [47:24]   output RGB
    //   [51:48]   input  {valid,ready,user,last}
    //   [55:52]   output {valid,ready,user,last}
    // Servo diagnosis map (fixed width at every supported simulation clock):
    // [159] PWM [158] enable [157:146] active pulse us [145:142] KEY_STATUS
    // [141] armed [140:109] trigger count [108:77] trigger frame
    // [76] manual enable [75] boundary [74:60] period us [59:53] sub-us
    // [52:37] frame count [36:18] area [17:6] target x [5:0] target y
    wire [6:0] servo_debug_sub_us = servo_sub_us_count;
    assign debug_probe = {
        servo_pwm,
        servo_active_enable,
        servo_active_pulse_us,
        key_status,
        key_capture_armed,
        key_trigger_count,
        key_trigger_frame,
        servo_command_enable,
        servo_period_boundary,
        servo_period_us,
        servo_debug_sub_us,
        stat_frame_count[15:0],
        stat_target_area[18:0],
        stat_target_x,
        stat_target_y[5:0]
    };

    //-------------------------------------------------------------------------
    // AXI4-Lite write channel
    //-------------------------------------------------------------------------
    reg        aw_pending, w_pending;
    reg [6:0]  awaddr_hold;
    reg [31:0] wdata_hold;
    reg [3:0]  wstrb_hold;
    wire [4:0] aw_word = awaddr_hold[6:2];

    // AW and W are independent AXI4-Lite channels.  Holding them separately
    // avoids the non-compliant assumption that both VALID signals coincide.
    assign s_axi_awready = !aw_pending && !s_axi_bvalid;
    assign s_axi_wready  = !w_pending  && !s_axi_bvalid;

    always @(posedge aclk) begin
        if (!aresetn) begin
            aw_pending     <= 1'b0;
            w_pending      <= 1'b0;
            awaddr_hold    <= 7'd0;
            wdata_hold     <= 32'd0;
            wstrb_hold     <= 4'd0;
            s_axi_bvalid   <= 1'b0;
            s_axi_bresp    <= 2'b00;
            ctl_enable          <= 1'b0;   // pass-through until the PS enables it
            ctl_threshold_enable<= 1'b0;
            ctl_overlay_enable  <= 1'b0;
            ctl_overlay_mode    <= 1'b0;
            ctl_invert          <= 1'b0;
            ctl_gray_enable     <= 1'b0;
            ctl_mean_filter_enable <= 1'b0;
            ctl_majority_enable <= 1'b0;
            ctl_sharpen_enable <= 1'b0;
            ctl_roi_mask_enable <= 1'b0; ctl_denoise_enable <= 0; ctl_tone_enable <= 0; ctl_chroma_enable <= 0;
            ctl_threshold       <= DEFAULT_THRESHOLD;
            roi_x0 <= 220; roi_x1 <= 510;
            roi_y0 <= 40; roi_y1 <= 305;
            servo_command_enable <= 1'b0;
            servo_command_pulse_us <= 12'd1500;
            key_capture_armed <= 1'b0;
            key_software_only <= 1'b0; key_software_start <= 1'b0;
            key_capture_ticks <= 12'd750;
        end else begin
            key_software_start <= 1'b0;
            if (s_axi_awready && s_axi_awvalid) begin
                awaddr_hold <= s_axi_awaddr;
                aw_pending <= 1'b1;
            end
            if (s_axi_wready && s_axi_wvalid) begin
                wdata_hold <= s_axi_wdata;
                wstrb_hold <= s_axi_wstrb;
                w_pending <= 1'b1;
            end

            if (!s_axi_bvalid && aw_pending && w_pending) begin
                aw_pending <= 1'b0;
                w_pending <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp <= 2'b00;
                case (aw_word)
                    5'h0: begin
                      if (wstrb_hold[1]) begin
                          ctl_sharpen_enable <= wdata_hold[8];
                          ctl_roi_mask_enable <= wdata_hold[9];
                          ctl_denoise_enable <= wdata_hold[10];
                          ctl_tone_enable <= wdata_hold[11];
                          ctl_chroma_enable <= wdata_hold[12];
                      end
                      if (wstrb_hold[0]) begin
                        ctl_enable             <= wdata_hold[0];
                        ctl_threshold_enable   <= wdata_hold[1];
                        ctl_overlay_enable     <= wdata_hold[2];
                        ctl_overlay_mode       <= wdata_hold[3];
                        ctl_invert             <= wdata_hold[4];
                        ctl_gray_enable        <= wdata_hold[5];
                        ctl_mean_filter_enable <= wdata_hold[6];
                        ctl_majority_enable    <= wdata_hold[7];
                      end
                    end
                    5'h1: if (wstrb_hold[0]) ctl_threshold <= wdata_hold[7:0];
                    5'h8: if (wstrb_hold[0])
                        servo_command_enable <= wdata_hold[0];
                    5'h9: if (wstrb_hold[1:0] != 2'b00) begin
                        if (wdata_hold < 32'd500)
                            servo_command_pulse_us <= 12'd500;
                        else if (wdata_hold > 32'd2500)
                            servo_command_pulse_us <= 12'd2500;
                        else
                            servo_command_pulse_us <= wdata_hold[11:0];
                    end
                    5'hB: if (wstrb_hold[0]) begin
                        key_capture_armed <= wdata_hold[0];
                        key_software_only <= wdata_hold[1];
                        key_software_start <= wdata_hold[0] && wdata_hold[1] && wdata_hold[2];
                    end
                    5'hC: if (wstrb_hold[1:0] == 2'b11) begin
                        if (wdata_hold < 1) key_capture_ticks <= 12'd1;
                        else if (wdata_hold > 3000) key_capture_ticks <= 12'd3000;
                        else key_capture_ticks <= wdata_hold[11:0];
                    end
                    5'h10: if (wstrb_hold == 4'hF) begin
                        roi_x0 <= wdata_hold[X_WIDTH-1:0];
                        roi_x1 <= wdata_hold[16 +: X_WIDTH];
                    end
                    5'h11: if (wstrb_hold == 4'hF) begin
                        roi_y0 <= wdata_hold[Y_WIDTH-1:0];
                        roi_y1 <= wdata_hold[16 +: Y_WIDTH];
                    end
                    default: ;
                endcase
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
        end
    end

    //-------------------------------------------------------------------------
    // AXI4-Lite read channel
    //-------------------------------------------------------------------------
    wire [4:0] ar_word = s_axi_araddr[6:2];

    reg [31:0] read_mux;
    always @(*) begin
        case (ar_word)
            5'h0: read_mux = {19'd0, ctl_chroma_enable, ctl_tone_enable, ctl_denoise_enable, ctl_roi_mask_enable, ctl_sharpen_enable,
                              ctl_majority_enable, ctl_mean_filter_enable,
                              ctl_gray_enable, ctl_invert, ctl_overlay_mode,
                              ctl_overlay_enable, ctl_threshold_enable, ctl_enable};
            5'h1: read_mux = {24'd0, ctl_threshold};
            5'h2: read_mux = {29'd0, busy, stat_frame_locked, stat_new};
            5'h3: read_mux = {{(32-X_WIDTH){1'b0}}, stat_target_x};
            5'h4: read_mux = {{(32-Y_WIDTH){1'b0}}, stat_target_y};
            5'h5: read_mux = stat_target_area;
            5'h6: read_mux = stat_frame_count;
            5'h7: read_mux = 32'h0005_000A;
            5'h8: read_mux = {31'd0, servo_command_enable};
            5'h9: read_mux = {20'd0, servo_command_pulse_us};
            5'hA: read_mux = {20'd0, servo_active_pulse_us};
            5'hB: read_mux = {30'd0, key_software_only, key_capture_armed};
            5'hC: read_mux = {20'd0, key_capture_ticks};
            5'hD: read_mux = {28'd0, key_status};
            5'hE: read_mux = key_trigger_count;
            5'hF: read_mux = key_trigger_frame;
            5'h10: read_mux = {4'd0, roi_x1, 4'd0, roi_x0};
            5'h11: read_mux = {4'd0, roi_y1, 4'd0, roi_y0};
            5'h12: read_mux = quality_frame;
            5'h13: read_mux = quality_edges;
            5'h14: read_mux = quality_clipped;
            5'h15: read_mux = quality_pixels;
            5'h16: read_mux = quality_luma_sum;
            5'h17: read_mux = quality_dark;
            5'h18: read_mux = IMAGE_WIDTH;
            5'h19: read_mux = IMAGE_HEIGHT;
            5'h1A: read_mux = 32'h53544331;
            5'h1C: read_mux = quality_saturated;
            5'h1D: read_mux = quality_noise;
            5'h1E: read_mux = {quality_shape_rows, quality_shape_flags};
            5'h1F: read_mux = quality_shape_pixels;
            default: read_mux = 32'hDEAD_BEEF;
        endcase
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            rstate        <= R_IDLE;
            s_axi_arready <= 1'b0;
            s_axi_rvalid  <= 1'b0;
            s_axi_rresp   <= 2'b00;
            s_axi_rdata   <= 32'd0;
        end else begin
            case (rstate)
                R_IDLE: begin
                    s_axi_arready <= 1'b1;
                    if (s_axi_arvalid && s_axi_arready) begin
                        s_axi_arready <= 1'b0;
                        s_axi_rdata   <= read_mux;
                        s_axi_rresp   <= 2'b00;
                        s_axi_rvalid  <= 1'b1;
                        rstate        <= R_RESP;
                    end
                end

                R_RESP: begin
                    if (s_axi_rready) begin
                        s_axi_rvalid <= 1'b0;
                        rstate       <= R_IDLE;
                    end
                end

                default: rstate <= R_IDLE;
            endcase
        end
    end

endmodule
