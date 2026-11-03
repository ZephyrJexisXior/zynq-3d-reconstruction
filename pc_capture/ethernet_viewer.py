"""Receive FPGA frames, display telemetry, and send image-mode changes."""

import argparse
import binascii
import json
import socket
import struct
import zlib
from collections import OrderedDict
from pathlib import Path

from capture_session import CaptureSession

import cv2
import numpy as np


HEADER = struct.Struct("<4sHHIIHHHIIIIII")
MAGIC = b"FPG3"
JPEG_MAGIC = b"FPG4"
MASK_MAGIC = b"FPGM"
RECT_MASK_MAGIC = b"FPGR"
START_MAGIC = b"FPGS"
CONTROL_MAGIC = b"FPGC"
CAPTURE_EVENT = struct.Struct("<4sIII")
QUALITY_EVENT = struct.Struct("<4sIIIII")
QUALITY_STEPPER_EVENT = struct.Struct("<4sIIIIII")
QUALITY_EXT_EVENT = struct.Struct("<4sIIIIIIIIIIIII")
MASK_HEADER = struct.Struct("<4sHHI")
RECT_MASK_HEADER = struct.Struct("<4sHHHHHH")
CONTROL_PORT = 5011
MAX_IMAGE_WIDTH = 2592
MAX_IMAGE_HEIGHT = 1944
MODE_IDS = {"raw": 0, "overlay": 1, "threshold": 2, "gray": 3, "sharp": 4,
            "denoise": 5, "tone": 6, "clean": 7, "ycocg": 8}
KEY_MODES = {ord(str(value)): name for name, value in MODE_IDS.items()}


def image_mode_from_control(control):
    if control & (1 << 1):
        return "threshold"
    if control & (1 << 5):
        return "gray"
    if control & (1 << 2):
        return "overlay"
    if control & (1 << 12) and control & (1 << 10):
        return "ycocg"
    if control & (1 << 10) and control & (1 << 11):
        return "clean"
    if control & (1 << 11):
        return "tone"
    if control & (1 << 10):
        return "denoise"
    return "sharp" if control & (1 << 8) else "raw"


def crc16(data):
    return binascii.crc_hqx(data, 0xFFFF)


def decode_fpga_mask(packet):
    if len(packet) == RECT_MASK_HEADER.size and packet[:4] == RECT_MASK_MAGIC:
        _, width, height, x0, y0, x1, y1 = RECT_MASK_HEADER.unpack(packet)
        if 0 < width <= MAX_IMAGE_WIDTH and 0 < height <= MAX_IMAGE_HEIGHT and \
                0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height:
            mask = np.zeros((height, width), dtype=np.uint8)
            mask[y0:y1, x0:x1] = 255
            return mask
        return None
    if len(packet) < MASK_HEADER.size + 2 or packet[:4] != MASK_MAGIC:
        return None
    _, width, height, packed_size = MASK_HEADER.unpack_from(packet)
    packed = packet[MASK_HEADER.size:-2]
    if not (0 < width <= MAX_IMAGE_WIDTH and 0 < height <= MAX_IMAGE_HEIGHT and
            packed_size == len(packed) and
            crc16(packed) == struct.unpack_from("<H", packet, len(packet)-2)[0]):
        return None
    try:
        decoded = zlib.decompress(packed, bufsize=width*height)
    except zlib.error:
        return None
    if len(decoded) != width*height:
        return None
    return np.frombuffer(decoded, dtype=np.uint8).reshape((height, width))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5012)
    parser.add_argument("--board-host", default="192.168.2.99")
    parser.add_argument("--wait-for-board", action="store_true",
                        help="Keep window hidden until the board sends data")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "captures_pc",
                        help="Save each KEY0 capture round under this directory")
    args = parser.parse_args()
    recorder = CaptureSession(args.output)

    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
    receiver.settimeout(0.2)
    receiver.bind((args.bind, args.port))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    frames = OrderedDict()
    quality_by_sequence = {}
    sample_by_sequence = {}
    current_mode = "sharp"
    threshold = 120
    waiting = np.zeros((480, 1100, 3), dtype=np.uint8)
    cv2.putText(waiting, "Waiting for KEY0: synchronized servo and photo capture...",
                (36, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (210, 230, 235), 2, cv2.LINE_AA)
    cv2.putText(waiting, "Board %s | UDP %d | Q to close" %
                (args.board_host, args.port), (36, 260),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (150, 170, 175), 1, cv2.LINE_AA)
    if not args.wait_for_board:
        cv2.imshow("FPGA Dual18 Experiment", waiting)
        cv2.waitKey(1)

    def draw_dashboard(image, metadata, quality, source_size):
        panel_width = 300
        panel = np.zeros((image.shape[0], panel_width, 3), dtype=np.uint8)
        panel[:] = (34, 39, 42)
        fields = [
            ("FPGA LIVE", ""),
            ("Frame", str(metadata[0])),
            ("Resolution", "%dx%d" % source_size),
            ("Target center", "%d, %d" % (metadata[1], metadata[2])),
            ("Target pixels", str(metadata[3])),
            ("ROI edges", str(quality["edges"]) if quality else "--"),
            ("ROI clipped", str(quality["clipped"]) if quality else "--"),
            ("Mean luma", "%.1f" % quality["mean_luma"]
             if quality and "mean_luma" in quality else "--"),
            ("Dark / saturated", "%.1f%% / %.1f%%" %
             (100.0 * quality["dark_ratio"],
              100.0 * quality["saturated_ratio"])
             if quality and "dark_ratio" in quality else "--"),
            ("Noise estimate", "%.1f" % quality["noise_estimate"]
             if quality and "noise_estimate" in quality else "--"),
            ("Frame shape", ("OK" if quality["frame_complete"] else "BAD")
             if quality and "frame_complete" in quality else "--"),
            ("Pixels / rows", "%d / %d" %
             (quality["frame_pixels"], quality["rows"])
             if quality and "frame_pixels" in quality else "--"),
            ("FPGA mask", "ready" if recorder.fpga_mask is not None else "--"),
            (("Motor steps", str(quality["motor_steps"]))
             if quality and "motor_steps" in quality else
             ("Servo pulse", "%d us" % metadata[4])),
            ("Image mode", current_mode),
            ("Threshold", str(threshold)),
            ("", ""),
            ("0  Original / 1  Bounding box", ""),
            ("2 Binary / 3 Gray / 4 Sharp", ""),
            ("5 Denoise / 6 Tone / 7 Clean", ""),
            ("+/-  Threshold", ""),
            ("Q  Close", ""),
        ]
        line_height = min(27, max(16, (image.shape[0] - 48) // (len(fields) - 1)))
        y = 28
        for label, value in fields:
            color = (100, 220, 180) if label == "FPGA LIVE" else (230, 235, 238)
            text = label if not value else "%s: %s" % (label, value)
            cv2.putText(panel, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.52, color, 1, cv2.LINE_AA)
            y += line_height
        return np.hstack((image, panel))

    def request_mode(mode):
        sender.sendto(CONTROL_MAGIC + struct.pack("<BB", MODE_IDS[mode], threshold),
                      (args.board_host, CONTROL_PORT))

    try:
        while True:
            try:
                packet, source = receiver.recvfrom(65535)
            except socket.timeout:
                if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                    break
                continue
            if source[0] != args.board_host:
                continue
            if len(packet) == CAPTURE_EVENT.size and packet[:4] in (b"FPGK", b"FPGD", b"FPGX"):
                event, trigger, duration_ms, detail = CAPTURE_EVENT.unpack(packet)
                if event == b"FPGK":
                    if trigger == recorder.last_trigger:
                        continue
                    frames.clear()
                    quality_by_sequence.clear()
                    sample_by_sequence.clear()
                    recorder.start(trigger, duration_ms, detail)
                    cv2.imshow("FPGA Dual18 Experiment", waiting)
                    cv2.waitKey(1)
                else:
                    recorder.finish("aborted" if event == b"FPGX" else "complete",
                                    trigger=trigger, sent_frames=detail)
                continue
            if packet[:4] == b"FPGP":
                try:
                    meta = json.loads(packet[4:].decode("utf-8"))
                    if isinstance(meta, dict) and meta.get("trigger") == recorder.trigger:
                        if meta.get("kind") == "plan" and isinstance(meta.get("plan"), dict):
                            recorder.set_capture_plan(meta["trigger"], meta["plan"])
                        elif meta.get("kind") == "sample" and isinstance(meta.get("sequence"), int):
                            sample_by_sequence[meta["sequence"]] = meta
                            while len(sample_by_sequence) > 128:
                                sample_by_sequence.pop(next(iter(sample_by_sequence)))
                except (ValueError, UnicodeDecodeError):
                    pass
                continue
            if packet == START_MAGIC:
                recorder.finish("board_restart")
                recorder.last_trigger = None
                recorder.fpga_mask = None
                frames.clear()
                quality_by_sequence.clear()
                sample_by_sequence.clear()
                cv2.imshow("FPGA Dual18 Experiment", waiting)
                if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                    break
                continue
            if packet[:4] in (MASK_MAGIC, RECT_MASK_MAGIC):
                mask_image = decode_fpga_mask(packet)
                if mask_image is not None:
                    try:
                        recorder.set_fpga_mask(mask_image)
                        print("FPGA_ROI_MASK_READY", mask_image.shape[1],
                              mask_image.shape[0], flush=True)
                    except ValueError:
                        pass
                continue
            # The extended quality event is 56 bytes, larger than the image
            # header. Dispatch by magic as well as length before image parsing.
            if packet[:4] == b"FPGQ" or len(packet) < HEADER.size + 2:
                if len(packet) == QUALITY_EXT_EVENT.size and packet[:4] == b"FPGQ":
                    (_, sequence, qframe, edges, clipped, pixels, luma_sum,
                     dark, saturated, noise, rows, frame_pixels, shape_flags,
                     motor_steps) = QUALITY_EXT_EVENT.unpack(packet)
                    roi_pixels = max(1, pixels)
                    quality_by_sequence[sequence] = {
                        "frame": qframe, "edges": edges, "clipped": clipped,
                        "pixels": pixels, "luma_sum": luma_sum,
                        "mean_luma": luma_sum / float(roi_pixels),
                        "dark": dark, "dark_ratio": dark / float(roi_pixels),
                        "saturated": saturated,
                        "saturated_ratio": saturated / float(roi_pixels),
                        "noise": noise,
                        "noise_estimate": noise / float(max(1, roi_pixels - rows)),
                        "rows": rows, "frame_pixels": frame_pixels,
                        "shape_flags": shape_flags,
                        "frame_complete": bool(shape_flags & 1),
                        "frame_bad": bool(shape_flags & 8),
                        "rows_match": bool(shape_flags & 16),
                        "pixels_match": bool(shape_flags & 32),
                    }
                    if motor_steps:
                        quality_by_sequence[sequence]["motor_steps"] = motor_steps
                elif len(packet) == QUALITY_STEPPER_EVENT.size and packet[:4] == b"FPGQ":
                    _, sequence, qframe, edges, clipped, pixels, steps = QUALITY_STEPPER_EVENT.unpack(packet)
                    quality_by_sequence[sequence] = {"frame": qframe, "edges": edges,
                                                     "clipped": clipped, "pixels": pixels,
                                                     "motor_steps": steps}
                elif len(packet) == QUALITY_EVENT.size and packet[:4] == b"FPGQ":
                    _, sequence, qframe, edges, clipped, pixels = QUALITY_EVENT.unpack(packet)
                    quality_by_sequence[sequence] = {"frame": qframe, "edges": edges,
                                                     "clipped": clipped, "pixels": pixels}
                if len(quality_by_sequence) > 32:
                    quality_by_sequence.pop(next(iter(quality_by_sequence)))
                continue
            (magic, width, height, sequence, total_length, chunk_index,
             chunk_count, chunk_length, frame_count, target_x, target_y,
             target_area, servo_pulse_us, control) = HEADER.unpack_from(packet)
            if chunk_length > len(packet) - HEADER.size - 2:
                continue
            payload = packet[HEADER.size:HEADER.size + chunk_length]
            checksum = struct.unpack_from("<H", packet, HEADER.size + chunk_length)[0]
            if (magic not in (MAGIC, JPEG_MAGIC) or width == 0 or height == 0 or
                    width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT or
                    (magic == MAGIC and total_length != width * height * 3) or
                    (magic == JPEG_MAGIC and total_length > width * height * 3) or
                    chunk_count == 0 or
                    chunk_index >= chunk_count or
                    len(payload) != chunk_length or crc16(payload) != checksum):
                continue
            metadata = (frame_count, target_x, target_y, target_area,
                        servo_pulse_us, control)
            frame_key = (magic, sequence)
            frame = frames.setdefault(frame_key, [total_length, chunk_count,
                                                  metadata, {}])
            if frame[0] != total_length or frame[1] != chunk_count or frame[2] != metadata:
                del frames[frame_key]
                continue
            frame[3][chunk_index] = bytes(payload)
            frames.move_to_end(frame_key)
            while len(frames) > 4:
                frames.popitem(last=False)
            if len(frame[3]) != frame[1]:
                continue
            image_bytes = b"".join(frame[3][i] for i in range(frame[1]))
            del frames[frame_key]
            if len(image_bytes) != frame[0]:
                continue
            current_mode = image_mode_from_control(metadata[5])
            # The FPGA stream is already packed as B, G, R bytes.  OpenCV's
            # display functions also expect BGR, so do not swap the red and blue
            # channels here.  Swapping them makes orange objects appear blue.
            if magic == JPEG_MAGIC:
                image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8),
                                     cv2.IMREAD_COLOR)
                if image is None or image.shape != (height, width, 3):
                    continue
            else:
                image = np.frombuffer(image_bytes, dtype=np.uint8).reshape((height, width, 3))
            quality = quality_by_sequence.pop(sequence, None)
            sample = sample_by_sequence.pop(sequence, None)
            recorder.save(image, sequence, metadata, quality, sample)
            scale = min(1.0, 960.0 / image.shape[1], 720.0 / image.shape[0])
            preview = (cv2.resize(image, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_AREA)
                       if scale < 1.0 else image)
            dashboard = draw_dashboard(preview, metadata, quality, (width, height))
            cv2.imshow("FPGA Dual18 Experiment", dashboard)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key in KEY_MODES:
                request_mode(KEY_MODES[key])
            elif key in (ord("+"), ord("=")):
                threshold = min(255, threshold + 5)
                request_mode(current_mode)
            elif key in (ord("-"), ord("_")):
                threshold = max(1, threshold - 5)
                request_mode(current_mode)
    finally:
        recorder.finish("interrupted")
        receiver.close()
        sender.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
