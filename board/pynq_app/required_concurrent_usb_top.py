"""Concurrent acquisition with both complete PL-checked images required per stop."""
from concurrent_usb_top import ConcurrentUsbTopSender
from required_usb_pairs import RequiredUsbPairSender


class RequiredConcurrentUsbTopSender(ConcurrentUsbTopSender, RequiredUsbPairSender):
    """Compose concurrent acquisition/cleanup with the required-pair quality gate.

    MRO: Concurrent -> Required -> UsbTop. Concurrent.capture_pair always clears
    its prepared frame; Required.capture_pair raises for failed/rejected upper
    frames; Required.send_quality checks the front before sending its image.
    The existing stop loop cannot start the next move until send() returns.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.selected != list(range(self.positions)):
            raise ValueError('required concurrent mode needs both cameras at every position')

    def begin(self, trigger, static=False):
        super().begin(trigger, static)
        self.manifest.update(upper_camera_role='upper_multiview',
                             reconstruction_strategy='dual_camera_joint_unverified',
                             expected_pair_count=self.positions,
                             geometry_verified=False, reconstruction_ready=False)
        self._save_manifest()

    def capture_pair(self, front, position, sample=None):
        event = super().capture_pair(front, position, sample)
        self.manifest['frames'][-1]['camera_role'] = 'upper_multiview'
        self._save_manifest()
        return event
