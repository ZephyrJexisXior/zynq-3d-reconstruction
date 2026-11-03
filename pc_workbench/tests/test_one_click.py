import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import numpy as np
from PIL import Image
from studio.capture_import import CaptureImporter
from studio.common import read_json, save_json, sha256, DIRECTIONS
from studio.direction_geometry import choose_directions,validate_manual_directions
from studio.ingest import import_folder, validate_capture, primary_sequence
from studio.jobs import JobManager
from studio.processing import write_stages
from vendor.fpga import receiver
from vendor.fpga.protocol import QUALITY_EXT
from test_protocol import packets


class DirectionTests(unittest.TestCase):
    def test_upper_can_arrive_first_without_becoming_front_reference(self):
        photos=[dict(id='u0',camera='upper',camera_role='upper',position_index=0),
                dict(id='f1',camera='front',camera_role='primary',position_index=1),
                dict(id='f0',camera='front',camera_role='primary',position_index=0)]
        self.assertEqual([r['id'] for r in primary_sequence(photos)],['f0','f1'])

    def ring(self, count=18):
        rows = []
        for i, angle in enumerate(np.linspace(0, 2*np.pi, count, endpoint=False)):
            center = np.array([-np.sin(angle),0,np.cos(angle)])*5
            forward = -center/5
            right = np.cross(forward,[0,1,0])
            down = np.cross(forward,right)
            R = np.vstack([right,down,forward])
            rows.append(dict(photo_id=f'p{i}', center=center, R=R, observations=100+i))
        return rows

    def test_first_photo_is_front_even_if_input_order_is_shuffled(self):
        points = np.random.default_rng(4).normal(0,.03,(300,3))
        rows = self.ring()
        np.random.default_rng(3).shuffle(rows)
        result = choose_directions(rows,points,18,'p0')
        self.assertEqual(result['views']['front'],'p0')
        self.assertEqual(result['views']['back'],'p9')
        self.assertIn(result['views']['left'],['p4','p5'])
        self.assertIn(result['views']['right'],['p13','p14'])

    def test_missing_front_or_insufficient_coverage_requires_confirmation(self):
        points = np.zeros((200,3))
        with self.assertRaises(ValueError):
            choose_directions(self.ring()[1:],points,18,'p0')
        with self.assertRaises(ValueError):
            choose_directions(self.ring()[:9],points,12,'p0')

    def test_manual_labels_reject_side_back_swap_without_overwriting(self):
        result=choose_directions(self.ring(),np.zeros((200,3)),18,'p0')
        selected=dict(result['views'])
        self.assertTrue(all(e<=25 for e in validate_manual_directions(selected,result).values()))
        selected['back'],selected['right']=selected['right'],selected['back']
        unchanged=dict(selected)
        with self.assertRaisesRegex(ValueError,'手选四方向与相机配准不符'):
            validate_manual_directions(selected,result)
        self.assertEqual(selected,unchanged)


class AutomaticImportTests(unittest.TestCase):
    def test_only_complete_capture_is_imported_and_restart_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/'receiver/captures/session_test/capture_001'
            folder.mkdir(parents=True)
            manifest = folder/'manifest.json'
            save_json(manifest,dict(capture=dict(state='capturing')))
            jobs = MagicMock(active=None)
            jobs.list.return_value=[]
            jobs.new_import.return_value='job_a'
            importer=CaptureImporter({'data_dir':tmp},None,jobs)
            importer.tick()
            jobs.new_import.assert_not_called()
            save_json(manifest,dict(capture=dict(state='complete',end_event_received=True)))
            importer.tick();importer.tick()
            jobs.new_import.assert_called_once()
            CaptureImporter({'data_dir':tmp},None,jobs).tick()
            jobs.new_import.assert_called_once()

    def test_snapshot_without_capture_manifest_never_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'receiver/captures/session_test/snapshots'
            root.mkdir(parents=True)
            save_json(root/'manifest.json',dict(capture=dict(state='complete',end_event_received=True)))
            jobs=MagicMock(active=None)
            CaptureImporter({'data_dir':tmp},None,jobs).tick()
            jobs.new_import.assert_not_called()


class DualReceiverTests(unittest.TestCase):
    def test_late_dual_plan_recovers_camera_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(receiver,'ROOT',Path(tmp)), contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'captures','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,100,0),host)
            image=np.random.default_rng(8).integers(0,255,(96,128,3),dtype=np.uint8)
            for seq,cam in enumerate(['front','upper']):
                r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':seq,'position_index':0,'camera_id':cam}).encode(),host)
                r.consume(QUALITY_EXT.pack(b'FPGQ',seq,1,100,0,12288,10000,0,0,0,96,12288,55,0),host)
                _,parts=packets(image,seq)
                for part in parts:r.consume(part,host)
            r.consume(struct.pack('<4sIII',b'FPGD',1,100,2),host)
            self.assertEqual(r.round['manifest']['capture']['state'],'incomplete')
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':1,'camera_ids':['front','upper']}}).encode(),host)
            self.assertEqual(r.round['manifest']['capture']['state'],'complete')
            self.assertEqual(r.round['manifest']['frames'][1]['camera_role'],'upper')

    def test_malformed_dual_metadata_is_rejected_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(receiver,'ROOT',Path(tmp)), contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'captures','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,100,0),host)
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':18,'camera_ids':[{},[]]}}).encode(),host)
            r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':0,'position_index':[],'camera_id':'upper'}).encode(),host)
            self.assertEqual(r.assembler.bad_packets,2)
            self.assertEqual(r.round['manifest']['capture']['state'],'capturing')

    def test_dual18_complete_requires_each_camera_and_position(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(receiver,'ROOT',Path(tmp)), contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'captures','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,100,0),host)
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':18,'camera_ids':['front','upper']}}).encode(),host)
            image=np.random.default_rng(8).integers(0,255,(96,128,3),dtype=np.uint8)
            for seq in range(36):
                cam=['front','upper'][seq%2]
                r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':seq,'position_index':seq//2,'camera_id':cam}).encode(),host)
                r.consume(QUALITY_EXT.pack(b'FPGQ',seq,1,100,0,12288,10000,0,0,0,96,12288,55,0),host)
                if seq==35:
                    r.consume(struct.pack('<4sIII',b'FPGD',1,100,36),host)
                    self.assertEqual(r.round['manifest']['capture']['state'],'incomplete')
                _,parts=packets(image,seq)
                for part in parts:r.consume(part,host)
            m=r.round['manifest']
            self.assertEqual(m['capture']['state'],'complete')
            self.assertEqual(m['capture']['camera_counts'],dict(front=18,upper=18))
            self.assertEqual(m['frames'][1]['camera_role'],'upper')
            validate_capture(r.round['path'],m)
            m['frames'][-1]['camera_id']='front'
            with self.assertRaises(ValueError):validate_capture(r.round['path'],m)


class OneClickTests(unittest.TestCase):
    def test_progress_survives_tqdm_prefix(self):
        self.assertEqual(JobManager.inference_step('Diffusion Sampling:: 38%| 3/8 [00:03]DIFFUSION_STEP 4'),4)
        self.assertEqual(JobManager.inference_step('DIFFUSION_STEP 8'),8)
        self.assertIsNone(JobManager.inference_step('Loading model'))

    def make_job(self,tmp):
        root=Path(tmp);source=root/'input';source.mkdir()
        for i,d in enumerate(DIRECTIONS):Image.new('RGBA',(80,80),(i*40,90,200,255)).save(source/(d+'.png'))
        m=JobManager({'data_dir':str(root/'data')})
        id_='job_20261102_000000_abcdef12';p=m.root/id_;p.mkdir()
        save_json(p/'state.json',dict(id=id_,status='photos_ready',created=time.time()))
        report=import_folder(source,p,lambda *x:None,lambda:False)
        return m,id_,{d:r['id'] for d,r in zip(DIRECTIONS,report['photos'])}

    def wait(self,m):
        for _ in range(250):
            if not m.active:return
            time.sleep(.02)
        self.fail('worker did not stop')

    def test_one_action_segments_then_generates_without_second_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,views=self.make_job(tmp);calls=[]
            def finish(id_,reviewed):
                calls.append('generate');m.update(id_,status='complete')
            with patch.object(m,'direction_action',side_effect=lambda *a,**k:calls.append('register') or True),patch.object(m,'segment_action',side_effect=lambda i:calls.append('segment')),patch.object(m,'generate_action',side_effect=finish):
                m.pipeline(id_,views,{},'manual');self.wait(m)
            self.assertEqual(calls,['segment','generate'])
            self.assertEqual(m.state(id_)['status'],'complete')

    def test_direction_failure_never_runs_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,views=self.make_job(tmp)
            with patch.object(m,'generate_action') as gpu:
                m.pipeline(id_,mode='auto');self.wait(m)
                gpu.assert_not_called()
            self.assertEqual(m.state(id_)['status'],'needs_directions')

    def test_auto_honors_explicit_front_and_its_capture_local_box(self):
        for correct in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                m,id_,views=self.make_job(tmp)
                first=primary_sequence(read_json(m.path(id_)/'photos.json')['photos'])[0]['id']
                chosen=first if correct else next(v for v in views.values() if v!=first)
                box=[.2,.1,.8,.8]
                with patch.object(m,'direction_action',return_value=False) as direction:
                    m.pipeline(id_,{'front':chosen},{'front':box},'calibrated');self.wait(m)
                    direction.assert_called_once_with(id_,box,reference_photo=chosen)

    def test_segmentation_failure_and_cancel_never_run_gpu(self):
        for exception in (ValueError('empty foreground'),InterruptedError('cancelled')):
            with tempfile.TemporaryDirectory() as tmp:
                m,id_,views=self.make_job(tmp)
                with patch.object(m,'direction_action',return_value=True),patch.object(m,'segment_action',side_effect=exception),patch.object(m,'generate_action') as gpu:
                    m.pipeline(id_,views,{},'manual');self.wait(m);gpu.assert_not_called()
                self.assertIn(m.state(id_)['status'],('failed','cancelled'))

    def test_manual_gray_does_not_require_registration_and_preserves_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,views=self.make_job(tmp)
            with patch.object(m,'direction_action',return_value=False) as registration,patch.object(m,'segment_action'),patch.object(m,'generate_action') as gpu:
                m.pipeline(id_,views,{},'manual');self.wait(m)
                registration.assert_not_called()
                gpu.assert_called_once()
            self.assertEqual(read_json(m.path(id_)/'selection.json')['views'],views)

    def test_gray_only_pipeline_passes_flag_without_requesting_color(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,views=self.make_job(tmp)
            with patch.object(m,'direction_action',return_value=True),patch.object(m,'segment_action'),patch.object(m,'generate_action') as generate:
                m.pipeline(id_,views,{},'manual',with_texture=False);self.wait(m)
                generate.assert_called_once_with(id_,reviewed=False,with_texture=False,prepare_color=True)

    def test_missing_foreground_still_blocks_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,views=self.make_job(tmp)
            save_json(m.path(id_)/'selection.json',dict(views=views))
            with patch.object(m,'direction_action') as registration,patch.object(m,'process') as process:
                with self.assertRaisesRegex(ValueError,'前景尚未准备'):m.generate_action(id_,True)
                process.assert_not_called()
                registration.assert_not_called()

    def test_subject_roi_propagation_and_explicit_exclusion_preserve_white_pixels(self):
        from studio.processing import subject_box,exclude_background
        rows=[dict(id='a',camera='main'),dict(id='b',camera='main'),dict(id='c',camera='upper')]
        box=[.2,.1,.8,.8];selection=dict(views=dict(front='a'),boxes=dict(front=box))
        self.assertEqual(subject_box(selection,rows[1],rows),box)
        self.assertIsNone(subject_box(selection,rows[2],rows))
        image=np.full((80,80,4),255,np.uint8)
        result=exclude_background(image,[[0,.8,1,1]])
        self.assertTrue((result[:64]==255).all())
        self.assertFalse(result[64:,:,3].any())
        self.assertTrue((image==255).all())

    def test_segment_keeps_capture_roi_only_for_same_reference(self):
        for same in (True,False):
            with tempfile.TemporaryDirectory() as tmp:
                m,id_,views=self.make_job(tmp)
                old=dict(views);old['front']=views['front'] if same else views['left']
                box=[.2,.1,.8,.8]
                save_json(m.path(id_)/'selection.json',dict(views=old,reference_box=box))
                with patch.object(m,'segment_action'):
                    m.segment(id_,views,{});self.wait(m)
                self.assertEqual(read_json(m.path(id_)/'selection.json')['reference_box'],box if same else None)

    def test_exclusion_changes_are_capture_local_and_invalidate_old_masks(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,id_,views=self.make_job(tmp);photo=views['front'];box=[0,.8,1,1]
            m.update(id_,can_generate=True)
            m.exclude_region(id_,photo,box)
            self.assertFalse(m.state(id_)['can_generate'])
            self.assertEqual(read_json(m.path(id_)/'background_regions.json'),{photo:[box]})
            with self.assertRaises(ValueError):m.exclude_region(id_,'other_capture',box)
            m.exclude_region(id_,photo,None)
            self.assertEqual(read_json(m.path(id_)/'background_regions.json'),{})

    def test_stage_preview_mask_matches_processed_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=Image.new('RGBA',(80,80),'white');result=original.copy()
            alpha=Image.new('L',(80,80),0);alpha.paste(255,(20,20,60,60));result.putalpha(alpha)
            write_stages(tmp,original,result,{'method':'test'}, {'id':'p0'})
            records=read_json(Path(tmp)/'processing/stages.json')['stages']
            self.assertEqual(len(records),3)
            self.assertEqual(Image.open(Path(tmp)/'processing/mask.png').tobytes(),alpha.tobytes())


if __name__=='__main__':unittest.main()
