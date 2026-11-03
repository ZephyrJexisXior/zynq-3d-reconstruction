import binascii
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from vendor.fpga.protocol import HEADER,MAX_CHUNK,Assembler,quality_event,QUALITY_EXT
from vendor.fpga import receiver


def packets(image,sequence=1):
    ok,jpg=cv2.imencode('.jpg',image);assert ok
    data=jpg.tobytes();count=(len(data)+MAX_CHUNK-1)//MAX_CHUNK;result=[]
    h,w=image.shape[:2]
    for i in range(count):
        chunk=data[i*MAX_CHUNK:(i+1)*MAX_CHUNK]
        head=HEADER.pack(b'FPG4',w,h,sequence,len(data),i,count,len(chunk),12,0,0,0,1500,0)
        result.append(head+chunk+struct.pack('<H',binascii.crc_hqx(chunk,0xffff)))
    return data,result


class ProtocolTests(unittest.TestCase):
    def setUp(self):self.image=np.random.default_rng(2).integers(0,256,(120,160,3),dtype=np.uint8)

    def test_jpeg_out_of_order_and_duplicates_preserve_exact_transport_bytes(self):
        expected,parts=packets(self.image);a=Assembler();a.feed(parts[-1]);frame=None
        for packet in reversed(parts):
            current=a.feed(packet)
            if current:frame=current
        self.assertIsNotNone(frame);self.assertEqual(frame.payload,expected)
        self.assertEqual(frame.image.shape,self.image.shape);self.assertEqual(a.bad_packets,0)

    def test_crc_error_never_completes_frame(self):
        _,parts=packets(self.image);a=Assembler();bad=bytearray(parts[0]);bad[-1]^=1
        a.feed(bytes(bad))
        for part in parts[1:]:self.assertIsNone(a.feed(part))
        self.assertEqual(a.bad_packets,1)

    def test_timeout_is_counted_and_bounded(self):
        _,parts=packets(self.image);a=Assembler(timeout=1,max_pending=1)
        a.feed(parts[0],now=0);a.expire(2)
        self.assertEqual(a.expired_frames,1);self.assertFalse(a.pending)

    def test_shape_mismatch_is_rejected(self):
        _,parts=packets(self.image);modified=[]
        for part in parts:
            fields=list(HEADER.unpack_from(part));fields[1]+=1
            modified.append(HEADER.pack(*fields)+part[HEADER.size:])
        a=Assembler()
        for part in modified:self.assertIsNone(a.feed(part))
        self.assertEqual(a.bad_packets,1)

    def test_quality_roi_population_is_not_replaced_with_full_frame(self):
        packet=QUALITY_EXT.pack(b'FPGQ',3,15,908,0,76850,4752394,0,0,117520,1944,5038848,55,0)
        seq,q=quality_event(packet)
        self.assertEqual(seq,3);self.assertEqual(q['pixels'],76850)
        self.assertEqual(q['frame_pixels'],5038848);self.assertTrue(q['frame_complete'])

    def test_round_remains_incomplete_on_missing_photo_and_late_frame_completes(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99')
            r.consume(struct.pack('<4sIII',b'FPGK',8,1000,12),('192.168.2.99',6000))
            r.consume(struct.pack('<4sIII',b'FPGK',8,1000,12),('192.168.2.99',6000))
            self.assertEqual(r.round_serial,1)
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':2}}).encode(),('192.168.2.99',6000))
            for seq in (0,1):
                q=QUALITY_EXT.pack(b'FPGQ',seq,15,900,0,19200,100000,0,0,100,120,19200,55,0)
                r.consume(q,('192.168.2.99',6000))
                r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':seq,'position_index':seq}).encode(),('192.168.2.99',6000))
                if seq==1:
                    r.consume(struct.pack('<4sIII',b'FPGD',8,1000,2),('192.168.2.99',6000))
                    self.assertEqual(r.round['manifest']['capture']['state'],'incomplete')
                _,parts=packets(self.image,seq)
                for part in parts:r.consume(part,('192.168.2.99',6000))
            self.assertEqual(r.round['manifest']['capture']['state'],'complete')
            self.assertEqual(len(r.round['manifest']['frames']),2)

    def test_foreign_host_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');_,parts=packets(self.image)
            for part in parts:r.consume(part,('192.168.2.123',6000))
            self.assertEqual(r.status['frames'],0)

    def test_late_quality_and_sample_finish_round_then_manifest_is_frozen(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',4,1000,12),host)
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':1}}).encode(),host)
            _,parts=packets(self.image,0)
            for part in parts:r.consume(part,host)
            r.consume(struct.pack('<4sIII',b'FPGD',4,1000,1),host)
            self.assertEqual(r.round['manifest']['capture']['state'],'incomplete')
            q=QUALITY_EXT.pack(b'FPGQ',0,15,900,0,19200,100000,0,0,100,120,19200,55,0)
            r.consume(q,host)
            r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':0,'position_index':0}).encode(),host)
            self.assertEqual(r.round['manifest']['capture']['state'],'complete')
            manifest=r.round['path']/'manifest.json';original=manifest.read_bytes()
            r.consume(QUALITY_EXT.pack(b'FPGQ',0,15,1,0,19200,1,0,0,100,120,19200,55,0),host)
            r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':0,'position_index':99}).encode(),host)
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':99}}).encode(),host)
            self.assertEqual(manifest.read_bytes(),original)

    def test_restarted_sender_can_reuse_trigger_and_photo_sequence_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');host=('192.168.2.99',6000)
            start=struct.pack('<4sIII',b'FPGK',1,1000,12)
            r.consume(start,host)
            _,first=packets(self.image,0)
            for part in first:r.consume(part,host)
            old=r.round['path'];old_bytes=(old/'images/frame_0000000000.jpg').read_bytes()
            old_manifest=(old/'manifest.json').read_bytes()
            r.consume(struct.pack('<4sIII',b'FPGD',1,1000,1),host)
            closed=(old/'manifest.json').read_bytes()
            r.consume(b'FPGS',host);r.consume(start,host);r.consume(start,host)
            new=r.round['path'];self.assertNotEqual(new,old)
            _,second=packets(255-self.image,0)
            for part in second:r.consume(part,host)
            self.assertEqual(r.status['frames'],2);self.assertEqual(r.assembler.bad_packets,0)
            self.assertEqual((old/'images/frame_0000000000.jpg').read_bytes(),old_bytes)
            self.assertEqual((old/'manifest.json').read_bytes(),closed)
            self.assertNotEqual((new/'images/frame_0000000000.jpg').read_bytes(),old_bytes)

    def test_new_start_frame_distinguishes_reused_trigger_without_handshake(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,1000,12),host);old=r.round['path']
            r.consume(struct.pack('<4sIII',b'FPGK',1,1000,40),host)
            self.assertNotEqual(old,r.round['path'])
            self.assertEqual(json.loads((old/'manifest.json').read_text())['capture']['state'],'incomplete')

    def test_sender_restart_marks_interrupted_capture_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,1000,12),host);old=r.round['path']
            r.consume(b'FPGS',host)
            self.assertIsNone(r.round)
            capture=json.loads((old/'manifest.json').read_text())['capture']
            self.assertEqual(capture['state'],'incomplete');self.assertTrue(capture['sender_restarted_before_end'])

    def test_locked_manifest_does_not_stop_photo_reception_and_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,1000,12),host)
            real_save=receiver.save_json
            def locked(path,value):
                if path.name=='manifest.json':raise PermissionError('simulated Windows reader lock')
                return real_save(path,value)
            with patch.object(receiver,'save_json',side_effect=locked):
                for seq in (0,1):
                    _,parts=packets(self.image,seq)
                    for part in parts:r.consume(part,host)
                self.assertEqual(r.status['frames'],2)
                self.assertEqual(len(r.pending_json),1)
                self.assertEqual(len(r.round['manifest']['frames']),2)
            r.flush()
            self.assertFalse(r.pending_json)
            saved=json.loads((r.round['path']/'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(saved['frames']),2)

    def test_atomic_json_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver.time,'sleep'):
            target=Path(tmp)/'manifest.json';original=Path.replace;attempts=[]
            def replace(path,destination):
                attempts.append(path)
                if len(attempts)<3:raise PermissionError('reader lock')
                return original(path,destination)
            with patch.object(Path,'replace',replace):receiver.save_json(target,{'frames':2})
            self.assertEqual(len(attempts),3)
            self.assertEqual(json.loads(target.read_text()),{'frames':2})

    def test_locked_disk_preview_does_not_lose_saved_photo_or_web_preview(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');original=Path.replace
            def replace(path,target):
                if Path(target).name=='latest.jpg':raise PermissionError('preview open in viewer')
                return original(path,target)
            with patch.object(Path,'replace',replace):
                _,parts=packets(self.image,0)
                for part in parts:r.consume(part,('192.168.2.99',6000))
            self.assertEqual(r.status['frames'],1);self.assertIsNotNone(r.last_jpeg)
            self.assertTrue(Path(r.status['last_frame']['path']).exists())

    def test_full_162_photo_round_survives_manifest_lock_in_final_frames(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(receiver,'ROOT',Path(tmp)),contextlib.redirect_stdout(io.StringIO()):
            r=receiver.Receiver(Path(tmp)/'datasets','192.168.2.99');host=('192.168.2.99',6000)
            r.consume(struct.pack('<4sIII',b'FPGK',1,1000,12),host)
            r.consume(b'FPGP'+json.dumps({'kind':'plan','plan':{'positions':162}}).encode(),host)
            original=receiver.save_json;blocked=False
            def save(path,value):
                if blocked and path.name=='manifest.json':raise PermissionError('end-of-round reader lock')
                return original(path,value)
            with patch.object(receiver,'save_json',side_effect=save):
                for seq in range(162):
                    blocked=seq>=150
                    r.consume(QUALITY_EXT.pack(b'FPGQ',seq,15,900,0,19200,100000,0,0,100,120,19200,55,0),host)
                    r.consume(b'FPGP'+json.dumps({'kind':'sample','sequence':seq,'position_index':seq}).encode(),host)
                    _,parts=packets(self.image,seq)
                    for part in parts:r.consume(part,host)
                r.consume(struct.pack('<4sIII',b'FPGD',1,1000,162),host)
                self.assertEqual(r.status['frames'],162)
                self.assertEqual(r.round['manifest']['capture']['state'],'complete')
                self.assertTrue(r.pending_json)
            r.flush();self.assertFalse(r.pending_json)
            manifest=json.loads((r.round['path']/'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['capture']['state'],'complete')
            self.assertEqual(manifest['capture']['missing_positions'],[])
            self.assertEqual(len(list((r.round['path']/'images').glob('*.jpg'))),162)


if __name__=='__main__':unittest.main()
