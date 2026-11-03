"""Check independent PNG decoding, color/order, integrity and reference reuse."""
import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from offline_hardware import block_hardware
block_hardware()

ROOT = Path(__file__).resolve().parents[1]/'board'
sys.path.insert(0, str(ROOT/'pynq_app'))
import cv2
import numpy as np
from fast_lossless_png import encode_bgr, save_verified_png
from front18_top4_capture import Front18Top4Sender


class FastStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(__file__).parent))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = np.random.RandomState(42).randint(0, 256, (61, 79, 3)).astype('uint8')

    def test_png_decoder_preserves_every_sample_and_color_channel(self):
        self.image[0, :4] = [[0, 0, 255], [255, 0, 0], [0, 255, 0], [255, 255, 255]]
        encoded = encode_bgr(self.image)
        decoded = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(decoded, self.image)

    def test_noncontiguous_and_reversed_views_preserve_orientation(self):
        view = self.image[::-2, 1::3]
        decoded = cv2.imdecode(np.frombuffer(encode_bgr(view), np.uint8), 1)
        np.testing.assert_array_equal(decoded, view)

    def test_saved_file_matches_hash_and_decoder(self):
        path = self.root/'frame.png'
        digest = save_verified_png(path, self.image)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        np.testing.assert_array_equal(cv2.imread(str(path)), self.image)

    def test_existing_files_and_pending_writes_are_preserved(self):
        path = self.root/'frame.png'
        path.write_bytes(b'original')
        with self.assertRaises(ValueError):
            save_verified_png(path, self.image)
        self.assertEqual(path.read_bytes(), b'original')
        pending = self.root/'pending.png.tmp'
        pending.write_bytes(b'pending')
        with self.assertRaises(ValueError):
            save_verified_png(self.root/'pending.png', self.image)
        self.assertEqual(pending.read_bytes(), b'pending')

    def test_invalid_input_rejected_without_final_file(self):
        for image in (self.image.astype('float32'), self.image[:, :, 0],
                      self.image[:0], np.zeros((2, 2, 4), dtype='uint8')):
            with self.assertRaises(ValueError):
                encode_bgr(image)

    def test_bad_decode_cannot_publish_an_accepted_file(self):
        path = self.root/'bad.png'
        with patch('fast_lossless_png.cv2.imdecode', return_value=None):
            with self.assertRaises(RuntimeError):
                save_verified_png(path, self.image)
        self.assertFalse(path.exists())

    def reference_sender(self):
        source = self.root/'front.png'
        digest = save_verified_png(source, self.image)
        sender = Front18Top4Sender.__new__(Front18Top4Sender)
        sender.sender = types.SimpleNamespace(round_dir=self.root, manifest={'frames': [
            dict(file='front.png', sha256=digest, valid=True, position_index=0)]})
        folder = self.root/'position_000'
        folder.mkdir()
        return sender, source, folder/'front_reference.png'

    def test_duplicate_reference_reuses_the_verified_file(self):
        sender, source, target = self.reference_sender()
        digest = sender.save_front_reference(target, self.image)
        self.assertEqual(source.stat().st_ino, target.stat().st_ino)
        self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())
        np.testing.assert_array_equal(cv2.imread(str(target)), self.image)

    def test_link_fallback_copies_identical_bytes(self):
        sender, source, target = self.reference_sender()
        with patch('front18_top4_capture.os.link', side_effect=OSError('cross-device link')):
            sender.save_front_reference(target, self.image)
        self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_wrong_position_cannot_be_reused(self):
        sender, source, target = self.reference_sender()
        sender.sender.manifest['frames'][0]['position_index'] = 1
        with self.assertRaises(RuntimeError):
            sender.save_front_reference(target, self.image)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
