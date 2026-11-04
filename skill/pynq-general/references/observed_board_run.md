# Observed Board Run

This is evidence from one read-only session on the deployed camera Overlay. It
is included to preserve measured values and gaps, not as a universal PYNQ
baseline. The overall record remains **incomplete**.

## Updated helper verification

The completed explicit-MMIO helper was subsequently executed on this board
without uploading files or programming the FPGA. The exact script and contract
hashes, full values, and service state are retained in
[board_probe_20261104.json](board_probe_20261104.json). Artifact and MMIO checks
passed. `Overlay.is_loaded()` returned true. The service remained active with
PID 688 before and after the probe. These results supersede the earlier
register-name probe failure for the explicit-offset mode; the missing
`register_map` limitation remains.

The board clock reported 2021; use `host_archived_at` for the evidence archive
date. The raw `observed_at` value is retained and marked unreliable.

The installed PYNQ video driver exposes `AxiVDMA` from `pynq.lib.video.dma`;
importing it directly from `pynq.lib.video` failed on this board. No VDMA channel
was constructed or started during this API inspection.

## Identity and read-only observations

- PYNQ: 2.7.0.
- Overlay pair: `/home/xilinx/usb_top_dual18_20261101_01/vivado/camera_5mp_quality.bit`
  and matching `.hwh`.
- `.bit` SHA-256: `d262ce1c09e6b707f601509177f1b73a048bb7afba4db95eccb0ca7036d33168`.
- `.hwh` SHA-256: `116d416dbbb12d3d455f35a23664154d4e6ae7432ef2c2cd57c00ed6cf78abd5`.
- Discovered IPs: `image_process_0`, `axi_vdma_0`, `clk_wiz_0`, `dual_view_0`,
  `foreground_hint_0`, `processing_system7_0`, `v_tc_0`, and `axi_gpio_0`.
- The custom IP did not expose `register_map` in this PYNQ 2.7.0 environment.
  Reads through the documented application MMIO offsets succeeded.
- Image-processing version readback: `0x0005000A`; dimensions: `2592 x 1944`.
- FCLK0 readback: 150 MHz.
- Frame counter advanced by 3 over 500 ms. Servo enable was 0 and active
  pulse readback was 1500 us. VDMA S2MM status readback was `0x00011000`.
- Existing application manifests contained 10 complete capture rounds with
  18 front images and 4 top frames each; 2 of 12 rounds were incomplete. The
  existing capture log recorded 18 sends and no failures for one round.

## What this does and does not prove

The live register values and capture manifests are observations of the exact
deployed application and Overlay. The existing VDMA capture path was not
started by this validation session. No bitstream was downloaded, no register
was written, no service was stopped or restarted, and no capture was triggered.

The generic `dma_helpers.transfer` helper was not exercised on the board.
Known-vector PL-versus-software comparison was not run. Repeated helper
transfer/error tests, Vivado reports, source revision, FPGA part, and stride
measurement were not collected. Therefore this run is useful hardware evidence
but does not qualify as a complete board validation or a pass for the generic
DMA path.
