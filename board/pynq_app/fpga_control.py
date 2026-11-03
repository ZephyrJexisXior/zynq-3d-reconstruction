"""PYNQ control layer for the generated image-processing overlay."""

import time
import numpy as np
from pathlib import Path
import xml.etree.ElementTree as ET

from pynq import Clocks, MMIO, Overlay, allocate


FRAME_WIDTH = 800
FRAME_HEIGHT = 480
FRAME_SHAPE = (FRAME_HEIGHT, FRAME_WIDTH, 3)
DMA_BUFFER_SHAPE = (3, FRAME_HEIGHT, FRAME_WIDTH, 3)
FULL_5MP_WIDTH = 2592
FULL_5MP_HEIGHT = 1944


CONTROL = 0x00
THRESHOLD = 0x04
STATUS = 0x08
TARGET_X = 0x0C
TARGET_Y = 0x10
TARGET_AREA = 0x14
FRAME_COUNT = 0x18
VERSION = 0x1C
SERVO_CTRL = 0x20
SERVO_PULSE_US = 0x24
SERVO_ACTIVE_US = 0x28
KEY_CAPTURE = 0x2C
CAPTURE_TICKS = 0x30
KEY_STATUS = 0x34
TRIGGER_COUNT = 0x38
TRIGGER_FRAME = 0x3C
ROI_X = 0x40
ROI_Y = 0x44
QUALITY_FRAME = 0x48
QUALITY_EDGES = 0x4C
QUALITY_CLIPPED = 0x50
QUALITY_PIXELS = 0x54
# Quality extension added after the stop-capture overlay.  The first two
# registers reuse the old stepper-only slots; the remaining registers are in
# the reserved upper half of the 7-bit AXI-Lite address space.
QUALITY_LUMA_SUM = 0x58
QUALITY_DARK = 0x5C
QUALITY_SATURATED = 0x70
QUALITY_NOISE = 0x74
QUALITY_SHAPE = 0x78
QUALITY_FRAME_PIXELS = 0x7C
MOTOR_STEPS = 0x58
MOTOR_FRAME_STEPS = 0x5C
KEY_ROTATING = 1 << 3
BUTTON_OVERLAY_VERSION = 0x00050000
STEPPER_OVERLAY_VERSION = 0x00050005
SERVO_CLOCK_MHZ = 100.0
SERVO_STOP_US = 1500
SERVO_CCW_MIN_US = 1551
SERVO_CCW_MAX_US = 2000

CONTROL_ENABLE = 1 << 0
CONTROL_THRESHOLD = 1 << 1
CONTROL_OVERLAY = 1 << 2
CONTROL_OVERLAY_CROSS = 1 << 3
CONTROL_INVERT = 1 << 4
CONTROL_GRAY = 1 << 5
CONTROL_MEAN_FILTER = 1 << 6
CONTROL_MAJORITY = 1 << 7
CONTROL_SHARPEN = 1 << 8
CONTROL_ROI_MASK = 1 << 9
CONTROL_DENOISE = 1 << 10
CONTROL_TONE = 1 << 11
CONTROL_CHROMA = 1 << 12
YCOCG_OVERLAY_VERSION = 0x00050008
STOP_CAPTURE_OVERLAY_VERSION = 0x00050009
QUALITY_METRICS_OVERLAY_VERSION = 0x0005000A
CAPTURE_FEATURE = 0x68
STOP_CAPTURE_FEATURE = 0x53544331
CLEAN_OVERLAY_VERSION = 0x00050007
SHARPEN_OVERLAY_VERSION = 0x00050002
ROI_OVERLAY_VERSION = 0x00050003
HD_OVERLAY_VERSION = 0x00050006
IMAGE_WIDTH_REG = 0x60
IMAGE_HEIGHT_REG = 0x64
STATUS_NEW_FRAME = 1 << 0


def quality_frame_is_complete(quality, width, height, require_shape=False):
    """Return True for legacy metrics or a verified shape extension frame."""
    if not quality or "frame_complete" not in quality:
        return not require_shape
    return (bool(quality["frame_complete"]) and
            not bool(quality.get("frame_bad", False)) and
            (int(quality.get("shape_flags", 0)) & 0x3F) == 0x37 and
            bool(quality.get("rows_match", quality.get("rows") == height)) and
            bool(quality.get("pixels_match",
                             quality.get("frame_pixels") == width * height)) and
            quality.get("rows") == height and
            quality.get("frame_pixels") == width * height)

IMAGE_MODES = {
    "raw": CONTROL_ENABLE,
    "sharp": CONTROL_ENABLE | CONTROL_SHARPEN,
    "denoise": CONTROL_ENABLE | CONTROL_DENOISE,
    "tone": CONTROL_ENABLE | CONTROL_TONE,
    "clean": CONTROL_ENABLE | CONTROL_SHARPEN | CONTROL_DENOISE | CONTROL_TONE,
    "ycocg": CONTROL_ENABLE | CONTROL_SHARPEN | CONTROL_DENOISE | CONTROL_TONE | CONTROL_CHROMA,
    "roi_mask": CONTROL_ENABLE | CONTROL_ROI_MASK,
    "overlay": CONTROL_ENABLE | CONTROL_OVERLAY,
    "threshold": CONTROL_ENABLE | CONTROL_THRESHOLD,
    "gray": CONTROL_ENABLE | CONTROL_GRAY,
}

# S2MM capture is polled because this overlay does not route VDMA IRQs to PS.
S2MM_CR = 0x30
S2MM_SR = 0x34
S2MM_ERRORS = 0x8FF0
# Circular S2MM with IOC retained for diagnostics. Completed frames are
# detected from clean DDR store advances, independent of the IOC status bit.
S2MM_START = 0x00011003
S2MM_STARTUP_TIMEOUT = 1.0
S2MM_STARTUP_ATTEMPTS = 3
S2MM_ERROR_NAMES = {
    0x10: 'internal error', 0x20: 'AXI slave error',
    0x40: 'AXI decode error', 0x80: 'early frame sync',
    0x100: 'early line end', 0x800: 'late frame sync',
    0x8000: 'late line end',
}
# Retain memory if a faulty device cannot be stopped or reset.
_UNHALTED_BUFFERS = []


def configure_clock_reference(bitfile):
    """Use the PS crystal specified by this overlay before PYNQ sets FCLKs.

    PYNQ 2.x assumes a 50 MHz crystal for Zynq-7000. This board uses
    33.333333 MHz; leaving that default scales both PWM and capture time
    by 1.5. PYNQ has no public reference-clock setter in this version.
    """
    hwh = Path(bitfile).with_suffix(".hwh")
    references = {
        float(parameter.attrib["VALUE"])
        for parameter in ET.parse(hwh).iter("PARAMETER")
        if parameter.attrib.get("NAME") == "PCW_CRYSTAL_PERIPHERAL_FREQMHZ"
    }
    if len(references) != 1:
        raise RuntimeError("overlay must declare one PS crystal frequency")
    reference_mhz = references.pop()
    if not 20.0 <= reference_mhz <= 60.0:
        raise RuntimeError("invalid PS crystal frequency in overlay HWH")
    clock = Clocks._instance
    if getattr(clock, "SLCR_BASE_ADDRESS", None) != 0xF8000000:
        raise RuntimeError("clock calibration requires a Zynq-7000 controller")
    clock._ref_clk_mhz = reference_mhz
    return reference_mhz


class FpgaCore:
    """MMIO and S2MM VDMA wrapper for the generated overlay."""

    def __init__(self, bitfile=None,
        ip_name="image_process_0", vdma_name="axi_vdma_0", resolution="800x480"):
        sizes = {"800x480": (800, 480), "1280x720": (1280, 720),
                 "1920x1080": (1920, 1080),
                 "2592x1944": (FULL_5MP_WIDTH, FULL_5MP_HEIGHT)}
        if resolution not in sizes:
            raise ValueError("unsupported capture resolution")
        self.width, self.height = sizes[resolution]
        self.frame_shape = (self.height, self.width, 3)
        if bitfile is None:
            vivado_dir = Path(__file__).resolve().parents[1] / "vivado"
            # Prefer overlays with bounded stop-and-capture support for the
            # modeling service. Archived stepper outputs must never override it.
            names = (("camera_5mp_quality",) if resolution == "2592x1944" else
                     ("camera_hd_stop", "camera_hd_ycocg", "camera_hd_clean", "camera_hd") if resolution == "1920x1080" else
                     ("camera_720p",) if resolution == "1280x720" else
                     ("camera_stop", "camera_color", "camera_roi", "camera_fixed", "pynq_overlay"))
            candidates = [vivado_dir / (name + ".bit") for name in names]
            bitfile = next((path for path in candidates
                            if path.is_file() and path.with_suffix(".hwh").is_file()), None)
            if bitfile is None:
                raise FileNotFoundError("matching V7 servo .bit/.hwh files not found")
        reference_mhz = configure_clock_reference(bitfile)
        self.overlay = Overlay(str(bitfile))
        # This board can retain an earlier FCLK setting across overlay reloads.
        # Set the clock before enabling capture or the calibrated servo PWM.
        if abs(Clocks.fclk0_mhz - SERVO_CLOCK_MHZ) > 0.5:
            previous_clock = Clocks.fclk0_mhz
            Clocks.fclk0_mhz = SERVO_CLOCK_MHZ
            print("FCLK0 adjusted from %.1f to %.1f MHz" %
                  (previous_clock, Clocks.fclk0_mhz), flush=True)
        self.mmio = self._find_mmio(ip_name)
        self.vdma = self._find_mmio(vdma_name)
        if abs(Clocks.fclk0_mhz - SERVO_CLOCK_MHZ) > 0.5:
            self.mmio.write(SERVO_CTRL, 0)
            raise RuntimeError("FCLK0 must be 100 MHz for calibrated servo PWM")
        print("Clock calibration: PS=%.6f MHz FCLK0=%.6f MHz" %
              (reference_mhz, Clocks.fclk0_mhz), flush=True)
        self.frame_buffer = None
        self._capture_started = False
        self._frame_valid = False
        self._last_frame_count = None
        self._last_frame = None
        self.last_quality = None
        self.version = self.mmio.read(VERSION)
        if resolution != "800x480" and (self.version not in (HD_OVERLAY_VERSION, CLEAN_OVERLAY_VERSION, YCOCG_OVERLAY_VERSION, STOP_CAPTURE_OVERLAY_VERSION, QUALITY_METRICS_OVERLAY_VERSION) or
                self.mmio.read(IMAGE_WIDTH_REG) != self.width or
                self.mmio.read(IMAGE_HEIGHT_REG) != self.height):
            self.mmio.write(SERVO_CTRL, 0)
            raise RuntimeError("HD capture requires matching camera bitstream/HWH and dimensions")
        if resolution == "2592x1944" and self.version != QUALITY_METRICS_OVERLAY_VERSION:
            self.mmio.write(KEY_CAPTURE, 0)
            self.mmio.write(SERVO_CTRL, 0)
            raise RuntimeError("5MP validation requires the 0x0005000A quality overlay")
        self.has_stop_capture = (self.version in (STOP_CAPTURE_OVERLAY_VERSION, QUALITY_METRICS_OVERLAY_VERSION) and
                                 self.mmio.read(CAPTURE_FEATURE) == STOP_CAPTURE_FEATURE)
        self.is_stepper = self.version == STEPPER_OVERLAY_VERSION
        self.has_button_capture = (self.version & 0xFFFF0000) == BUTTON_OVERLAY_VERSION
        if self.has_button_capture:
            self.mmio.write(KEY_CAPTURE, 0)
        self.mmio.write(SERVO_PULSE_US, SERVO_STOP_US)
        self.mmio.write(SERVO_CTRL, 0)

    def _find_mmio(self, ip_name):
        description = self.overlay.ip_dict.get(ip_name)
        if description is None:
            available = ", ".join(sorted(self.overlay.ip_dict))
            raise RuntimeError("overlay IP not found: %s; available: %s" %
                               (ip_name, available))
        return MMIO(description["phys_addr"], description["addr_range"])

    def configure(self, servo_pulse_us=1500, threshold=120,
                  control=IMAGE_MODES["overlay"], wait_for_frame=True):
        self._check_image_control(control)
        if servo_pulse_us != SERVO_STOP_US and not SERVO_CCW_MIN_US <= servo_pulse_us <= SERVO_CCW_MAX_US:
            raise ValueError("servo pulse must be 1500 (stop) or 1551..2000 us (counterclockwise)")
        if not 0 <= threshold <= 255:
            raise ValueError("threshold must be 0..255")
        if self.frame_buffer is None:
            self.allocate_frame()
        self.mmio.write(THRESHOLD, threshold)
        self.mmio.write(SERVO_PULSE_US, servo_pulse_us)
        self.mmio.write(SERVO_CTRL, 0 if servo_pulse_us == SERVO_STOP_US else 1)
        self.mmio.write(CONTROL, control)
        self._start_continuous_dma(wait_for_frame=wait_for_frame)
        self._capture_started = True

    def start_servo_rotation(self, pulse_us):
        if not SERVO_CCW_MIN_US <= pulse_us <= SERVO_CCW_MAX_US:
            raise ValueError("counterclockwise rotation pulse must be 1551..2000 us")
        self.mmio.write(SERVO_PULSE_US, pulse_us)
        self.mmio.write(SERVO_CTRL, 1)

    def arm_button_capture(self, pulse_us=1612, duration=30.0):
        if self.is_stepper:
            raise RuntimeError("stepper overlay requires arm_stepper_capture()")
        if not self.has_button_capture:
            raise RuntimeError("KEY0 capture requires the matching version 5 .bit/.hwh overlay")
        if not SERVO_CCW_MIN_US <= pulse_us <= SERVO_CCW_MAX_US:
            raise ValueError("counterclockwise rotation pulse must be 1551..2000 us")
        if not 0 < duration <= 60:
            raise ValueError("duration must be greater than 0 and at most 60 seconds")
        ticks = max(1, min(3000, int(duration / 0.020 + 0.5)))
        self.mmio.write(KEY_CAPTURE, 0)
        self.mmio.write(SERVO_CTRL, 0)
        self.mmio.write(SERVO_PULSE_US, pulse_us)
        self.mmio.write(CAPTURE_TICKS, ticks)
        previous = self.mmio.read(TRIGGER_COUNT)
        self.mmio.write(KEY_CAPTURE, 1)
        return previous, ticks * 0.020

    def arm_stop_capture(self, pulse_us=1612, move_duration=0.12):
        if not self.has_stop_capture:
            raise RuntimeError("stop capture requires camera_hd_stop version 0x00050009")
        if not 0.02 <= move_duration <= 1.0:
            raise ValueError("short move must be 0.02..1.0 seconds")
        return self.arm_button_capture(pulse_us, move_duration)

    def set_short_rotation_duration(self, duration):
        """Update the next bounded timer while KEY0 is locked and PWM neutral."""
        if (not self.has_stop_capture or self.mmio.read(KEY_CAPTURE) != 3 or
                self.mmio.read(KEY_STATUS) & 12 or
                self.mmio.read(SERVO_ACTIVE_US) != SERVO_STOP_US):
            raise RuntimeError('move duration can only change between locked, neutral stops')
        if not 0.02 <= duration <= 1.0:
            raise ValueError('short move must be 0.02..1.0 seconds')
        ticks = int(duration / .020 + .5)
        self.mmio.write(CAPTURE_TICKS, ticks)
        return ticks * .020

    def lock_stop_capture_round(self):
        if not self.has_stop_capture:
            raise RuntimeError("stop capture overlay not loaded")
        # Leave the first hardware timer running. Ignore physical KEY0
        # throughout the round, including all stops and transfers.
        self.mmio.write(KEY_CAPTURE, 3)

    def trigger_short_rotation(self):
        if not self.has_stop_capture or self.mmio.read(KEY_CAPTURE) != 3:
            raise RuntimeError("short rotation requires software-only button mode")
        if self.mmio.read(KEY_STATUS) & 12:
            raise RuntimeError("previous short rotation is pending or active")
        previous = self.mmio.read(TRIGGER_COUNT)
        self.mmio.write(KEY_CAPTURE, 7)
        return previous

    def rearm_stop_capture(self):
        if self.mmio.read(KEY_STATUS) & 12 or self.mmio.read(SERVO_ACTIVE_US) != 1500:
            raise RuntimeError("cannot rearm before neutral PWM is active")
        self.mmio.write(KEY_CAPTURE, 1)

    def arm_stepper_capture(self):
        if not self.is_stepper:
            raise RuntimeError("A4988 capture requires camera_stepper.bit/.hwh")
        self.mmio.write(KEY_CAPTURE, 0)
        self.mmio.write(SERVO_CTRL, 0)
        previous = self.mmio.read(TRIGGER_COUNT)
        self.mmio.write(KEY_CAPTURE, 1)
        return previous, 400 / 25.0

    def button_capture_state(self):
        return (self.mmio.read(TRIGGER_COUNT), self.mmio.read(KEY_STATUS),
                self.mmio.read(TRIGGER_FRAME))

    def stop_servo_rotation(self):
        if self.is_stepper:
            self.mmio.write(KEY_CAPTURE, 0)
            self.mmio.write(SERVO_CTRL, 0)
            return
        if self.has_button_capture and self.mmio.read(KEY_CAPTURE) & 1:
            # Switch out of button mode while the PWM is still sending stop.
            self.mmio.write(SERVO_PULSE_US, SERVO_STOP_US)
            self.mmio.write(SERVO_CTRL, 1)
            self.mmio.write(KEY_CAPTURE, 0)
        if self.mmio.read(SERVO_CTRL) & 1:
            self.mmio.write(SERVO_PULSE_US, SERVO_STOP_US)
            time.sleep(0.20)
        self.mmio.write(SERVO_CTRL, 0)

    def _check_image_control(self, control):
        version = self.mmio.read(VERSION)
        if control & CONTROL_CHROMA and version not in (YCOCG_OVERLAY_VERSION, STOP_CAPTURE_OVERLAY_VERSION, QUALITY_METRICS_OVERLAY_VERSION):
            raise RuntimeError("FPGA YCoCg ISP requires version 0x00050008 and matching .bit/.hwh")
        if control & (CONTROL_DENOISE | CONTROL_TONE) and version not in (CLEAN_OVERLAY_VERSION, YCOCG_OVERLAY_VERSION, STOP_CAPTURE_OVERLAY_VERSION, QUALITY_METRICS_OVERLAY_VERSION):
            raise RuntimeError("FPGA clean ISP requires version 0x00050007 or 0x00050008 and matching .bit/.hwh")
        if control & CONTROL_ROI_MASK and version < ROI_OVERLAY_VERSION:
            raise RuntimeError("FPGA ROI mask requires version 0x00050003 or newer")
        if control & CONTROL_SHARPEN and not (
                (version & 0xFFFF0000) == BUTTON_OVERLAY_VERSION and
                version >= SHARPEN_OVERLAY_VERSION):
            raise RuntimeError("FPGA sharpening requires version 0x00050002 or newer; "
                               "reload the matching .bit/.hwh overlay (loaded 0x%08X)" % version)

    def set_image_mode(self, mode, threshold=None):
        if mode not in IMAGE_MODES:
            raise ValueError("unknown image mode: %s" % mode)
        self._check_image_control(IMAGE_MODES[mode])
        if threshold is not None:
            if not 0 <= threshold <= 255:
                raise ValueError("threshold must be 0..255")
            self.mmio.write(THRESHOLD, threshold)
        self.mmio.write(CONTROL, IMAGE_MODES[mode])

    def set_roi(self, x0=220, y0=40, x1=510, y1=305):
        if self.version < ROI_OVERLAY_VERSION:
            raise RuntimeError("FPGA ROI requires version 0x00050003 or newer")
        if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
            raise ValueError("ROI must fit inside the %dx%d image" %
                             (self.width, self.height))
        self.mmio.write(ROI_X, x0 | (x1 << 16))
        self.mmio.write(ROI_Y, y0 | (y1 << 16))

    def capture_roi_mask(self, restore_mode="sharp"):
        """Read the FPGA's fixed ROI mask after the mode settles at SOF."""
        self.set_image_mode("roi_mask")
        try:
            for _ in range(3):
                mask = self.capture_frame(timeout=10.0 if self.width >= 2592 else 3.0 if self.width > FRAME_WIDTH else 1.0)
            return mask
        finally:
            self.set_image_mode(restore_mode)
            for _ in range(3):
                self.capture_frame(timeout=10.0 if self.width >= 2592 else 3.0 if self.width > FRAME_WIDTH else 1.0)

    def quality(self):
        result = {"frame": self.mmio.read(QUALITY_FRAME),
                "edges": self.mmio.read(QUALITY_EDGES),
                "clipped": self.mmio.read(QUALITY_CLIPPED),
                "pixels": self.mmio.read(QUALITY_PIXELS)}
        if self.version >= QUALITY_METRICS_OVERLAY_VERSION:
            luma_sum = self.mmio.read(QUALITY_LUMA_SUM)
            dark = self.mmio.read(QUALITY_DARK)
            saturated = self.mmio.read(QUALITY_SATURATED)
            noise = self.mmio.read(QUALITY_NOISE)
            shape = self.mmio.read(QUALITY_SHAPE)
            frame_pixels = self.mmio.read(QUALITY_FRAME_PIXELS)
            rows = (shape >> 16) & 0xFFFF
            flags = shape & 0xFFFF
            roi_pixels = max(1, result["pixels"])
            result.update({
                "luma_sum": luma_sum,
                "mean_luma": luma_sum / float(roi_pixels),
                "dark": dark,
                "dark_ratio": dark / float(roi_pixels),
                "saturated": saturated,
                "saturated_ratio": saturated / float(roi_pixels),
                "noise": noise,
                "noise_estimate": noise / float(max(1, roi_pixels - rows)),
                "rows": rows,
                "frame_pixels": frame_pixels,
                "shape_flags": flags,
                "frame_complete": bool(flags & 0x0001),
                "frame_bad": bool(flags & 0x0008),
                "rows_match": bool(flags & 0x0010),
                "pixels_match": bool(flags & 0x0020),
            })
        if self.is_stepper:
            result["motor_steps"] = self.mmio.read(MOTOR_FRAME_STEPS)
        return result

    def telemetry(self):
        return {
            "frame_count": self.mmio.read(FRAME_COUNT),
            "target_x": self.mmio.read(TARGET_X),
            "target_y": self.mmio.read(TARGET_Y),
            "target_area": self.mmio.read(TARGET_AREA),
            "servo_pulse_us": self.mmio.read(SERVO_ACTIVE_US),
            "control": self.mmio.read(CONTROL),
        }

    def stop(self):
        self.stop_servo_rotation()
        self.mmio.write(CONTROL, 0)
        self._capture_started = False
        self._frame_valid = False
        try:
            self._halt_dma()
        except BaseException:
            if self.frame_buffer is not None:
                _UNHALTED_BUFFERS.append(self.frame_buffer)
                self.frame_buffer = None
            raise
        if self.frame_buffer is not None:
            self.frame_buffer.freebuffer()
            self.frame_buffer = None

    def _wait_dma(self, predicate, message, timeout=1.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError('%s; S2MM status=0x%08X' %
                                   (message, self.vdma.read(S2MM_SR)))
            time.sleep(.001)

    def _reset_dma(self):
        self.vdma.write(S2MM_CR, 4)
        self._wait_dma(lambda: not self.vdma.read(S2MM_CR) & 4,
                       'VDMA reset timeout')

    def _start_continuous_dma(self, wait_for_frame=True):
        """Bound startup recovery, then leave the frame stores running."""
        self.startup_attempts = []
        for attempt in range(S2MM_STARTUP_ATTEMPTS):
            if attempt:
                # Reset before touching buffers that may still be in use.
                self._reset_dma()
            try:
                self._arm_continuous_dma(wait_for_frame=wait_for_frame)
                self.startup_attempts.append({'attempt': attempt + 1,
                    'status': hex(self.vdma.read(S2MM_SR)), 'ready': True})
                return
            except (TimeoutError, RuntimeError) as exc:
                status = self.vdma.read(S2MM_SR)
                self.startup_attempts.append({'attempt': attempt + 1,
                    'status': hex(status), 'ready': False, 'error': str(exc)})
                print('VDMA startup attempt=%d status=0x%08X frame_count=%d store=%d CR=0x%08X buffer=0x%08X' %
                      (attempt + 1, status, self.frame_count(), self._current_dma_store(),
                       self.vdma.read(S2MM_CR), self.frame_buffer.physical_address), flush=True)
                self.frame_buffer.invalidate()
                print('VDMA buffer changed bytes: %s' %
                      [int((frame != 0xA5).sum()) for frame in self.frame_buffer], flush=True)
                if self.width > FRAME_WIDTH:
                    for index, frame in enumerate(self.frame_buffer):
                        rows = (frame != 0xA5).sum(axis=(1, 2))
                        print('VDMA store %d row bytes first=%s last=%s' %
                              (index, rows[:4].tolist(), rows[-12:].tolist()), flush=True)
                # Bus/address errors are not synchronization residue.
                if status & 0x60 or attempt + 1 == S2MM_STARTUP_ATTEMPTS:
                    raise

    def _arm_continuous_dma(self, wait_for_frame=True):
        self._reset_dma()
        address = int(self.frame_buffer.physical_address)
        if address % 8 or address + self.frame_buffer.nbytes > 2**32:
            raise RuntimeError('VDMA buffer is not a valid aligned 32-bit address')
        for index in range(3):
            self.vdma.write(0xAC + 4 * index,
                            address + index * self.width * self.height * 3)
        self.vdma.write(0xA8, self.width * 3)
        self.vdma.write(0xA4, self.width * 3)
        self.vdma.write(S2MM_CR, S2MM_START)
        self.vdma.write(0xA0, self.height)
        self._last_frame_count = self.frame_count()
        if not wait_for_frame:
            return
        # Reset may join a frame in progress. Recover synchronization residue
        # at frame-scale intervals, then prove three clean DDR store advances.
        # The IRQ flag can remain clear while circular VDMA writes continue.
        time.sleep(0.10)
        self.vdma.write(S2MM_SR, S2MM_ERRORS | 0x7000)
        previous_store = self._current_dma_store()
        clean_advances = 0
        next_ack = time.monotonic()
        deadline = time.monotonic() + (10.0 if self.width >= 2592 else S2MM_STARTUP_TIMEOUT)
        while time.monotonic() < deadline:
            status = self.vdma.read(S2MM_SR)
            current_store = self._current_dma_store()
            if status & 0x60:
                self._check_dma_errors(status)
            if status & S2MM_ERRORS:
                clean_advances = 0
                if time.monotonic() >= next_ack:
                    self.vdma.write(S2MM_SR, S2MM_ERRORS | 0x7000)
                    next_ack = time.monotonic() + 0.05
            elif not status & 1 and current_store != previous_store:
                clean_advances += 1
                if clean_advances >= 3:
                    self._last_frame_count = self.frame_count()
                    return
            previous_store = current_store
            time.sleep(.001)
        status = self.vdma.read(S2MM_SR)
        self._check_dma_errors(status)
        raise TimeoutError('VDMA stores did not advance after startup; status=0x%08X' % status)

    def restart_capture_after_sensor_setup(self):
        """Rearm DDR capture after the sensor enables its configured DVP output."""
        self.stop_servo_rotation()
        self._frame_valid = False
        self._start_continuous_dma(wait_for_frame=True)
        self._capture_started = True

    def _current_dma_store(self):
        current_store = (self.vdma.read(0x28) >> 24) & 0x1F
        if current_store >= 3:
            raise RuntimeError('Invalid VDMA frame store: %d' % current_store)
        return current_store

    def _halt_dma(self):
        self.vdma.write(S2MM_CR, self.vdma.read(S2MM_CR) & ~1)
        try:
            self._wait_dma(lambda: self.vdma.read(S2MM_SR) & 1,
                           'VDMA halt timeout')
        except TimeoutError:
            self._reset_dma()

    def _check_dma_errors(self, status):
        if status & S2MM_ERRORS:
            names = ', '.join(name for bit, name in S2MM_ERROR_NAMES.items()
                              if status & bit)
            raise RuntimeError('VDMA S2MM status=0x%08X: %s' %
                               (status, names or 'stream/DMA error'))

    def status(self):
        return self.mmio.read(STATUS)

    def frame_count(self):
        return self.mmio.read(FRAME_COUNT)

    def wait_frame(self, previous, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.frame_count() != previous:
                return self.frame_count()
            time.sleep(0.001)
        raise TimeoutError("FPGA frame timeout")

    def allocate_frame(self, shape=None, dtype="u1"):
        if shape is not None and tuple(shape) != self.frame_shape or dtype != 'u1':
            raise ValueError('frame shape must match the overlay resolution')
        if self.frame_buffer is not None:
            raise RuntimeError('frame buffer is already allocated')
        self.frame_buffer = allocate(shape=(3,) + self.frame_shape, dtype=dtype)
        self.frame_buffer[:] = 0xA5
        self.frame_buffer.flush()
        return self.frame_buffer

    def capture_frame(self, timeout=1.0):
        """Copy the completed DDR store after a clean VDMA store advance."""
        if not self._capture_started:
            raise RuntimeError("call configure() before capture_frame()")
        self._frame_valid = False
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        diagnostic = {'store_advances': 0, 'copy_attempts': 0, 'rejections': {}}
        self.last_capture_diagnostic = diagnostic
        previous_store = self._current_dma_store()
        while time.monotonic() < deadline:
            status = self.vdma.read(S2MM_SR)
            self._check_dma_errors(status)
            current_store = self._current_dma_store()
            if not status & 1 and current_store != previous_store:
                diagnostic['store_advances'] += 1
                completed_store = (current_store + 2) % 3
                quality_before = self.quality() if self.version >= ROI_OVERLAY_VERSION else None
                completed_buffer = self.frame_buffer[completed_store]
                completed_buffer.invalidate()
                frame = np.array(completed_buffer, copy=True, subok=False)
                diagnostic['copy_attempts'] += 1
                self._check_dma_errors(self.vdma.read(S2MM_SR))
                quality_after = self.quality() if quality_before is not None else None
                rejected = []
                if self._current_dma_store() == completed_store:
                    rejected.append('writer_caught_copy')
                if quality_before != quality_after:
                    rejected.append('metrics_changed_during_copy')
                if quality_before is not None:
                    if (quality_before['frame'] == 0 or quality_before['pixels'] == 0 or
                            not quality_frame_is_complete(quality_before, self.width, self.height,
                                require_shape=self.version == QUALITY_METRICS_OVERLAY_VERSION)):
                        rejected.append('incomplete_quality')
                    if self.last_quality is not None and quality_before['frame'] == self.last_quality['frame']:
                        rejected.append('stale_quality')
                if rejected:
                    for reason in rejected:
                        counts = diagnostic['rejections']
                        counts[reason] = counts.get(reason, 0) + 1
                    diagnostic.update(elapsed_seconds=time.monotonic()-started,
                        s2mm_status=hex(self.vdma.read(S2MM_SR)),
                        last_quality_frame=quality_before.get('frame') if quality_before else None)
                    # The writer caught up during the copy; wait for another
                    # completed buffer rather than return a torn frame.
                    previous_store = self._current_dma_store()
                    continue
                self._last_frame_count = self.frame_count()
                self._last_frame = frame
                self.last_quality = quality_after
                self._frame_valid = True
                diagnostic.update(elapsed_seconds=time.monotonic()-started,
                    s2mm_status=hex(self.vdma.read(S2MM_SR)), accepted=True)
                return frame
            time.sleep(.001)
        diagnostic.update(elapsed_seconds=time.monotonic()-started,
            s2mm_status=hex(self.vdma.read(S2MM_SR)), accepted=False,
            frame_count=self.frame_count(), current_store=self._current_dma_store())
        raise TimeoutError('No completed VDMA store; S2MM=0x%08X; diagnostics=%s' %
                           (self.vdma.read(S2MM_SR), diagnostic))

    def read_frame(self):
        if not self._frame_valid:
            raise RuntimeError("capture a complete frame first")
        return bytes(self._last_frame)
