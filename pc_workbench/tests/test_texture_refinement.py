import unittest
import importlib.util
import numpy as np
from studio.texture_refinement import coherent_labels,srgb_to_linear,linear_to_srgb


class PhotoPatchTests(unittest.TestCase):
    def test_invisible_photo_never_selected_even_if_neighbors_prefer_it(self):
        scores=np.array([[.9,0,.9],[.8,.8,.8]])
        colors=np.full((2,3,3),.5)
        labels,report=coherent_labels(colors,scores,np.array([[0,1],[1,2]]),np.tile([0,0,1.],(3,1)))
        self.assertEqual(labels[1],1)
        self.assertTrue(np.all(scores[labels,np.arange(3)]>0))

    def test_unobserved_face_stays_unobserved(self):
        scores=np.array([[.9,0,.9],[.8,0,.8]])
        labels,_=coherent_labels(np.full((2,3,3),.5),scores,np.array([[0,1],[1,2]]),np.tile([0,0,1.],(3,1)))
        self.assertEqual(labels[1],-1)

    def test_srgb_roundtrip_preserves_white_and_dark_colors(self):
        rgb=np.array([0,.001,.04,.5,1])
        np.testing.assert_allclose(linear_to_srgb(srgb_to_linear(rgb)),rgb,atol=1e-6)


@unittest.skipUnless(importlib.util.find_spec('scipy'),'AI worker environment required')
class CompletionTests(unittest.TestCase):
    def test_disconnected_part_has_no_fabricated_neighbor_evidence(self):
        from studio.texture_refinement import complete_vertex_colors
        vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,.01],[1,0,.01],[0,1,.01]],float)
        faces=np.array([[0,1,2],[3,4,5]]);normals=np.tile([0,0,1.],(6,1))
        colors=np.zeros((6,3));colors[0]=[.8,.2,.1]
        known=np.array([True,False,False,False,False,False])
        result,filled=complete_vertex_colors(vertices,faces,normals,colors,known)
        np.testing.assert_allclose(result[:3],np.tile(colors[0],(3,1)))
        np.testing.assert_array_equal(filled,[False,True,True,False,False,False])
        np.testing.assert_array_equal(result[3:],0)

    def test_observed_vertex_colors_never_modified_by_completion(self):
        from studio.texture_refinement import complete_vertex_colors
        vertices=np.array([[0,0,0],[1,0,0],[0,1,0]],float);faces=np.array([[0,1,2]])
        colors=np.array([[1,1,1],[0,0,0],[0,0,0]],float);known=np.array([True,True,False])
        result,filled=complete_vertex_colors(vertices,faces,np.tile([0,0,1.],(3,1)),colors,known)
        np.testing.assert_array_equal(result[known],colors[known]);self.assertTrue(filled[2])
        self.assertTrue(np.all(result[2]>=0)&np.all(result[2]<=1))

    def test_unrelated_materials_do_not_trigger_exposure_fit(self):
        from studio.texture_refinement import exposure_gains
        colors=np.array([np.tile([.8,.1,.1],(1000,1)),np.tile([.1,.1,.8],(1000,1))])
        gains,report=exposure_gains(colors,np.ones((2,1000)),0)
        np.testing.assert_array_equal(gains,1);self.assertFalse(report['applied'])


if __name__=='__main__':unittest.main()
