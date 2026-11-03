"""Four concurrent top patches; retain the tested PL ISP/quality/matching path."""
from concurrent_usb_top import ConcurrentUsbTopSender
from paired_quality_recovery import ReviewedConcurrentPairSender
from required_usb_pairs import PairQualityRejected
import hashlib
import os
from pathlib import Path
import shutil


class Front18Top4Sender(ConcurrentUsbTopSender):
    # Reuse the existing PL subject-ROI review without requiring 18 upper
    # images. No ISP/feature/matching algorithm or threshold is changed.
    assess_upper_quality = ReviewedConcurrentPairSender.assess_upper_quality

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.positions != 18 or self.selected != [0, 4, 9, 13]:
            raise ValueError('this entry requires 18 front images and 4 selected top images')

    def begin(self, trigger, static=False):
        super().begin(trigger, static)
        self.manifest.update(front_photos_requested=18, upper_photos_requested=4,
            upper_camera_role='top_patch', pair_policy='sparse_top_patch',
            reconstruction_strategy='OV5640_primary_with_four_upper_patches',
            front_manifest=str(self.sender.round_dir/'front_manifest.json'),
            motion_profile='requested18_580ms_1612us')
        self._save_manifest()

    def capture_pair(self, front, position, sample=None):
        event = super().capture_pair(front, position, sample)
        if event['status'] == 'quality_rejected':
            raise PairQualityRejected('top patch quality rejected: '+
                                      ', '.join(event.get('rejection_reasons', [])))
        return event

    def save_front_reference(self, path, image):
        # FrontOnlySender has already saved/decoded/verified this very sample.
        # Link that immutable file instead of encoding a second 5MP PNG.
        record = self.sender.manifest['frames'][-1]
        position = int(Path(path).parent.name.split('_')[-1])
        if record['position_index'] != position or not record['valid']:
            raise RuntimeError('front reference does not match the saved position')
        source = self.sender.round_dir/record['file']
        path = Path(path)
        if path.exists():
            raise ValueError('front reference already exists')
        try:
            os.link(str(source), str(path))
        except OSError:
            # Different filesystems may reject hard links; preserve identical
            # bytes and the integrity check rather than re-encode the image.
            with path.open('xb') as destination, source.open('rb') as original:
                shutil.copyfileobj(original, destination)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record['sha256']:
            raise RuntimeError('saved front reference failed SHA256 verification')
        return digest

    def capture_abort(self, trigger, duration, sent_frames):
        self.prepared = None
        self.sample = None
        self.front_quality = None
        super().capture_abort(trigger, duration, sent_frames)
