"""Bounded PS USB streaming during a front-camera read; no motion or PL access."""
import json
from pathlib import Path
import subprocess
import sys
import time


class ConcurrentUsbStill:
    def __init__(self, folder, device, timeout=30.0):
        self.folder, self.device = Path(folder), device
        self.timeout = timeout
        self.process = self.log = None

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=False)
        self.log = (self.folder/'usb_worker.log').open('w')
        worker = Path(__file__).with_name('usb_capture_worker.py')
        self.process = subprocess.Popen(
            [sys.executable, '-u', str(worker), '--device', self.device,
             '--output', str(self.folder/'upper_usb_decoded.png'), '--concurrent'],
            stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
            universal_newlines=True)
        deadline = time.monotonic()+self.timeout
        ready_path = self.folder/'usb_stream_ready.json'
        while not ready_path.exists():
            if self.process.poll() is not None:
                raise RuntimeError('USB stream failed before ready; see usb_worker.log')
            if time.monotonic() >= deadline:
                raise TimeoutError('USB stream warmup timed out')
            time.sleep(.01)
        ready = json.loads(ready_path.read_text())
        if ready.get('actual_size') != [2592, 1944] or not ready.get('streaming'):
            raise RuntimeError('USB stream did not prove a full 5MP warmup frame')
        return ready

    def select(self, target_ns):
        if self.process.poll() is not None:
            raise RuntimeError('USB stream stopped before selecting the front frame')
        self.process.stdin.write('SELECT %d\n' % target_ns)
        self.process.stdin.flush()
        self.process.stdin.close()
        code = self.process.wait(timeout=self.timeout)
        if code:
            raise RuntimeError('USB concurrent worker failed; see usb_worker.log')
        report = json.loads((self.folder/'upper_usb_decoded.json').read_text())
        if (not report.get('full_5mp') or
                report.get('front_target_monotonic_ns') != target_ns):
            raise RuntimeError('USB selected frame does not match this front request')
        return report

    def close(self):
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
                if self.process.stdin is not None and not self.process.stdin.closed:
                    self.process.stdin.close()
        finally:
            if self.log is not None:
                self.log.close()


def acquire_concurrently(acquire_front, folder, device, worker_factory=ConcurrentUsbStill):
    """Keep USB streaming while acquiring front; select by PS read completion.

    These are software observations on one monotonic clock. Neither timestamp
    reports sensor exposure, and USB buffering latency is not measured.
    """
    worker = worker_factory(folder, device)
    try:
        ready = worker.start()
        started_ns = time.monotonic_ns()
        result = acquire_front()
        observed_ns = time.monotonic_ns()
        usb = worker.select(observed_ns)
        usb_ns = usb['frame_observed_monotonic_ns']
        timing = dict(schema='qian-software-pair-timing/v1',
                      acquisition_mode='concurrent_stream_nearest_observation',
                      hardware_synchronized=False, exposure_synchronization_verified=False,
                      front_read_started_monotonic_ns=started_ns,
                      front_read_completed_monotonic_ns=observed_ns,
                      upper_read_started_monotonic_ns=usb['frame_read_started_monotonic_ns'],
                      upper_read_completed_monotonic_ns=usb_ns,
                      upper_stream_ready_monotonic_ns=ready['ready_monotonic_ns'],
                      software_observation_delta_ms=(usb_ns-observed_ns)/1e6,
                      software_observation_abs_delta_ms=abs(usb_ns-observed_ns)/1e6,
                      timestamp_source='PS_read_completion_not_sensor_exposure',
                      limitations='Different sensor/USB/ISP delays are unmeasured; close read times do not prove simultaneous exposure.')
        if ready['ready_monotonic_ns'] > started_ns:
            raise RuntimeError('USB stream readiness is later than front read start')
        (Path(folder)/'pair_timing.json').write_text(json.dumps(timing, indent=2))
        return result, timing
    finally:
        worker.close()
