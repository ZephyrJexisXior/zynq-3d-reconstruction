`timescale 1ns/1ps
// Observation-only CDC. Individual bits can be delayed independently;
// the sampled bus is not used as a coherent pixel or for image processing.
module camera_probe_sync((* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *) input wire clk, input wire [39:0] async_probe,
                         output wire [39:0] sync_probe);
    (* ASYNC_REG = "TRUE" *) reg [39:0] sample_meta = 0;
    (* ASYNC_REG = "TRUE" *) reg [39:0] sample_sync = 0;
    reg previous_cam_href = 1'b0;
    reg previous_vid_active = 1'b0;
    reg previous_cam_vsync = 1'b0;
    reg [15:0] cam_href_total = 16'd0;
    reg [15:0] vid_active_total = 16'd0;
    reg [15:0] cam_href_at_vsync = 16'd0;
    reg [15:0] vid_active_at_vsync = 16'd0;
    reg [11:0] cam_lines_per_frame = 12'd0;
    reg [11:0] vid_lines_per_frame = 12'd0;

    always @(posedge clk) begin
        sample_meta <= async_probe;
        sample_sync <= sample_meta;

        // Count line-valid rising edges in the 100 MHz observation domain.
        // These counters never feed back into the camera or AXI video path.
        previous_cam_href <= sample_sync[8];
        previous_vid_active <= sample_sync[11];
        previous_cam_vsync <= sample_sync[9];

        if (sample_sync[8] && !previous_cam_href)
            cam_href_total <= cam_href_total + 1'b1;
        if (sample_sync[11] && !previous_vid_active)
            vid_active_total <= vid_active_total + 1'b1;

        // Publish the number of camera HREF and bridge active-video lines
        // between successive VSYNC rising edges. The deltas are modulo 4096,
        // comfortably larger than one 5 MP frame's line count.
        if (sample_sync[9] && !previous_cam_vsync) begin
            cam_lines_per_frame <= cam_href_total[11:0] - cam_href_at_vsync[11:0];
            vid_lines_per_frame <= vid_active_total[11:0] - vid_active_at_vsync[11:0];
            cam_href_at_vsync <= cam_href_total;
            vid_active_at_vsync <= vid_active_total;
        end
    end

    // xlconcat In0 is the least-significant field: cam_data [7:0],
    // HREF [8], VSYNC [9], CE [10], active [11], vid_vsync [12],
    // RGB [36:13], overflow [37], reset [38], PCLK [39].
    // Replace only RGB: [36:25] camera lines, [24:13] bridge lines.
    assign sync_probe = {sample_sync[39:37], cam_lines_per_frame,
                         vid_lines_per_frame, sample_sync[12:0]};
endmodule
