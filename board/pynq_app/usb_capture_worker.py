"""Capture one real 2592x1944 USB still in a bounded child process on PS."""
import argparse
import hashlib
import json
import select
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np


def closest_observed_frame(frames, target_ns):
    """Choose by host read completion, never claim a sensor exposure timestamp."""
    return min(frames, key=lambda frame: abs(frame[1]-target_ns))


def capture_concurrent(device, output, warmup=5):
    output = Path(output)
    if output.exists() or output.with_suffix('.json').exists():
        raise ValueError('refusing to replace an existing photograph')
    output.parent.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    report = dict(schema='qian-usb-concurrent-still/v1', device=device,
                  requested_size=[2592, 1944], full_5mp=False,
                  hardware_synchronized=False,
                  timestamp_source='PS_after_cap_read_not_sensor_exposure',
                  processing='PS USB MJPEG decode; no PL in this worker')
    started = time.monotonic()
    frames = deque(maxlen=2)
    try:
        if not cap.isOpened():
            raise RuntimeError('USB camera unavailable: '+device)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2592)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1944)
        report['requested_single_buffer_accepted'] = bool(cap.set(cv2.CAP_PROP_BUFFERSIZE, 1))
        size = [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
        report['negotiated_size'] = size
        if size != [2592, 1944]:
            raise RuntimeError('USB did not negotiate real 5MP')

        def read_frame():
            read_start = time.monotonic_ns()
            ok, image = cap.read()
            observed = time.monotonic_ns()
            if not ok or image is None or image.shape != (1944, 2592, 3) or image.dtype != np.uint8:
                raise RuntimeError('USB returned an incomplete or non-5MP frame')
            frames.append((read_start, observed, image))

        for _ in range(warmup):
            read_frame()
        ready = dict(streaming=True, actual_size=size, ready_monotonic_ns=time.monotonic_ns())
        ready_path = output.parent/'usb_stream_ready.json'
        ready_path.with_suffix('.tmp').write_text(json.dumps(ready))
        ready_path.with_suffix('.tmp').replace(ready_path)
        print('USB_STREAM_READY '+json.dumps(ready), flush=True)
        deadline = time.monotonic()+30
        while True:
            if select.select([sys.stdin], [], [], 0)[0]:
                command = sys.stdin.readline().strip().split()
                if len(command) != 2 or command[0] != 'SELECT':
                    raise RuntimeError('USB pair selection cancelled or parent disconnected')
                target_ns = int(command[1])
                if not ready['ready_monotonic_ns'] <= target_ns <= time.monotonic_ns():
                    raise RuntimeError('invalid front timestamp for this USB stream')
                # Bracket the requested front observation where possible.
                if frames[-1][1] < target_ns:
                    read_frame()
                read_start, observed_ns, image = closest_observed_frame(frames, target_ns)
                frames.clear()
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('front read did not select a USB frame within 30 seconds')
            read_frame()
        cap.release()
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise RuntimeError('USB PNG encoding failed')
        data = encoded.tobytes()
        back = cv2.imdecode(np.frombuffer(data, np.uint8), 1)
        if not np.array_equal(back, image):
            raise RuntimeError('USB lossless PNG roundtrip failed')
        output.with_suffix('.png.tmp').write_bytes(data)
        output.with_suffix('.png.tmp').replace(output)
        report.update(full_5mp=True, actual_size=size, front_target_monotonic_ns=target_ns,
                      frame_read_started_monotonic_ns=read_start,
                      frame_observed_monotonic_ns=observed_ns,
                      sha256=hashlib.sha256(data).hexdigest(),
                      selection_policy='nearest_of_two_recent_host_read_completions')
    except BaseException as exc:
        report['error'] = str(exc)
        raise
    finally:
        cap.release()
        report['elapsed_s'] = time.monotonic()-started
        output.with_suffix('.json').write_text(json.dumps(report, indent=2))
        print('USB_CONCURRENT_STILL '+json.dumps(report, sort_keys=True), flush=True)


def capture(device, output, warmup=5):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.with_suffix('.json').exists():
        raise ValueError('refusing to replace an existing photograph')
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    started = time.monotonic()
    report = {'schema': 'qian-usb-still/v1', 'device': device,
              'requested_size': [2592, 1944], 'full_5mp': False,
              'capture_started_monotonic_ns': time.monotonic_ns(),
              'timestamp_source': 'PS_after_cap_read_not_sensor_exposure',
              'processing': 'PS USB MJPEG decode; no PL in this worker'}
    try:
        if not cap.isOpened():
            raise RuntimeError('USB camera unavailable: '+device)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2592)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1944)
        report['negotiated_size'] = [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                     int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
        if report['negotiated_size'] != [2592, 1944]:
            raise RuntimeError('USB did not negotiate real 5MP')
        for index in range(warmup):
            ok, image = cap.read()
            observed_ns = time.monotonic_ns()
            if not ok or image is None:
                raise RuntimeError('USB frame read failed')
            if image.shape != (1944, 2592, 3) or image.dtype != np.uint8:
                raise RuntimeError('USB returned an incomplete or non-5MP frame')
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise RuntimeError('USB PNG encoding failed')
        data = encoded.tobytes()
        back = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if not np.array_equal(back, image):
            raise RuntimeError('USB lossless PNG roundtrip failed')
        temporary = output.with_suffix('.png.tmp')
        temporary.write_bytes(data)
        temporary.replace(output)
        report.update(full_5mp=True, actual_size=[image.shape[1], image.shape[0]],
                      frame_observed_monotonic_ns=observed_ns,
                      frames_read=warmup, sha256=hashlib.sha256(data).hexdigest(),
                      mean_bgr=[float(x) for x in image.mean(axis=(0, 1))])
    except BaseException as exc:
        report['error'] = str(exc)
        raise
    finally:
        cap.release()
        report['elapsed_s'] = time.monotonic()-started
        output.with_suffix('.json').write_text(json.dumps(report, indent=2))
        print('USB_STILL '+json.dumps(report, sort_keys=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='/dev/video0')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--concurrent', action='store_true')
    args = parser.parse_args()
    (capture_concurrent if args.concurrent else capture)(args.device, args.output)
