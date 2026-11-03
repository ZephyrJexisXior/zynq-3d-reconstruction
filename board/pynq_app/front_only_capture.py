"""OV5640-only storage/quality adapter. No USB camera or dual-view imports."""
import json
from pathlib import Path
import shutil
import socket
import time

import numpy as np

from fpga_control import quality_frame_is_complete
from fast_lossless_png import STORAGE_PROFILE, save_verified_png

WIDTH, HEIGHT = 2592, 1944


class FrontQualityRejected(RuntimeError):
    pass


def quality_reasons(q):
    if not quality_frame_is_complete(q, WIDTH, HEIGHT, True):
        return ['incomplete_5mp']
    reasons = []
    if q.get('pixels') != WIDTH * HEIGHT:
        reasons.append('quality_roi_is_not_full_frame')
    if not 35 <= q['mean_luma'] <= 220:
        reasons.append('exposure')
    if q['dark_ratio'] > .35:
        reasons.append('too_dark')
    if q['saturated_ratio'] > .15:
        reasons.append('overexposed')
    if q['edges'] / max(1, q['pixels']) < .001:
        reasons.append('too_few_edges')
    if q['noise_estimate'] > 15:
        reasons.append('excessive_horizontal_variation')
    return reasons


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temporary.replace(path)


def save_png(path, image):
    if image.shape != (HEIGHT, WIDTH, 3) or image.dtype != np.uint8:
        raise ValueError('OV5640 output must be full 2592x1944 uint8 color')
    return save_verified_png(path, image)


class OptionalNetwork:
    """Reuse the existing protocol while allowing local capture without a PC."""
    def __init__(self, host, port):
        from ethernet_stream import UdpFrameSender, START_MAGIC
        # The existing constructor transmits immediately; initialize its two
        # transport fields here so a missing route cannot abort camera startup.
        self.sender = UdpFrameSender.__new__(UdpFrameSender)
        self.sender.address = (host, port)
        self.sender.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        self.sender.socket.settimeout(.25)
        self.errors = 0
        self.last_warning = -1e9
        self._call(self.sender.socket.sendto, START_MAGIC, self.sender.address)

    def _call(self, function, *args, **kwargs):
        try:
            function(*args, **kwargs)
        except OSError as exc:
            self.errors += 1
            if time.monotonic() - self.last_warning >= 30:
                print('NETWORK_OPTIONAL local_save_continues: %s' % exc, flush=True)
                self.last_warning = time.monotonic()

    def __getattr__(self, name):
        target = getattr(self.sender, name)
        return lambda *args, **kwargs: self._call(target, *args, **kwargs)

    def close(self):
        self.sender.close()


class FrontOnlySender:
    def __init__(self, transport, output, plan):
        self.transport, self.output, self.plan = transport, Path(output), dict(plan)
        self.output.mkdir(parents=True, exist_ok=True)
        self.manifest = self.round_dir = self.pending = self.quality = None
        self.active = False

    def _save_manifest(self):
        save_json(self.round_dir/'front_manifest.json', self.manifest)

    def capture_event(self, starting, trigger, duration, detail):
        if starting:
            # Reserve worst-case uncompressed color size plus headroom. Never
            # delete old rounds automatically when SD storage is running low.
            # Include USB original/processed images and the worst-case copy
            # fallback for front references when hard links are unavailable.
            stored_images = self.plan['positions'] + 3 * self.plan.get('upper_photos_requested', 0)
            required = stored_images * WIDTH * HEIGHT * 3 + 128 * 1024 * 1024
            if shutil.disk_usage(str(self.output)).free < required:
                raise RuntimeError('insufficient SD space for a complete front-only round')
            self.round_dir = self.output/('round_%08d_%d' % (trigger, time.monotonic_ns()))
            self.round_dir.mkdir(exist_ok=False)
            self.manifest = {'schema': 'qian-ov5640-only/v1', 'primary_camera': 'OV5640',
                'upper_camera_enabled': False, 'trigger': trigger, 'plan': dict(self.plan),
                'frames': [], 'events': [], 'capture_complete': False,
                'quality_passed_all': False, 'reconstruction_ready': False,
                'full_turn_verified': False, 'angle_calibrated': False,
                'clock_note': 'Directory uses monotonic time; wall clock may be incorrect',
                'processing': 'PL YCoCg ISP and full-frame quality; PS PNG/save/UDP',
                'storage_profile': STORAGE_PROFILE,
                'network_note': 'UDP send is not a receiver acknowledgment'}
            self.pending = self.quality = None
            self.active = True
        else:
            if not self.active or len(self.manifest['frames']) != detail or detail != self.plan['positions']:
                raise RuntimeError('front-only round has missing photographs')
            self.manifest.update(capture_complete=True,
                                 quality_passed_all=all(f['valid'] for f in self.manifest['frames']),
                                 frames_saved=detail)
            self.active = False
        self._save_manifest()
        self.transport.capture_event(starting, trigger, duration, detail)

    def send_capture_metadata(self, metadata):
        if metadata.get('kind') == 'sample':
            if not self.active or self.pending is not None:
                raise RuntimeError('sample metadata outside a round or previous sample unsaved')
            if metadata.get('position_index') != len(self.manifest['frames']):
                raise RuntimeError('sample position out of order')
            self.pending = dict(metadata)
        self.transport.send_capture_metadata(metadata)

    def send_quality(self, sequence, quality):
        if self.active:
            if self.pending is None or self.pending['sequence'] != sequence:
                raise RuntimeError('quality sequence does not match photograph')
            self.quality = dict(quality)
        self.transport.send_quality(sequence, quality)

    def send(self, image, **kwargs):
        if self.active:
            if (self.pending is None or self.quality is None or
                    self.pending['sequence'] != kwargs.get('sequence') or
                    self.pending['sample_frame'] != self.quality['frame']):
                raise RuntimeError('image, quality and sample metadata disagree')
            sample, quality = self.pending, self.quality
            position = sample['position_index']
            relative = 'images/A%04d.png' % position
            path = self.round_dir/relative
            path.parent.mkdir(exist_ok=True)
            started = time.monotonic()
            digest = save_png(path, image)
            reasons = quality_reasons(quality)
            record = {'position_index': position, 'sequence': kwargs['sequence'],
                'file': relative, 'sha256': digest, 'resolution': [WIDTH, HEIGHT],
                'fpga_quality': quality, 'sample': sample,
                'valid': not reasons, 'rejection_reasons': reasons,
                'saved_monotonic_ns': time.monotonic_ns(),
                'save_and_verify_s': time.monotonic() - started}
            self.manifest['frames'].append(record)
            self.pending = self.quality = None
            self._save_manifest()
            print('FRONT_SAVED ' + json.dumps(record, sort_keys=True), flush=True)
            if reasons:
                raise FrontQualityRejected('front quality rejected: ' + ', '.join(reasons))
        self.transport.send(image, **kwargs)

    def capture_abort(self, trigger, duration, sent_frames):
        if self.manifest is not None and self.active:
            self.manifest.update(capture_complete=False, aborted=True,
                                 frames_saved=len(self.manifest['frames']),
                                 frames_forwarded_before_abort=sent_frames)
            self.active = False
            self._save_manifest()
        self.transport.capture_abort(trigger, duration, sent_frames)

    def close(self):
        self.transport.close()
