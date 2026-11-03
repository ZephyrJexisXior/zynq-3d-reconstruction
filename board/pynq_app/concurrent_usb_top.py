"""Acquire front/top concurrently at the same stop; replay through PL in order."""
import json

from concurrent_capture import acquire_concurrently
from stop_capture import _require_stopped
from usb_top_capture import UsbTopSender


class ConcurrentUsbTopSender(UsbTopSender):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prepared = None

    def begin(self, trigger, static=False):
        if self.prepared is not None:
            raise RuntimeError('unconsumed concurrent pair from a previous round')
        super().begin(trigger, static)
        self.manifest.update(acquisition_mode='concurrent_stream_nearest_observation',
                             hardware_synchronized=False)
        self._save_manifest()

    def acquire_still(self, position, acquire_front):
        if position not in self.selected:
            return acquire_front()
        if self.prepared is not None:
            raise RuntimeError('previous concurrent pair was not consumed')
        trigger = _require_stopped(self.core)
        folder = self.round_dir/('position_%03d' % position)
        result, timing = acquire_concurrently(acquire_front, folder, self.device)
        if _require_stopped(self.core) != trigger:
            raise RuntimeError('turntable position changed during concurrent capture')
        self.prepared = dict(position=position, folder=folder, timing=timing)
        print('CONCURRENT_PAIR_CAPTURED '+json.dumps(timing, sort_keys=True), flush=True)
        return result

    def _pair_folder(self, position):
        if self.prepared is None or self.prepared['position'] != position:
            raise RuntimeError('no concurrently acquired pair for this position')
        return self.prepared['folder']

    def _prepare_usb_frame(self, folder, position):
        if folder != self._pair_folder(position):
            raise RuntimeError('concurrent pair folder mismatch')
        return json.loads((folder/'upper_usb_decoded.json').read_text())

    def capture_pair(self, front, position, sample=None):
        try:
            event = super().capture_pair(front, position, sample)
            if event['status'] not in ('saved', 'quality_rejected') or not event['source_restored']:
                raise RuntimeError('concurrent pair processing failed: '+json.dumps(event))
            return event
        finally:
            self.prepared = None
