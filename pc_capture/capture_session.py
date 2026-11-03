"""Persist each button-triggered round as a reconstruction dataset."""
import json
import time
from pathlib import Path

import cv2
import numpy as np


class CaptureSession:
    def __init__(self, output, camera_id="front_ov5640"):
        self.output = Path(output)
        self.camera_id = camera_id
        self.path = None
        self.trigger = None
        self.last_trigger = None
        self.fpga_mask = None

    def set_fpga_mask(self, image):
        if image.ndim == 2:
            mask = image
        elif image.ndim == 3 and image.shape[2] == 3:
            if (not np.array_equal(image[:, :, 0], image[:, :, 1]) or
                    not np.array_equal(image[:, :, 0], image[:, :, 2])):
                raise ValueError("FPGA mask channels disagree")
            mask = image[:, :, 0]
        else:
            raise ValueError("FPGA mask must be one or three channels")
        if not np.isin(mask, (0, 255)).all() or not (mask.min() == 0 and mask.max() == 255):
            raise ValueError("FPGA mask is not a binary ROI")
        self.fpga_mask = mask.copy()

    def start(self, trigger, duration_ms, trigger_frame):
        if trigger == self.last_trigger:
            return  # start events are repeated for UDP reliability
        self.finish("interrupted")
        self.last_trigger = self.trigger = trigger
        name = "button_%s_%09d_%d" % (time.strftime("%Y-%m-%d_%H-%M-%S"),
                                       time.time_ns() % 1000000000, trigger)
        self.path = self.output / name
        (self.path / "images").mkdir(parents=True, exist_ok=False)
        if self.fpga_mask is not None:
            (self.path / "masks").mkdir()
        self.records = []
        self.capture_plan = None
        self.width = self.height = None
        self.duration_ms = duration_ms
        self.trigger_frame = trigger_frame
        self.started = time.monotonic()
        self.started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._manifest("capturing")
        print("PHOTO_SESSION", self.path, flush=True)

    def set_capture_plan(self, trigger, plan):
        if self.path is not None and self.trigger == trigger:
            self.capture_plan = plan
            self._manifest("capturing")

    def save(self, image, sequence, metadata, quality=None, sample=None):
        if self.path is None:
            return
        filename = "A%04d.BMP" % len(self.records)
        ok, encoded = cv2.imencode(".bmp", image)
        if not ok:
            raise RuntimeError("Unable to encode camera frame")
        (self.path / "images" / filename).write_bytes(encoded.tobytes())
        if self.fpga_mask is not None:
            if self.fpga_mask.shape != image.shape[:2]:
                raise RuntimeError("FPGA mask and image dimensions differ")
            ok, mask_png = cv2.imencode(".png", self.fpga_mask)
            if not ok:
                raise RuntimeError("Unable to encode FPGA mask")
            (self.path / "masks" / (filename + ".png")).write_bytes(mask_png.tobytes())
        self.height, self.width = image.shape[:2]
        motor_steps = quality.get("motor_steps") if quality else None
        position_index = (sample.get("position_index")
                          if isinstance(sample, dict) else None)
        shape_verified = bool(quality and "frame_complete" in quality)
        frame_complete = (bool(quality.get("frame_complete")) and
            quality.get("rows") == self.height and
            quality.get("frame_pixels") == self.width * self.height and
            (int(quality.get("shape_flags", 0)) & 0x3F) == 0x37) if shape_verified else False
        frame_bad = (quality.get("frame_bad", False)
                     if quality else False)
        self.records.append({
            "file": "images/" + filename, "frame": len(self.records),
            "source_sequence": sequence, "timestamp_us": time.time_ns() // 1000,
            "camera_id": self.camera_id,
            "position_index": position_index,
            "servo_pulse_us": None if motor_steps is not None else metadata[4],
            "motor_step_count": motor_steps,
            "angle_estimate_deg": ((motor_steps % 200) * 1.8)
                if motor_steps is not None else None,
            "angle_source": "fpga_full_step_nominal" if motor_steps is not None else None,
            "valid": bool(frame_complete and not frame_bad),
            "quality_verdict": ("pass" if frame_complete and not frame_bad else
                                "retry" if shape_verified else "unverified"),
            "quality_shape_verified": shape_verified,
            "fpga_frame_count": metadata[0],
            "target_x": metadata[1], "target_y": metadata[2],
            "target_area": metadata[3], "control": metadata[5],
            "fpga_sharpen_requested": bool(metadata[5] & (1 << 8)),
            "fpga_denoise_requested": bool(metadata[5] & (1 << 10)),
            "fpga_tone_requested": bool(metadata[5] & (1 << 11)),
            "fpga_chroma_requested": bool(metadata[5] & (1 << 12)),
            "mask": "masks/" + filename + ".png" if self.fpga_mask is not None else None,
            "mask_source": "fpga_fixed_roi" if self.fpga_mask is not None else None,
            "fpga_quality": quality,
            "stop_capture": sample,
        })
        self._manifest("capturing")

    def _manifest(self, state, sent_frames=None):
        payload = {
            "schema": "fpga-reconstruction/v2", "created_utc": self.started_utc,
            "camera_id": self.camera_id,
            "image_size": {"width": self.width, "height": self.height},
            "frames": self.records,
            "capture": {"trigger": self.trigger, "trigger_source": "pl_key[0]",
                        "trigger_frame": self.trigger_frame,
                        "requested_duration_s": self.duration_ms / 1000,
                        "receiver_elapsed_s": time.monotonic() - self.started,
                        "complete_frames": len(self.records),
                        "sent_frames": sent_frames, "state": state,
                        "plan": self.capture_plan},
        }
        temporary = self.path / "manifest.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path / "manifest.json")

    def finish(self, state="complete", trigger=None, sent_frames=None):
        if self.path is None or (trigger is not None and trigger != self.trigger):
            return
        self._manifest(state, sent_frames)
        print("PHOTOS_SAVED", len(self.records), self.path, state, flush=True)
        self.path = None
        self.trigger = None
