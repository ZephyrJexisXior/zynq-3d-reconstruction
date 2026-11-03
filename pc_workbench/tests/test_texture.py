import tempfile
from pathlib import Path
import unittest
import importlib.util
from unittest.mock import patch
import numpy as np
from studio.texture_geometry import project,depth_buffer,read_cameras
from studio.jobs import JobManager
from studio.common import save_json,read_json
from tools.select_ring_subset import select


class TextureGeometryTests(unittest.TestCase):
    def view(self):
        return dict(R=np.eye(3),t=np.zeros(3),center=np.zeros(3),camera=dict(model='SIMPLE_RADIAL',size=[80,80],params=[40,40,40,0]))

    def test_camera_projection_handles_radial_distortion(self):
        v=self.view(); v['camera']['params'][-1]=.1
        uv,z=project(np.array([[1,0,2],[0,0,4]],float),v)
        np.testing.assert_allclose(uv,[[60.5,40],[40,40]])
        np.testing.assert_allclose(z,[2,4])

    def test_hidden_back_triangle_never_wins_depth(self):
        front=np.array([[-1,-1,2],[1,-1,2],[0,1,2]],float)
        back=front*2
        d=depth_buffer(np.r_[front,back],np.array([[3,4,5],[0,1,2]]),self.view(),80)
        self.assertAlmostEqual(float(d[40,40]),2,places=5)
        self.assertTrue(np.isinf(d[0,0]))

    def test_perspective_depth_is_not_linear_depth(self):
        # Projected screen triangle (20,20),(60,20),(40,60), z=2,4,8.
        vertices=np.array([[-1,-1,2],[2,-2,4],[0,4,8]],float)
        d=depth_buffer(vertices,np.array([[0,1,2]]),self.view(),80)
        self.assertAlmostEqual(float(d[40,40]),1/(.25/2+.25/4+.5/8),places=5)

    @unittest.skipUnless(importlib.util.find_spec('scipy'), 'Run in AI environment for SciPy subset assignment')
    def test_subset_has_18_distinct_angles_and_front_first(self):
        rows=[dict(id=str(i),relative_angle_deg=i*5) for i in range(72)]
        np.random.default_rng(4).shuffle(rows)
        subset=select(rows,18)
        self.assertEqual(len({r['id'] for r in subset}),18)
        self.assertEqual(subset[0]['relative_angle_deg'],0)
        self.assertEqual([r['relative_angle_deg'] for r in subset],list(range(0,360,20)))


class TextureFailureTests(unittest.TestCase):
    def manager(self,tmp):
        manager=JobManager(dict(data_dir=tmp,ai_python='python'))
        id_='job_20261102_123456_1234abcd'; job=manager.root/id_; job.mkdir()
        save_json(job/'state.json',dict(id=id_,status='running'))
        run=job/'run/shape'; save_json(run/'delivery/model_report.json',dict(texture_generated=False,artifacts={}))
        return manager,id_,run

    def test_texture_failure_preserves_gray_and_explains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager,id_,run=self.manager(tmp)
            (run/'delivery/model_untextured.glb').write_bytes(b'preserved')
            with patch.object(manager,'process',side_effect=RuntimeError('missing cameras')):
                manager.texture_action(id_,run)
            self.assertFalse(read_json(run/'delivery/model_report.json')['texture_generated'])
            self.assertIn('missing cameras',manager.state(id_)['texture_warning'])
            self.assertEqual((run/'delivery/model_untextured.glb').read_bytes(),b'preserved')

    def test_cancel_is_not_swallowed_as_completed_gray(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager,id_,run=self.manager(tmp)
            with patch.object(manager,'process',side_effect=InterruptedError('cancel')):
                with self.assertRaises(InterruptedError): manager.texture_action(id_,run)


if __name__=='__main__': unittest.main()
