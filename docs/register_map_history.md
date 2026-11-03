# FPGA Control Register Map

The AXI-Lite control block must expose these 32-bit registers at its base
address. All values are little-endian.

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| 0x00 | CONTROL | RW | bit0 enable, bit1 threshold, bit2 overlay, bit3 cross mode, bit4 invert, bit5 grayscale, bit6 mean filter, bit7 majority, bit8 sharpen, bit9 fixed ROI mask, bit10 color denoise, bit11 tone |
| 0x04 | THRESHOLD | RW | 8-bit threshold; zero selects the HDL default |
| 0x08 | STATUS | RO | bit0 new frame (cleared on read), bit1 frame locked, bit2 busy |
| 0x0C | TARGET_X | RO | last target center X |
| 0x10 | TARGET_Y | RO | last target center Y |
| 0x14 | TARGET_AREA | RO | last target area |
| 0x18 | FRAME_COUNT | RO | previous input frame published at the next SOF; not a DDR completion flag |
| 0x1C | VERSION | RO | `0x00050007` current clean HD servo; `0x00050006` legacy HD; `0x00050005` A4988 |
| 0x20 | SERVO_CTRL | RW | bit0 manual command enable; after first command, clearing it selects 1500 us neutral |
| 0x24 | SERVO_PULSE_US | RW | clamped to 500..2500 us |
| 0x28 | SERVO_ACTIVE_US | RO | latched pulse width; does not indicate whether PWM is enabled |
| 0x2C | KEY_CAPTURE | RW | bit0 arm button mode; defaults to 0 |
| 0x30 | CAPTURE_TICKS | RW | 20 ms periods, clamped to 1..3000, default 750 |
| 0x34 | KEY_STATUS | RO | bit0 debounced pressed, bit1 ready, bit2 pending, bit3 rotating |
| 0x38 | TRIGGER_COUNT | RO | increments at first rotation PWM boundary |
| 0x3C | TRIGGER_FRAME | RO | FRAME_COUNT latched at that same boundary |
| 0x40 | ROI_X | RW | x0 in bits 11:0, exclusive x1 in bits 27:16; default 220..510 |
| 0x44 | ROI_Y | RW | y0 in bits 11:0, exclusive y1 in bits 27:16; default 40..305 |
| 0x48 | QUALITY_FRAME | RO | count of accepted output frames; advances at final output pixel |
| 0x4C | QUALITY_EDGES | RO | horizontal luminance changes >=12 inside ROI |
| 0x50 | QUALITY_CLIPPED | RO | ROI pixels with luminance <=16 or >=240 |
| 0x54 | QUALITY_PIXELS | RO | number of ROI pixels in completed output frame |
| 0x58 | MOTOR_STEPS | RO | emitted STEP rising edges in current/last KEY0 move; A4988 overlay |
| 0x5C | MOTOR_FRAME_STEPS | RO | STEP count latched with QUALITY_FRAME at output-frame end; A4988 overlay |
| 0x60 | IMAGE_WIDTH | RO | native image width; HD versions 6/7 |
| 0x64 | IMAGE_HEIGHT | RO | native image height; HD versions 6/7 |

For VERSION `0x00050005`, KEY_CAPTURE arms an A4988 KEY0 move of exactly 400
STEP rising edges at 25 Hz. KEY_STATUS bit3 is high during the move, including
the driver setup and last pulse. TRIGGER_COUNT and TRIGGER_FRAME latch at the
first STEP rising edge. The legacy SERVO registers are not connected to an
external output in this overlay; CAPTURE_TICKS does not set stepper duration.
EN is active low on V18, STEP is U17, and DIR=0 is Y16. Full-step direct drive
of a 200-step motor is assumed for the two-revolution count.

In button mode PWM stays enabled: waiting/stopped = 1500 us; rotating =
SERVO_PULSE_US clamped to 1551..2000 us. The duration is counted in hardware.
KEY0 L14 is active low with a pull-up, two synchronizer stages and 20 ms
debounce. A release is required before each accepted press. Busy presses
are ignored and do not extend the duration. Disarming clears the run.
After either button mode or manual mode has been enabled, disarming and
clearing SERVO_CTRL keep sending 1500 us neutral every 20 ms until FPGA reset.
PWM is initially disabled immediately after reset. Version-family detection
uses VERSION & 0xFFFF0000 == 0x00050000.

Pulse widths and durations require an actual 100 MHz FCLK0. Before loading
the overlay, the runtime reads PCW_CRYSTAL_PERIPHERAL_FREQMHZ from its HWH
and sets PYNQ's reference accordingly (33.333333 MHz on this board). PYNQ's
default 50 MHz assumption otherwise stretches all times by 1.5. A raw
Clocks.fclk0_mhz read without this calibration is not sufficient evidence.


## Clean HD image modes (VERSION 0x00050007)

| Mode | CONTROL | Processing on PL |
| --- | ---: | --- |
| raw | 0x001 | existing sensor RGB passed through |
| sharp | 0x101 | legacy sharpening |
| denoise | 0x401 | 3x3 color-range-gated Gaussian |
| tone | 0x801 | shared RGB gain from luminance LUT |
| clean | 0xD01 | denoise, mild sharpening, then tone |

Bit10/11 require version 0x00050007; software rejects them on older overlays
before writing CONTROL. Pixel controls latch at SOF. Threshold, grayscale and
ROI-mask output bypass new ISP processing. Legacy bit8-only sharp retains its
previous gain and clipping behavior. KEY0 and V7 PWM registers are unchanged.
Frame headers carry the selected CONTROL; capture manifests also record
fpga_sharpen_requested, fpga_denoise_requested and fpga_tone_requested.
The selected control is reported by software; wait for completed frames after
changing modes before using a frame for an A/B comparison.

## Ethernet frame format (FPG3/FPG4)

Each frame is fragmented into UDP datagrams because an 800x480 RGB frame is
larger than the maximum UDP payload:

```text
magic[4] = FPG3
width    uint16 little-endian
height   uint16 little-endian
sequence uint32 little-endian
total_length uint32 little-endian
chunk_index uint16 little-endian
chunk_count uint16 little-endian
chunk_length uint16 little-endian
frame_count uint32 little-endian
target_x uint32 little-endian
target_y uint32 little-endian
target_area uint32 little-endian
servo_pulse_us uint32 little-endian
control uint32 little-endian
payload  chunk_length bytes
crc16    uint16 little-endian
```

`control` is the FPGA image-process CONTROL register value. The FPGA also
sends one fixed ROI mask as a zlib-compressed `FPGM` UDP datagram, repeated
three times before arming KEY0. The datagram is `<4sHHI>` (magic, width,
height, compressed byte count), compressed FPGA mask channel, then CRC16 of the
compressed bytes. This is a frame generated by the FPGA, reused for each
photo while ROI coordinates remain fixed. The receiver saves it beside every
photo as `masks/<image filename>.png`.

For each color photo, `FPGQ` uses `<4sIIIII>`: magic, UDP image sequence,
QUALITY_FRAME, QUALITY_EDGES, QUALITY_CLIPPED, QUALITY_PIXELS. It is sent
three times before the image chunks. The runtime accepts a quality snapshot
only when the output frame count stays stable across the completed DDR buffer
copy. Missing FPGQ yields a null manifest quality record.

The A4988 overlay extends `FPGQ` to `<4sIIIIII>` by appending
MOTOR_FRAME_STEPS. The PC receiver accepts both lengths and saves the FPGA
step count with each photo. Reported angle assumes full-step, direct drive;
it is an estimate and does not detect motor stalls.

The FPG3 payload is a width*height*3 RGB888 frame in B, G, R byte order. FPG4 uses the same header and CRC layout with JPEG bytes; the current HD service encodes at quality 97 on ARM/PS, preserving native 1920x1080 dimensions. The PC decodes and saves BMP; this does not undo JPEG loss. Datagrams stay below
the Ethernet MTU to avoid IP fragmentation. `crc16` is CRC-16/CCITT with
initial value `0xFFFF`. The viewer reassembles the frame and displays the image
with FPGA telemetry.

The board sends `FPGS` to UDP port 5002 after camera initialization, before arming KEY0. The PC
viewer opens a waiting window on that signal and replaces it with the first
complete `FPG3` frame. The PC sends image-mode commands to board UDP port 5001:

```text
magic[4] = FPGC
mode     uint8 (0=raw, 1=overlay, 2=threshold, 3=grayscale, 4=sharpen, 5=denoise, 6=tone, 7=clean)
threshold uint8
```

## Capture window events (UDP 5002)

Each event is 16 bytes (<4sIII, little-endian) and is repeated three times:

| Magic | uint32 field 1 | uint32 field 2 | uint32 field 3 |
| --- | --- | --- | --- |
| FPGK | trigger count | duration in ms | FRAME_COUNT at start boundary |
| FPGD | trigger count | duration in ms | complete frames sent |

The receiver deduplicates start/finish events and saves received complete
FPG3 images only inside a session. Start the viewer before pressing KEY0.
FPGS resets session deduplication after a board process restart.
Events and images are UDP; repetition does not guarantee delivery.
FRAME_COUNT advances at stream SOF after the first observed frame, representing
previous-frame statistics. The sender excludes frames until the counter has
advanced at least twice after TRIGGER_FRAME and checks rotating again before
sending. This opens a synchronized window on a running camera, not a hardware
exposure trigger. A frame already being sent may arrive just after rotation ends.

