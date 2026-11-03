"""Require both stationary 5MP images; PL feature counts remain a precheck."""
from usb_top_capture import UsbTopSender, quality_reasons


class PairQualityRejected(RuntimeError):
    """A quality rejection with no hardware/source failure; round stays incomplete."""


class RequiredUsbPairSender(UsbTopSender):
    def begin(self, trigger, static=False):
        super().begin(trigger, static)
        self.front_quality = None
        self.manifest.update(pair_policy='both_cameras_required',
                             capture_complete=False,
                             timestamp_note='PS observation times, not hardware exposure synchronization')
        self._save_manifest()

    def send_quality(self, sequence, quality):
        if self.sample is not None:
            reasons = quality_reasons(quality)
            if quality.get('pixels') != 2592 * 1944:
                reasons.append('quality_roi_is_not_full_frame')
            if reasons:
                raise PairQualityRejected('front quality rejected: ' + ', '.join(reasons))
        super().send_quality(sequence, quality)

    def capture_pair(self, front, position, sample=None):
        event = super().capture_pair(front, position, sample)
        if event.get('status') != 'saved' or event.get('source_restored') is not True:
            error = (PairQualityRejected if event.get('status') == 'quality_rejected'
                     and event.get('source_restored') is True else RuntimeError)
            raise error('required upper photograph failed at position %d: %s' %
                        (position, event.get('error') or event.get('rejection_reasons')))
        self.manifest['frames'][-1].update(
            motion_position_index=sample.get('motion_position_index') if sample else None,
            front_observed_monotonic_ns=sample.get('front_observed_monotonic_ns') if sample else None)
        self._save_manifest()
        return event

    def capture_event(self, starting, trigger, duration, detail):
        if not starting:
            records = self.manifest['frames'] if self.manifest else []
            accepted = [r['position_index'] for r in records if r.get('valid') is True]
            if sorted(accepted) != list(range(self.positions)) or detail != self.positions:
                raise RuntimeError('cannot finish: both cameras must provide every planned position')
            self.manifest.update(capture_complete=True, accepted_pairs=len(accepted))
        super().capture_event(starting, trigger, duration, detail)

    def capture_abort(self, trigger, duration, sent_frames):
        if self.manifest is not None:
            self.manifest['capture_complete'] = False
        super().capture_abort(trigger, duration, sent_frames)
