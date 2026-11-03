"""Verify pair timing, failure cleanup, and nearest-frame selection offline."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]/'board'/'pynq_app'
sys.path.insert(0, str(APP))
from concurrent_capture import acquire_concurrently, ConcurrentUsbStill
from usb_capture_worker import closest_observed_frame


class ConcurrentTests(unittest.TestCase):
    def test_two_recent_frames_select_nearest_observation(self):
        before, after = (100, 110, 'before'), (130, 140, 'after')
        self.assertIs(closest_observed_frame([before, after], 118), before)
        self.assertIs(closest_observed_frame([before, after], 136), after)

    def worker(self, folder, failure=None):
        class Worker:
            closed = streaming = False
            target = None
            def start(self):
                if failure == 'start':
                    raise RuntimeError('warmup failed')
                self.streaming = True
                return dict(ready_monotonic_ns=1)
            def select(self, target):
                self.target = target
                if failure == 'select':
                    raise RuntimeError('USB stream failed')
                return dict(full_5mp=True, frame_observed_monotonic_ns=target-2_000_000,
                            frame_read_started_monotonic_ns=target-10_000_000)
            def close(self):
                self.closed = True
                self.streaming = False
        return Worker()

    def test_front_read_occurs_during_usb_stream_and_records_honest_timing(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            worker = self.worker(folder)
            def read_front():
                self.assertTrue(worker.streaming)
                self.assertFalse(worker.closed)
                self.assertIsNone(worker.target)
                return 'front frame', 'quality'
            result, timing = acquire_concurrently(read_front, folder, '/dev/fake',
                                                   worker_factory=lambda *args: worker)
            self.assertEqual(result, ('front frame', 'quality'))
            self.assertEqual(worker.target, timing['front_read_completed_monotonic_ns'])
            self.assertEqual(timing['software_observation_delta_ms'], -2)
            self.assertFalse(timing['hardware_synchronized'])
            self.assertFalse(timing['exposure_synchronization_verified'])
            self.assertTrue(worker.closed)
            self.assertEqual(json.loads((Path(folder)/'pair_timing.json').read_text()), timing)

    def test_worker_closed_if_front_acquisition_raises(self):
        worker = self.worker(None)
        def fail():
            raise TimeoutError('no complete front frame')
        with self.assertRaises(TimeoutError):
            acquire_concurrently(fail, '.', '/dev/fake', lambda *args: worker)
        self.assertTrue(worker.closed)
        self.assertIsNone(worker.target)

    def test_worker_closed_if_warmup_or_selection_fails(self):
        for phase in ('start', 'select'):
            with self.subTest(phase=phase):
                worker = self.worker(None, phase)
                with self.assertRaises(RuntimeError):
                    acquire_concurrently(lambda: None, '.', '/dev/fake', lambda *args: worker)
                self.assertTrue(worker.closed)

    def test_worker_lifecycle_close_is_safe_before_start(self):
        ConcurrentUsbStill('.', '/dev/fake').close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
