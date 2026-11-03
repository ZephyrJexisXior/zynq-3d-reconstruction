"""Stationary 5MP DDR -> PL replay and hardware correspondence readout.

USB transport/MJPEG decode runs on PS. ISP, frame metrics, FAST9/BRIEF256 and
mutual Hamming matching run on PL. No motor command is issued by this module.
"""
import time
import numpy as np
from pynq import allocate
from fpga_control import (CONTROL, IMAGE_MODES, KEY_CAPTURE, SERVO_ACTIVE_US,
                          quality_frame_is_complete)

RETAINED_INPUTS = []
MM2S_ERRORS = 0x8FF0

class DualView:
    def __init__(self, core):
        self.core = core
        self.reg = core._find_mmio('dual_view_0')
        if self.reg.read(0x0C) != 0x44565031:
            raise RuntimeError('dual PL register version mismatch')
        if (core.width, core.height) != (2592, 1944):
            raise RuntimeError('dual capture requires actual 2592x1944 overlay')
        self.input_buffer = None
        self.input_allocation_count = 0

    def wait(self, predicate, message, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError(message + '; dual_status=0x%X mm2s=0x%X s2mm=0x%X' %
                                   (self.reg.read(8), self.core.vdma.read(4), self.core.vdma.read(0x34)))
            time.sleep(.002)

    def stopped(self):
        _, status, _ = self.core.button_capture_state()
        if status & 12 or self.core.mmio.read(SERVO_ACTIVE_US) != 1500:
            raise RuntimeError('dual processing requires a stopped turntable')

    def command(self, value):
        self.reg.write(4, value)
        if self.reg.read(8) & 32:
            raise RuntimeError('PL rejected command 0x%X' % value)

    def halt_read(self):
        self.core.vdma.write(0, self.core.vdma.read(0) & ~1)
        try:
            self.wait(lambda: self.core.vdma.read(4) & 1, 'MM2S halt failed', 2)
        except TimeoutError:
            self.core.vdma.write(0, 4)
            self.wait(lambda: not self.core.vdma.read(0) & 4, 'MM2S reset failed', 2)

    def source(self, ddr):
        self.stopped()
        self.wait(lambda: not self.reg.read(8) & 3, 'feature engine busy')
        previous = self.reg.read(0) & 2
        self.reg.write(0, previous)  # disable, allow the last registered beat to drain
        self.wait(lambda: not self.reg.read(8) & 64, 'mux failed to drain', 2)
        self.reg.write(0, 2 if ddr else 0)
        if self.reg.read(8) & 32:
            raise RuntimeError('PL source switch rejected')

    def quiesce_input(self):
        if self.input_buffer is not None:
            self.halt_read()

    def release_input(self):
        if self.input_buffer is not None:
            self.halt_read()  # must prove quiescence before freeing physical memory
            self.core._halt_dma()
            self.input_buffer.freebuffer()
            self.input_buffer = None

    def replay(self, bgr, bank=None, mode='raw'):
        self.stopped()
        if bgr.shape != (1944, 2592, 3) or bgr.dtype != np.uint8:
            raise ValueError('replay accepts only actual 2592x1944 uint8 BGR')
        self.quiesce_input()
        self.source(True)
        self.core._halt_dma()
        self.core.last_quality = None
        anchor = self.core.frame_count()
        # This board's installed PYNQ/XRT freebuffer() does not reclaim default
        # allocations until process exit. Reallocating 15 MB every replay
        # exhausts its 128 MB CMA pool after only a few paired positions.
        # Keep exactly one input allocation for the entire session. Both DMA
        # channels are halted above before overwriting its immutable pixels.
        if self.input_buffer is None:
            self.input_buffer = allocate(shape=bgr.shape, dtype='u1')
            self.input_allocation_count += 1
        self.input_buffer[:] = bgr
        self.input_buffer.flush()
        address = int(self.input_buffer.physical_address)
        if address % 8 or address + self.input_buffer.nbytes > 2**32:
            raise RuntimeError('invalid MM2S buffer address')
        self.core.set_image_mode(mode)
        # No sensor can reach the ISP while DDR is selected. S2MM is armed first.
        self.core._arm_continuous_dma(wait_for_frame=False)
        vdma = self.core.vdma
        vdma.write(0, 4)
        self.wait(lambda: not vdma.read(0) & 4, 'MM2S reset failed', 2)
        for index in range(3):
            vdma.write(0x5C + 4*index, address)
        vdma.write(0x58, 2592*3)
        vdma.write(0x54, 2592*3)
        vdma.write(4, MM2S_ERRORS | 0x7000)
        self.reg.write(0, 3)
        if bank is not None:
            self.command(1 if bank == 0 else 2)
        vdma.write(0, 0x00011003)
        vdma.write(0x50, 1944)
        self.core._capture_started = True
        # Repeated source is immutable. Ignore stale metrics until two complete
        # output frames and a fresh DDR store prove the selected ISP mode.
        previous_store = self.core._current_dma_store()
        advances = 0
        def replay_ready():
            nonlocal previous_store, advances
            status = vdma.read(0x34)
            self.core._check_dma_errors(status)
            current = self.core._current_dma_store()
            if current != previous_store:
                advances += 1
                previous_store = current
            return (advances >= 3 and
                    ((self.core.frame_count()-anchor) & 0xFFFFFFFF) >= 3 and
                    quality_frame_is_complete(self.core.quality(), 2592, 1944, True))
        self.wait(replay_ready, 'replay did not produce complete 5MP frames/stores', 20)
        # DDR replay runs faster than the physical sensor. A 15 MB CPU copy
        # cannot reliably outrun a continuously cycling 100 MHz stream. Stop
        # the immutable source at a frame boundary, then quiesce the writer
        # before copying a proven completed ring store. Never accept a torn
        # copy merely because its dimensions are correct.
        self.halt_read()
        self.core._halt_dma()
        self.core._check_dma_errors(vdma.read(0x34))
        completed = (self.core._current_dma_store() + 2) % 3
        quality = dict(self.core.quality())
        if not quality_frame_is_complete(quality, 2592, 1944, True):
            raise RuntimeError('incomplete PL quality at replay snapshot')
        completed_buffer = self.core.frame_buffer[completed]
        completed_buffer.invalidate()
        frame = np.array(completed_buffer, copy=True, subok=False)
        if quality != self.core.quality():
            raise RuntimeError('PL metrics changed after both DMA channels halted')
        self.core.last_quality = quality
        self.core._last_frame = frame
        self.core._frame_valid = True
        self.core._capture_started = False
        print('PL_REPLAY_COMPLETE stores=%d completed=%d frame=%d' %
              (advances, completed, quality['frame']), flush=True)
        self.wait(lambda: not self.reg.read(8) & 1, 'feature capture timed out')
        if bank is not None and not self.reg.read(8) & (4 << bank):
            raise RuntimeError('PL feature bank rejected frame dimensions')
        if vdma.read(4) & MM2S_ERRORS:
            raise RuntimeError('MM2S stream error 0x%X' % vdma.read(4))
        self.quiesce_input()
        return frame, quality

    def match(self):
        self.stopped()
        self.command(4)
        generation = self.reg.read(0x1C)
        self.wait(lambda: self.reg.read(8) & 16, 'hardware matching timed out', 15)
        pairs = []
        for index in range(self.reg.read(0x30)):
            self.reg.write(0x20, index)
            # AXI transactions provide more than the synchronous RAM latency.
            self.reg.read(0x20)
            status = self.reg.read(0x2C)
            if status & 0x80000000:
                left, right = self.reg.read(0x24), self.reg.read(0x28)
                pairs.append({'front_xy': [left & 65535, left >> 16],
                              'upper_xy': [right & 65535, right >> 16],
                              'hamming': status & 511})
        if len(pairs) != self.reg.read(0x18) or generation != self.reg.read(0x1C):
            raise RuntimeError('inconsistent hardware match readout')
        return {'producer': 'PL_FAST9_BRIEF256_MUTUAL_HAMMING',
                'generation': generation, 'features_front': self.reg.read(0x10),
                'features_upper': self.reg.read(0x14), 'correspondence_count': len(pairs),
                'maximum_hamming': 64, 'ratio_percent': 75, 'mutual_nearest': True,
                'coordinates': 'full-resolution image pixels', 'pairs': pairs,
                'geometry_verified': False,
                'limitations': 'Fixed orientation/scale descriptor; precheck only. Multi-view geometry must still be verified.'}

    def restore_front(self, mode='ycocg'):
        self.command(8)  # discard any abandoned frame before changing source
        self.quiesce_input()
        self.source(False)
        self.core._halt_dma()
        self.core.last_quality = None
        self.core.set_image_mode(mode)
        self.reg.write(0, 1)
        self.core._start_continuous_dma(wait_for_frame=True)
        self.core._capture_started = True

    def close(self):
        self.core.stop_servo_rotation()
        try:
            self.command(8)
            self.release_input()
        except BaseException:
            if self.input_buffer is not None:
                RETAINED_INPUTS.append(self.input_buffer)
                self.input_buffer = None
            raise

