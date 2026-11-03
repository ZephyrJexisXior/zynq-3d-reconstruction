import unittest
import numpy as np
try:
    import cv2
except ImportError:
    cv2 = None
from studio.foreground_boundary import refine_foreground
from studio.processing import Foreground, exclude_background


@unittest.skipUnless(cv2, 'requires processing OpenCV runtime')
class ForegroundBoundaryTests(unittest.TestCase):
    def test_context_recovers_white_connection_without_filling_real_gap(self):
        # A generic object with a white connecting strip, a separate dark lobe,
        # and an actual hole. Same white RGB also appears in the background.
        rgb = np.full((160,180,3), 235, np.uint8)
        truth = np.zeros((160,180),bool)
        truth[20:120,70:130] = True
        truth[70:95,25:80] = True
        truth[65:105,15:45] = True
        truth[40:55,90:110] = False
        rgb[truth] = (60,70,80)
        rgb[70:95,45:75] = (235,235,235)
        primary = np.where(truth,.99,.001).astype('float32')
        primary[70:95,45:70] = .01
        context = np.where(truth,.99,.001).astype('float32')
        # An unrelated context-only detection must not be added.
        context[125:145,145:165] = .99
        alpha, info = refine_foreground(rgb,primary,context)
        self.assertTrue((alpha[74:91,49:66]==255).all())
        self.assertTrue((alpha[43:52,93:107]==0).all())
        self.assertTrue((alpha[128:142,148:162]==0).all())
        self.assertTrue((alpha[:10]==0).all())
        self.assertGreater(info['restored_pixels'],0)

    def test_native_edges_do_not_become_blanket_dilation(self):
        rgb=np.full((100,100,3),230,np.uint8);rgb[20:80,30:70]=(20,30,40)
        score=np.full((100,100),.001,np.float32);score[20:80,30:70]=.99
        alpha,_=refine_foreground(rgb,score)
        self.assertTrue((alpha[22:78,32:68]==255).all())
        self.assertFalse(alpha[:19].any());self.assertFalse(alpha[:,71:].any())

    def test_transparent_input_and_explicit_roi_stay_authoritative(self):
        rgba=np.full((40,40,4),255,np.uint8);rgba[...,3]=0;rgba[8:32,8:32,3]=123
        result,_=Foreground('unused.onnx').segment(rgba,[.25,.25,.75,.75])
        self.assertTrue(np.array_equal(result[...,:3],rgba[...,:3]))
        self.assertTrue((result[10:30,10:30,3]==123).all())
        self.assertFalse(result[:10,:,3].any())
        excluded=exclude_background(result,[[.5,0,1,1]])
        self.assertFalse(excluded[:,20:,3].any())


if __name__=='__main__':unittest.main()
