"""Simulate complete 18+4 routing using the actual sender chain; no motor IO."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from offline_hardware import block_hardware
block_hardware()

ROOT = Path(__file__).resolve().parents[1]/'board'
sys.path.insert(0, str(ROOT/'pynq_app'))
import numpy as np
from main_front18_top4 import capture_plan
from front_only_capture import FrontOnlySender
from front18_top4_capture import Front18Top4Sender
from paired_quality_recovery import ReviewedConcurrentPairSender
from required_usb_pairs import PairQualityRejected


class NullTransport:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Core:
    def __init__(self):
        self.mmio = self
    def button_capture_state(self):
        return 0, 0, 0
    def read(self, offset):
        assert offset == 0x28
        return 1500


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(__file__).parent))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.front = FrontOnlySender(NullTransport(), self.root/'front', capture_plan())
        self.sender = Front18Top4Sender(self.front, Core(), self.root/'top', 18, 4)

    def test_plan_and_algorithms_retained(self):
        p = capture_plan()
        self.assertEqual((p['positions'], p['upper_photos_requested']), (18, 4))
        self.assertEqual(p['upper_positions'], [0, 4, 9, 13])
        self.assertEqual(p['move_durations_s'], [.58]*18)
        self.assertEqual((p['motion_command_us'], p['stop_command_us']), (1612, 1500))
        self.assertIs(Front18Top4Sender.assess_upper_quality,
                      ReviewedConcurrentPairSender.assess_upper_quality)

    def test_complete_round_routes_exactly_four_concurrent_captures(self):
        image = np.zeros((1944, 2592, 3), dtype=np.uint8)
        q = dict(frame=99, frame_complete=True, frame_bad=False, rows=1944,
            frame_pixels=2592*1944, shape_flags=55, pixels=2592*1944,
            mean_luma=100, dark_ratio=.01, saturated_ratio=0,
            edges=15000, noise_estimate=1)
        def pair(sender, front, position, sample=None):
            sender.manifest['frames'].append({'valid': True, 'position_index': position})
            return dict(status='saved', source_restored=True)
        def acquire(acquire_front, folder, device):
            return acquire_front(), {'hardware_synchronized': False}
        self.sender.capture_event(True, 1, 45.72, 0)
        with patch('front_only_capture.save_png', return_value='a'*64), \
             patch('usb_top_capture.UsbTopSender.capture_pair', new=pair), \
             patch('concurrent_usb_top.acquire_concurrently', side_effect=acquire) as concurrent:
            for position in range(18):
                result, quality = self.sender.acquire_still(position, lambda: (image, q))
                self.sender.send_capture_metadata(dict(kind='sample', sequence=position,
                    position_index=position, sample_frame=99))
                self.sender.send_quality(position, quality)
                self.sender.send(result, sequence=position)
            self.assertEqual(concurrent.call_count, 4)
        self.sender.capture_event(False, 1, 45.72, 18)
        self.assertEqual(len(self.front.manifest['frames']), 18)
        self.assertEqual([x['position_index'] for x in self.sender.manifest['frames']], [0, 4, 9, 13])
        self.assertTrue(self.front.manifest['capture_complete'])
        self.assertTrue(self.sender.manifest['capture_complete'])

    def test_rejection_still_aborts_and_clears_prepared_frame(self):
        self.sender.capture_event(True, 1, 45.72, 0)
        self.sender.prepared = {'position': 0}
        with patch('concurrent_usb_top.ConcurrentUsbTopSender.capture_pair',
                   return_value=dict(status='quality_rejected', rejection_reasons=['exposure'])):
            with self.assertRaises(PairQualityRejected):
                self.sender.capture_pair(None, 0)
        self.sender.capture_abort(1, 45.72, 0)
        self.assertIsNone(self.sender.prepared)
        self.assertFalse(self.front.manifest['capture_complete'])
        self.assertFalse(self.sender.manifest['capture_complete'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
