import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
from studio.common import save_json, read_json, sha256, DIRECTIONS
from studio.jobs import JobManager
from studio.quick_views import suggest_views
from tools.build_gray import run_workers


class IndependentGrayTests(unittest.TestCase):
    def test_estimates_use_primary_ring_and_never_claim_calibration(self):
        rows=[]
        for i in range(18):
            rows.extend([dict(id=f'u{i}',camera='upper',camera_role='upper',position_index=i),
                         dict(id=f'p{i}',camera='main',camera_role='primary',position_index=i)])
        report=dict(photos=rows[::-1],capture_verified=True)
        self.assertEqual(suggest_views(report),dict(front='p16',left='p3',back='p7',right='p12'))
        self.assertEqual(suggest_views(report,increasing='right')['left'],'p11')
        report['capture_verified']=False
        with self.assertRaisesRegex(ValueError,'手选四方向'):suggest_views(report)

    def test_gray_is_published_before_failed_calibration_finishes(self):
        release=threading.Event();seen=[]
        def cal():
            if not release.wait(3):raise RuntimeError('gray was blocked')
            raise ValueError('unregistered photographs')
        def publish(key,value):
            seen.append((key,value['status']))
            if key=='shape':release.set()
        report=run_workers(lambda:dict(status='ready'),cal,publish)
        self.assertEqual(seen,[('shape','ready'),('calibration','failed')])
        self.assertEqual(report['shape']['status'],'ready')

    def test_gray_succeeds_when_parallel_calibration_fails_or_is_skipped(self):
        for calibration in ('failed','skipped'):
            with tempfile.TemporaryDirectory() as tmp:
                m=JobManager(dict(data_dir=tmp,python='python'))
                id_='job_20261103_100000_abcdef12';p=m.root/id_;p.mkdir()
                save_json(p/'state.json',dict(id=id_,status='running',started=time.time()))
                save_json(p/'photos.json',dict(photos=[]))
                views={d:'p'+str(i) for i,d in enumerate(DIRECTIONS)}
                save_json(p/'selection.json',dict(views=views,boxes={}))
                (p/'masks').mkdir()
                for d in DIRECTIONS:Image.new('RGBA',(32,32),'white').save(p/'masks'/f'{d}.png')
                def worker(id_,argv,parse):
                    run=Path(argv[argv.index('--run')+1]);delivery=run/'delivery';delivery.mkdir(parents=True)
                    gray=delivery/'model_untextured.glb';gray.write_bytes(b'gray')
                    save_json(delivery/'model_report.json',dict(artifacts={'model_untextured':dict(file=gray.name,sha256=sha256(gray))}))
                    status=dict(status=calibration,detail='not calibrated')
                    save_json(run.parent/'calibration_status.json',status)
                    parse('GRAY_READY {}')
                    self.assertIsNotNone(m.state(id_)['result'])
                    parse('CAL_STATE '+json.dumps(status))
                    if calibration=='skipped':self.assertIn('--skip-calibration',argv)
                with patch('studio.jobs.validate_views'),patch.object(m,'process',side_effect=worker),patch.object(m,'texture_action') as texture:
                    m.generate_action(id_,True,with_texture=False,prepare_color=calibration!='skipped')
                    texture.assert_not_called()
                s=m.state(id_)
                self.assertEqual(s['status'],'complete')
                self.assertTrue((p/s['result']/'model_untextured.glb').exists())
                self.assertEqual(s['calibration']['status'],calibration)
                self.assertFalse(s['texture_generated'])


if __name__=='__main__':unittest.main()
