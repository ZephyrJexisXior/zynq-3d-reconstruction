"""Fragmented UDP sender for FPGA processed frames."""

import binascii
import json
import socket
import struct
import zlib

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
# Extended quality packet: sequence, quality frame, legacy edge/clipped/ROI
# counts, luma sum, dark count, saturated count, horizontal noise sum, actual
# rows, actual accepted pixels, shape flags, and optional motor steps.
QUALITY_EXT_EVENT = struct.Struct("<4sIIIIIIIIIIIII")
MASK_HEADER = struct.Struct("<4sHHI")
RECT_MASK_HEADER = struct.Struct("<4sHHHHHH")
MAX_DATAGRAM = 1400


def crc16(data):
    return binascii.crc_hqx(data, 0xFFFF)


class UdpFrameSender:
    def __init__(self, host="192.168.2.1", port=5012):
        self.address = (host, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        self.socket.sendto(START_MAGIC, self.address)

    def send(self, payload, width=800, height=480, sequence=0, telemetry=None,
             compress=False):
        if compress:
            if payload.shape != (height, width, 3):
                raise ValueError("JPEG frame dimensions do not match metadata")
            ok, encoded = cv2.imencode(".jpg", payload,
                                       [cv2.IMWRITE_JPEG_QUALITY, 97])
            if not ok:
                raise RuntimeError("Unable to encode full-resolution frame")
            payload = memoryview(encoded).cast("B")
            magic = JPEG_MAGIC
        else:
            payload = memoryview(payload).cast("B")
            magic = MAGIC
        telemetry = telemetry or {}
        chunk_size = MAX_DATAGRAM - HEADER.size - 2
        chunk_count = (len(payload) + chunk_size - 1) // chunk_size
        if chunk_count > 65535:
            raise ValueError("encoded frame exceeds UDP sequence capacity")
        for chunk_index in range(chunk_count):
            start = chunk_index * chunk_size
            chunk = payload[start:start + chunk_size]
            header = HEADER.pack(magic, width, height, sequence,
                                 len(payload), chunk_index, chunk_count,
                                 len(chunk),
                                 telemetry.get("frame_count", 0),
                                 telemetry.get("target_x", 0),
                                 telemetry.get("target_y", 0),
                                 telemetry.get("target_area", 0),
                                 telemetry.get("servo_pulse_us", 1500),
                                 telemetry.get("control", 0))
            self.socket.sendto(header + chunk.tobytes() +
                               struct.pack("<H", crc16(chunk)), self.address)

    def send_mask(self, mask, width=800, height=480):
        if mask.shape != (height, width, 3):
            raise ValueError("FPGA mask size does not match frame dimensions")
        if not (np.array_equal(mask[:, :, 0], mask[:, :, 1]) and
                np.array_equal(mask[:, :, 0], mask[:, :, 2])):
            raise ValueError("FPGA mask channels disagree")
        packed = zlib.compress(mask[:, :, 0].tobytes(), level=9)
        packet = MASK_HEADER.pack(MASK_MAGIC, width, height, len(packed)) + packed
        packet += struct.pack("<H", crc16(packed))
        if len(packet) <= MAX_DATAGRAM:
            for _ in range(3):
                self.socket.sendto(packet, self.address)
        else:
            active = mask[:, :, 0] == 255
            ys, xs = np.nonzero(active)
            if (len(xs) == 0 or not active[ys.min():ys.max()+1,
                                          xs.min():xs.max()+1].all() or
                    len(xs) != (ys.max()-ys.min()+1)*(xs.max()-xs.min()+1)):
                raise RuntimeError("FPGA ROI mask cannot be represented as a rectangle")
            packet = RECT_MASK_HEADER.pack(RECT_MASK_MAGIC, width, height,
                                           int(xs.min()), int(ys.min()),
                                           int(xs.max()+1), int(ys.max()+1))
            for _ in range(3):
                self.socket.sendto(packet, self.address)

    def capture_event(self, starting, trigger, duration, detail):
        packet = CAPTURE_EVENT.pack(b"FPGK" if starting else b"FPGD", trigger,
                                    int(round(duration * 1000)), detail)
        for _ in range(3):
            self.socket.sendto(packet, self.address)

    def capture_abort(self, trigger, duration, sent_frames):
        packet = CAPTURE_EVENT.pack(b"FPGX", trigger, int(round(duration*1000)), sent_frames)
        for _ in range(3):
            self.socket.sendto(packet, self.address)

    def send_capture_metadata(self, metadata):
        packet = b"FPGP" + json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        if len(packet) > MAX_DATAGRAM:
            raise ValueError("capture metadata exceeds one datagram")
        for _ in range(3):
            self.socket.sendto(packet, self.address)

    def send_quality(self, sequence, quality):
        if "luma_sum" in quality:
            packet = QUALITY_EXT_EVENT.pack(
                b"FPGQ", sequence, quality["frame"], quality["edges"],
                quality["clipped"], quality["pixels"], quality["luma_sum"],
                quality["dark"], quality["saturated"], quality["noise"],
                quality["rows"], quality["frame_pixels"], quality["shape_flags"],
                quality.get("motor_steps", 0))
        elif "motor_steps" in quality:
            packet = QUALITY_STEPPER_EVENT.pack(
                b"FPGQ", sequence, quality["frame"], quality["edges"],
                quality["clipped"], quality["pixels"], quality["motor_steps"])
        else:
            packet = QUALITY_EVENT.pack(b"FPGQ", sequence, quality["frame"],
                                        quality["edges"], quality["clipped"],
                                        quality["pixels"])
        for _ in range(3):
            self.socket.sendto(packet, self.address)

    def close(self):
        self.socket.close()


class UdpControlReceiver:
    def __init__(self, port=5011):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0", port))
        self.socket.setblocking(False)

    def poll(self):
        try:
            packet, _ = self.socket.recvfrom(64)
        except BlockingIOError:
            return None
        if len(packet) != 6 or packet[:4] != CONTROL_MAGIC:
            return None
        mode_id, threshold = struct.unpack("<BB", packet[4:])
        modes = {0: "raw", 1: "overlay", 2: "threshold", 3: "gray", 4: "sharp",
                 5: "denoise", 6: "tone", 7: "clean", 8: "ycocg"}
        mode = modes.get(mode_id)
        return (mode, threshold) if mode else None

    def close(self):
        self.socket.close()
