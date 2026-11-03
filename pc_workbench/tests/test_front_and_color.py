import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from studio.common import read_json,save_json,sha256
from studio.ingest import default_front
from studio.jobs import JobManager


class FrontConventionTests(unittest.TestCase):
    def test_penultimate_is_within_main_camera_sorted_by_position(self):
        rows=[]
        for i in range(18):
            rows.extend([dict(id=f'u{i}',camera='upper',camera_role='upper',position_index=i),
                         dict(id=f'm{i}',camera='main',camera_role='primary',position_index=i)])
        rows.reverse()
        self.assertEqual(default_front(dict(photos=rows,capture_verified=True))['id'],'m16')
        self.assertEqual(default_front(dict(photos=rows,capture_verified=False))['id'],'m0')

    def test_penultimate_uses_order_not_photo_identifier(self):
        rows=[dict(id='last',camera='x',capture_order=9),dict(id='start',camera='x',capture_order=0),
              dict(id='chosen',camera='x',capture_order=8)]
        self.assertEqual(default_front(dict(photos=rows,capture_verified=True))['id'],'chosen')


class ColorLifecycleTests(unittest.TestCase):
    def make_job(self,tmp):
        m=JobManager(dict(data_dir=tmp))
        id_='job_20261103_000000_abcdef12';p=m.root/id_;p.mkdir()
        run=p/'run_original';delivery=run/'shape/delivery';delivery.mkdir(parents=True)
        gray=delivery/'model_untextured.glb';gray.write_bytes(b'immutable-gray-test')
        save_json(delivery/'model_report.json',dict(texture_generated=False,
            artifacts=dict(model_untextured=dict(file=gray.name,sha256=sha256(gray)))))
        save_json(run/'inputs/views.json',dict(views={},original_selection={}))
        save_json(run/'inputs/calibration/result.json',dict(snapshot='original calibration'))
        save_json(p/'state.json',dict(id=id_,status='complete',result='run_original/shape/delivery',run_id='run_original'))
        return m,id_,p,gray

    def wait(self,m):
        for _ in range(250):
            if not m.active:return
            time.sleep(.02)
        self.fail('worker still active')

    def test_color_retains_gray_and_uses_immutable_inputs_without_shape_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,p,gray=self.make_job(tmp);digest=sha256(gray)
            def bake(id_,result):
                self.assertEqual(read_json(result.parent/'inputs/calibration/result.json')['snapshot'],'original calibration')
                report=read_json(result/'delivery/model_report.json');report['texture_generated']=True
                save_json(result/'delivery/model_report.json',report)
            with patch.object(m,'texture_action',side_effect=bake),patch.object(m,'generate_action') as shape:
                m.color_existing(id_);self.wait(m);shape.assert_not_called()
            state=m.state(id_)
            self.assertTrue(state['texture_generated'])
            self.assertEqual(sha256(p/state['result']/'model_untextured.glb'),digest)
            self.assertEqual(sha256(gray),digest)
            self.assertNotEqual(state['result'],'run_original/shape/delivery')

    def test_texture_failure_keeps_original_gray_as_available_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,p,gray=self.make_job(tmp)
            with patch.object(m,'texture_action'):
                m.color_existing(id_);self.wait(m)
            self.assertEqual(m.state(id_)['result'],'run_original/shape/delivery')
            self.assertTrue(gray.exists())
            self.assertEqual(m.state(id_)['status'],'complete')

    def test_corrupted_gray_is_rejected_before_texture(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,p,gray=self.make_job(tmp);gray.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'灰模校验失败'):m.color_existing(id_)

    def test_deferred_color_calibrates_without_regenerating_gray(self):
        for status in ('ready','failed'):
            with tempfile.TemporaryDirectory() as tmp:
                m,id_,p,gray=self.make_job(tmp);m.cfg['python']='python';digest=sha256(gray)
                (p/'run_original/inputs/calibration/result.json').unlink()
                save_json(p/'run_original/inputs/photos.json',dict(photos=[]))
                def calibration(id_,argv,parse):
                    self.assertIn('--calibration-only',argv)
                    run=Path(argv[argv.index('--run')+1])
                    save_json(run.parent/'calibration_status.json',dict(status=status,detail='test calibration'))
                    if status=='ready':save_json(run.parent/'inputs/calibration/result.json',dict(snapshot='deferred'))
                def bake(id_,result):
                    report=read_json(result/'delivery/model_report.json');report['texture_generated']=True
                    save_json(result/'delivery/model_report.json',report)
                with patch.object(m,'process',side_effect=calibration),patch.object(m,'texture_action',side_effect=bake) as texture,patch.object(m,'generate_action') as shape:
                    m.color_existing(id_);self.wait(m);shape.assert_not_called()
                    self.assertEqual(texture.call_count,1 if status=='ready' else 0)
                self.assertEqual(m.state(id_)['status'],'complete')
                self.assertEqual(sha256(gray),digest)
                if status=='failed':self.assertEqual(m.state(id_)['result'],'run_original/shape/delivery')


class TurntableProposalTests(unittest.TestCase):
    def test_consensus_rejects_moving_support_and_does_not_use_color(self):
        from studio.turntable import consensus_subject_box
        p=dict(box=[.2,.1,.8,.6],support_top=.62,support_width=.5)
        self.assertEqual(consensus_subject_box([p,p,p,p]),p['box'])
        self.assertIsNone(consensus_subject_box([p,p,None,None]))
        self.assertIsNone(consensus_subject_box([p,p,p,dict(p,support_top=.8)]))

    def test_wide_object_without_narrower_top_is_not_cut(self):
        try:import cv2
        except ImportError:self.skipTest('OpenCV only in processing runtime')
        from studio.turntable import support_proposal
        alpha=np.zeros((400,500),np.uint8);alpha[40:340,120:380]=255
        self.assertIsNone(support_proposal(alpha))


if __name__=='__main__':unittest.main()
