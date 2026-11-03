"""Camera startup helpers bundled inside the disposable dual50 experiment."""
import time
import numpy as np
from camera_init import Sccb, initialize_ov5640
from fpga_control import FpgaCore, IMAGE_MODES

CAMERA_READY_TIMEOUT_S = 5.0
CAMERA_START_ATTEMPTS = 3


def _stop_on_signal(signum, frame):
    raise SystemExit(0)


def _usable_frame(frame):
    return (int(frame.max()) - int(frame.min()) > 8 and
            np.count_nonzero(frame) >= frame.size // 10)


def _start_camera(image_mode, resolution="800x480"):
    for attempt in range(1, CAMERA_START_ATTEMPTS + 1):
        core = FpgaCore(resolution=resolution)
        try:
            if core.is_stepper:
                raise RuntimeError('servo capture requires the V7 servo overlay, not camera_stepper')
            core.allocate_frame()
            core.configure(servo_pulse_us=1500, control=IMAGE_MODES[image_mode],
                           wait_for_frame=False)
            sensor_id = initialize_ov5640(resolution)
            print("OV5640 initialized, sensor ID=0x%04X" % sensor_id, flush=True)
            # DVP is held off until the sensor geometry is fully configured.
            # Rearm DDR capture before accepting clean completed frame stores.
            core.restart_capture_after_sensor_setup()
            deadline = time.monotonic() + (20.0 if core.width >= 2592 else CAMERA_READY_TIMEOUT_S)
            good_frames = 0
            while good_frames < 2:
                frame = core.capture_frame(timeout=max(0.1, deadline - time.monotonic()))
                good_frames = good_frames + 1 if _usable_frame(frame) else 0
                if time.monotonic() >= deadline and good_frames < 2:
                    raise TimeoutError('camera produced no usable image; VDMA=0x%08X' %
                                       core.vdma.read(0x34))
            print("Camera ready on attempt %d, frame count %d" %
                  (attempt, core.frame_count()), flush=True)
            return core
        except (RuntimeError, TimeoutError) as exc:
            core.stop()
            print("Camera startup attempt %d/%d failed: %s" %
                  (attempt, CAMERA_START_ATTEMPTS, exc), flush=True)
            if attempt == CAMERA_START_ATTEMPTS:
                raise
            try:
                with Sccb() as sensor:
                    sensor.write_register(0x4202, 0x0F)
                    time.sleep(0.1)
                    try:
                        sensor.write_register(0x3008, 0x42)
                    except RuntimeError:
                        # A reset can take effect before the SCCB ACK completes.
                        pass
            except RuntimeError as reset_error:
                print("Camera output reset failed: %s" % reset_error, flush=True)
            time.sleep(0.5)
