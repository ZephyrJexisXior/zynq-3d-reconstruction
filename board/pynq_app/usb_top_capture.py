"""Sparse USB top processing with sequential acquisition by default.

Primary UDP frames keep their existing sequence and transport. USB frames are
saved separately, only after a primary still was sent, at the same stationary
position. The concurrent subclass prepares USB frames during the primary read
and reuses this module's PL processing. Motion remains in stop_capture.
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from dual_view import DualView
from fpga_control import quality_frame_is_complete
from stop_capture import _fresh_still_frame, _require_stopped

MAX_USB_TOP_PHOTOS = 18


def sparse_positions(positions, count):
    if not 1 <= positions <= 360 or not 0 <= count <= min(MAX_USB_TOP_PHOTOS, positions):
        raise ValueError('invalid primary/upper photo counts')
    return [i*positions//count for i in range(count)]


def save_png(path, image):
    if path.exists():
        raise ValueError('refusing to replace existing photograph')
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('PNG encoding failed')
    data = encoded.tobytes()
    if not np.array_equal(cv2.imdecode(np.frombuffer(data, np.uint8), 1), image):
        raise RuntimeError('PNG lossless roundtrip failed')
    temporary = path.with_suffix('.png.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    return hashlib.sha256(data).hexdigest()


def quality_reasons(q):
    # Same acquisition thresholds as the existing dual_stop_capture quality gate.
    if not quality_frame_is_complete(q, 2592, 1944, True):
        return ['incomplete_5mp']
    reasons = []
    if not 35 <= q['mean_luma'] <= 220:
        reasons.append('exposure')
    if q['dark_ratio'] > .35:
        reasons.append('too_dark')
    if q['saturated_ratio'] > .15:
        reasons.append('overexposed')
    if q['edges']/max(1, q['pixels']) < .001:
        reasons.append('too_few_edges')
    if q['noise_estimate'] > 15:
        reasons.append('excessive_horizontal_variation')
    return reasons


class UsbTopSender:
    def __init__(self, sender, core, output, positions=18, count=4,
                 device='/dev/video0', mode='ycocg', front_roi=(256, 80, 2336, 1840)):
        self.sender, self.core = sender, core
        self.output, self.device, self.mode = Path(output), device, mode
        self.positions, self.selected = positions, sparse_positions(positions, count)
        self.front_roi = tuple(front_roi)
        self.dual = None
        self.manifest = self.round_dir = self.sample = None
        self.front_quality = None

    def __getattr__(self, name):
        return getattr(self.sender, name)

    def _save_manifest(self):
        path = self.round_dir/'usb_top_manifest.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.manifest, indent=2))
        temporary.replace(path)

    def begin(self, trigger, static=False):
        self.round_dir = self.output/('round_%08d_%d' % (trigger, time.monotonic_ns()))
        self.round_dir.mkdir(parents=True, exist_ok=False)
        self.sample = None
        self.manifest = {'schema': 'qian-sparse-usb-top/v1', 'trigger': trigger,
                         'static_acceptance_only': static, 'primary_camera': 'OV5640',
                         'primary_positions': self.positions, 'upper_positions': self.selected,
                         'top_expected_count': len(self.selected),
                         'pair_policy': 'sparse_top_patch', 'frames': [], 'events': [],
                         'primary_round_finished': False, 'capture_complete': False,
                         'geometry_verified': False, 'reconstruction_ready': False,
                         'angle_calibrated': False, 'full_turn_verified': False,
                         'clock_note': 'Board wall clock may be wrong; directory uses monotonic time',
                         'processing': 'PS USB transport/decode and PNG; PL ISP/quality/FAST/BRIEF/matching'}
        self._save_manifest()
        print('USB_TOP_PLAN '+json.dumps({'upper_positions': self.selected,
              'manifest': str(self.round_dir/'usb_top_manifest.json')}), flush=True)

    def capture_event(self, starting, trigger, duration, detail):
        self.sender.capture_event(starting, trigger, duration, detail)
        if starting:
            self.begin(trigger)
        elif self.manifest is not None:
            saved = [r for r in self.manifest['frames'] if r.get('valid') is True]
            self.manifest.update(primary_round_finished=True, primary_frames_sent=detail,
                                 top_frames_saved=len(saved),
                                 capture_complete=(detail == self.positions and
                                                    len(saved) == len(self.selected)))
            self._save_manifest()

    def capture_abort(self, trigger, duration, sent_frames):
        if self.manifest is not None:
            self.manifest.update(primary_round_finished=False, aborted=True,
                                 primary_frames_sent=sent_frames)
            self._save_manifest()
        self.sender.capture_abort(trigger, duration, sent_frames)

    def send_capture_metadata(self, metadata):
        self.sender.send_capture_metadata(metadata)
        if metadata.get('kind') == 'sample':
            self.sample = dict(metadata)

    def send_quality(self, sequence, quality):
        self.front_quality = dict(quality)
        self.sender.send_quality(sequence, quality)

    def send(self, image, **kwargs):
        self.sender.send(image, **kwargs)
        sample, self.sample = self.sample, None
        if (self.manifest is not None and sample is not None and
                sample['sequence'] == kwargs.get('sequence') and
                sample['position_index'] in self.selected):
            self.capture_pair(image, sample['position_index'], sample)

    def _pair_folder(self, position):
        folder = self.round_dir/('position_%03d' % position)
        folder.mkdir(exist_ok=False)
        return folder

    def _prepare_usb_frame(self, folder, position):
        worker = Path(__file__).with_name('usb_capture_worker.py')
        process = subprocess.run([sys.executable, '-u', str(worker), '--device', self.device,
                                  '--output', str(folder/'upper_usb_decoded.png')],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=30, universal_newlines=True)
        (folder/'usb_worker.log').write_text(process.stdout)
        if process.returncode:
            raise RuntimeError('USB worker failed; see usb_worker.log')
        return json.loads((folder/'upper_usb_decoded.json').read_text())

    def assess_upper_quality(self, quality, raw):
        return quality_reasons(quality), {'policy': 'legacy_full_frame'}

    def save_front_reference(self, path, image):
        return save_png(path, image)

    def capture_pair(self, front, position, sample=None):
        if front.shape != (1944, 2592, 3) or front.dtype != np.uint8:
            raise ValueError('upper pair requires the actual full-resolution primary frame')
        trigger = _require_stopped(self.core)
        event = {'position_index': position, 'status': 'failed',
                 'hardware_trigger': trigger, 'sample': sample,
                 'geometry_verified': False, 'source_restored': False}
        started, switched = time.monotonic(), False
        folder = self._pair_folder(position)
        try:
            if not Path(self.device).exists():
                raise RuntimeError('optional USB camera is not present')
            front_hash = self.save_front_reference(folder/'front_reference.png', front)
            usb_report = self._prepare_usb_frame(folder, position)
            raw_bytes = (folder/'upper_usb_decoded.png').read_bytes()
            if (not usb_report['full_5mp'] or
                    hashlib.sha256(raw_bytes).hexdigest() != usb_report['sha256']):
                raise RuntimeError('USB frame evidence mismatch')
            raw = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), 1)
            if raw is None or raw.shape != (1944, 2592, 3):
                raise RuntimeError('USB saved frame is not complete 5MP')
            if _require_stopped(self.core) != trigger:
                raise RuntimeError('position changed during USB capture')
            if self.dual is None:
                self.dual = DualView(self.core)
            switched = True  # Even a partially failed source switch must be restored.
            self.core.set_roi(0, 0, 2592, 1944)
            _, feature_quality = self.dual.replay(front, bank=0, mode='raw')
            upper, quality = self.dual.replay(raw, bank=1, mode=self.mode)
            upper_hash = save_png(folder/'upper_pl_5mp.png', upper)
            match = self.dual.match()
            match.update(position_index=position, geometry_verified=False,
                         images_sha256={'front': front_hash, 'upper': upper_hash})
            (folder/'pl_matches.json').write_text(json.dumps(match, indent=2))
            reasons, quality_review = self.assess_upper_quality(quality, raw)
            record = {'camera_id': 'upper', 'camera_role': 'top_patch',
                      'position_index': position,
                      'file': str((folder/'upper_pl_5mp.png').relative_to(self.round_dir)),
                      'sha256': upper_hash, 'valid': not reasons, 'rejection_reasons': reasons,
                      'paired_front_file': str((folder/'front_reference.png').relative_to(self.round_dir)),
                      'paired_front_sha256': front_hash, 'paired_front_quality': self.front_quality,
                      'front_feature_replay_quality': feature_quality, 'fpga_quality': quality,
                      'quality_review': quality_review,
                      'pl_match_report': str((folder/'pl_matches.json').relative_to(self.round_dir)),
                      'pl_precheck_passed': match['correspondence_count'] >= 16,
                      'angle_calibrated': False, 'geometry_verified': False,
                      'reconstruction_ready': False, 'usb_capture': usb_report,
                      'processing': 'PL_ISP_quality_FAST9_BRIEF256_MUTUAL_HAMMING'}
            if (folder/'pair_timing.json').exists():
                record['capture_timing'] = json.loads((folder/'pair_timing.json').read_text())
                event['capture_timing'] = record['capture_timing']
            self.manifest['frames'].append(record)
            event.update(status='saved' if record['valid'] else 'quality_rejected',
                         file=record['file'], rejection_reasons=reasons,
                         pl_correspondences=match['correspondence_count'])
        except Exception as exc:
            event['error'] = str(exc)
            print('USB_TOP_SKIPPED '+json.dumps(event, sort_keys=True), flush=True)
        finally:
            try:
                if switched:
                    self.core.set_roi(*self.front_roi)
                    self.dual.restore_front(self.mode)
                    _, restored_quality = _fresh_still_frame(self.core, lambda f: True, timeout=20)
                    event['restored_front_quality'] = restored_quality
                if _require_stopped(self.core) != trigger:
                    raise RuntimeError('unexpected position after optional USB photograph')
                event['source_restored'] = True
            except BaseException as exc:
                event['restore_error'] = str(exc)
                raise  # Never allow the original loop to issue its next move.
            finally:
                event['elapsed_s'] = time.monotonic()-started
                self.manifest['events'].append(event)
                self._save_manifest()
        print('USB_TOP_RESULT '+json.dumps(event, sort_keys=True), flush=True)
        return event

    def close(self):
        try:
            if self.dual is not None:
                self.dual.release_input()
        finally:
            self.sender.close()
