"""Independent paired18 quality review in PL; recover only known quality failures.

The original whole-frame gate remains authoritative for completeness/exposure.
Only a low edge density may be reviewed in a fixed central subject window.
All full-frame metrics, warnings, and ROI evidence remain in the manifest.
This is an acquisition precheck, not a geometry or reconstruction certificate.
"""
import json

from required_concurrent_usb_top import RequiredConcurrentUsbTopSender
from required_usb_pairs import PairQualityRejected
from stop_capture import _require_stopped, run_stop_capture_loop
from usb_top_capture import quality_reasons


SUBJECT_ROI = (648, 243, 1944, 1701)
SUBJECT_MIN_EDGES = 2000


class ReviewedConcurrentPairSender(RequiredConcurrentUsbTopSender):
    def assess_upper_quality(self, quality, raw):
        reasons = quality_reasons(quality)
        review = {'policy': 'full_frame_with_PL_subject_edge_review_v1',
                  'full_frame_reasons': list(reasons), 'warnings': [],
                  'roi_recheck_performed': False}
        # Do not relax incomplete frames, poor exposure, clipping, or noise.
        if reasons != ['too_few_edges']:
            return reasons, review
        _require_stopped(self.core)
        self.core.set_roi(*SUBJECT_ROI)
        try:
            # Replay the unchanged USB original through the same PL ISP mode.
            # bank=None preserves the full-frame feature/matching evidence.
            _, roi_quality = self.dual.replay(raw, bank=None, mode=self.mode)
        finally:
            self.core.set_roi(0, 0, 2592, 1944)
        roi_reasons = quality_reasons(roi_quality)
        x0, y0, x1, y1 = SUBJECT_ROI
        if roi_quality.get('pixels') != (x1-x0)*(y1-y0):
            roi_reasons.append('subject_roi_pixel_count_mismatch')
        if roi_quality.get('edges', 0) < SUBJECT_MIN_EDGES:
            roi_reasons.append('subject_has_too_few_edges')
        review.update(roi_recheck_performed=True, subject_roi=list(SUBJECT_ROI),
                      minimum_subject_edges=SUBJECT_MIN_EDGES,
                      subject_fpga_quality=roi_quality, subject_reasons=roi_reasons)
        if not roi_reasons:
            review['warnings'] = ['low_full_frame_edge_density']
            reasons = []
        print('UPPER_QUALITY_REVIEW '+json.dumps(review, sort_keys=True), flush=True)
        return reasons, review

    def capture_abort(self, trigger, duration, sent_frames):
        # A front rejection can precede capture_pair(), whose finally normally
        # clears this prepared pair. Keep its files, but clear pending state.
        self.prepared = None
        self.sample = None
        self.front_quality = None
        super().capture_abort(trigger, duration, sent_frames)


def run_recovering_capture_loop(core, sender, *args, **kwargs):
    while True:
        try:
            return run_stop_capture_loop(core, sender, *args, **kwargs)
        except PairQualityRejected as exc:
            # The unchanged loop's finally has stopped motion and marked the
            # round aborted. No motion is retried automatically. Calling it
            # again arms the same timer and awaits a fresh physical KEY0 edge.
            _require_stopped(core)
            print('PAIR_QUALITY_ABORTED '+json.dumps({
                'error': str(exc), 'capture_complete': False,
                'action': 'wait_for_new_KEY0', 'automatic_motion_retry': False,
                'manifest': str(sender.round_dir/'usb_top_manifest.json')
            }, sort_keys=True), flush=True)
