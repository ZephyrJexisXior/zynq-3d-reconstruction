import base64
import contextlib
import io
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image
import numpy as np
from studio.common import inside, save_json, read_json, sha256
from studio.hardware import evaluate_network, detect
from studio.jobs import JobManager
from studio.ingest import validate_capture, import_folder, validate_views
from studio.capture import CaptureService
from vendor.fpga import receiver
from vendor.fpga.protocol import QUALITY_EXT
from test_protocol import packets


class InputTests(unittest.TestCase):
    def test_traversal_and_absolute_path_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for relative in ('../other.jpg', str(Path(tmp).parent / 'secret.jpg')):
                with self.assertRaises(ValueError):
                    inside(tmp, relative)

    def test_import_preserves_bytes_and_rejects_duplicate_camera_selections(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            for i in range(4):
                Image.new('RGB', (64, 64), (i * 50, 100, 220)).save(source / f'{i}.png')
            (source / 'duplicate.png').write_bytes((source / '0.png').read_bytes())
            report = import_folder(source, Path(tmp) / 'job', lambda *a: None, lambda: False)
            self.assertEqual(len(report['photos']), 4)
            self.assertEqual(report['duplicate_files_skipped'], 1)
            for row in report['photos']:
                self.assertEqual(sha256(source / row['name']), row['sha256'])
                self.assertEqual(sha256(Path(tmp) / 'job' / row['file']), row['sha256'])
            with self.assertRaises(ValueError):
                validate_views(Path(tmp) / 'job', {d: 'p0000' for d in ('front', 'left', 'back', 'right')})

    def test_false_complete_flag_cannot_bypass_capture_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = {'capture': {'state': 'complete', 'board_sent_images': 4},
                 'frames': [{'file': 'missing.jpg'}]}
            with self.assertRaises(ValueError):
                validate_capture(Path(tmp), m)


class HardwareTests(unittest.TestCase):
    def test_nic_serial_and_free_port_do_not_pass_when_board_does_not_respond(self):
        info = dict(adapters=[dict(Status='Up', HardwareInterface=True, PhysicalMediaType=14, ifIndex=4)],
                    addresses=[dict(InterfaceIndex=4, IPAddress='192.168.2.1', PrefixLength=24)])
        cfg = dict(board_ip='192.168.2.99', board_target_ip='192.168.2.1', serial_port='COM6',
                   serial_baud=115200, require_serial=True, udp_port=5002)
        network = MagicMock(returncode=0, stdout=json.dumps(info).encode())
        no_board = MagicMock(returncode=1, stdout=b'Request timed out.')
        port = MagicMock(device='COM6', description='USB serial', vid=0x0403)
        recv = MagicMock()
        recv.snapshot.return_value = {'running': False}
        fake_serial = MagicMock()
        fake_serial.__enter__.return_value.in_waiting = 0
        fake_serial.__enter__.return_value.read.return_value = b''
        with patch('studio.hardware.subprocess.run', side_effect=[network, no_board]), \
             patch('serial.tools.list_ports.comports', return_value=[port]), \
             patch('serial.Serial', return_value=fake_serial), patch('studio.hardware.socket.socket'):
            result = detect(cfg, recv)
        self.assertFalse(result['ready'])
        self.assertFalse(next(c['ok'] for c in result['checks'] if c['name'] == '开发板 IP'))

    def test_virtual_and_wifi_up_do_not_pass_ethernet(self):
        adapters = [dict(Status='Up', HardwareInterface=False, PhysicalMediaType=14, ifIndex=4),
                    dict(Status='Up', HardwareInterface=True, PhysicalMediaType=9, ifIndex=8)]
        ips = [dict(InterfaceIndex=8, IPAddress='192.168.2.1', PrefixLength=24)]
        self.assertFalse(evaluate_network(adapters, ips, '192.168.2.99', '192.168.2.1')[0])

    def test_existing_board_destination_must_be_assigned(self):
        a = [dict(Status='Up', HardwareInterface=True, PhysicalMediaType=14, ifIndex=4)]
        ip = [dict(InterfaceIndex=4, IPAddress='192.168.2.100', PrefixLength=24)]
        self.assertFalse(evaluate_network(a, ip, '192.168.2.99', '192.168.2.1')[0])
        ip.append(dict(InterfaceIndex=4, IPAddress='192.168.2.1', PrefixLength=24))
        self.assertTrue(evaluate_network(a, ip, '192.168.2.99', '192.168.2.1')[0])

    def test_loopback_udp_transfers_exact_photo_and_manifest(self):
        # Explicitly local synthetic traffic, never sent to FPGA IP or real UDP5002.
        with tempfile.TemporaryDirectory() as tmp, patch.object(receiver, 'ROOT', Path(tmp)), contextlib.redirect_stdout(io.StringIO()):
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
            probe.close()
            capture = CaptureService({'data_dir': tmp, 'board_ip': '127.0.0.1', 'udp_port': port})
            try:
                capture.start()
                self.assertFalse(capture.snapshot()['image_link_verified_this_session'])
                image = np.random.default_rng(12).integers(0, 255, (96, 128, 3), dtype=np.uint8)
                expected, parts = packets(image, 0)
                messages = [struct.pack('<4sIII', b'FPGK', 1, 100, 0),
                    b'FPGP' + json.dumps({'kind': 'plan', 'plan': {'positions': 1}}).encode(),
                    b'FPGP' + json.dumps({'kind': 'sample', 'sequence': 0, 'position_index': 0}).encode(),
                    QUALITY_EXT.pack(b'FPGQ', 0, 1, 100, 0, 128 * 96, 100000, 0, 0, 0, 96, 128 * 96, 55, 0)]
                sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                for p in messages + parts + [struct.pack('<4sIII', b'FPGD', 1, 100, 1)]:
                    sender.sendto(p, ('127.0.0.1', port))
                    time.sleep(.005)
                sender.close()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    status = capture.snapshot()
                    if status.get('capture', {}).get('state') == 'complete':
                        break
                    time.sleep(.02)
                self.assertTrue(status['image_link_verified_this_session'])
                self.assertEqual(status['capture']['state'], 'complete')
                self.assertEqual(Path(status['last_frame']['path']).read_bytes(), expected)
                path = Path(status['capture_path'])
                validate_capture(path, read_json(path / 'manifest.json'))
            finally:
                capture.stop()


class TaskTests(unittest.TestCase):
    def create(self, tmp):
        m = JobManager({'data_dir': tmp})
        id_ = 'job_20261101_000000_1234abcd'
        path = m.root / id_
        path.mkdir()
        save_json(path / 'state.json', {'id': id_, 'status': 'new', 'created': time.time()})
        return m, id_

    def wait(self, m):
        end = time.monotonic() + 8
        while m.active and time.monotonic() < end:
            time.sleep(.02)
        self.assertIsNone(m.active)

    def test_process_failure_is_failure_not_100_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, id_ = self.create(tmp)
            m.start_task(id_, 'test', lambda: m.process(id_, [sys.executable, '-c', 'raise RuntimeError("bad input")'], lambda line: None))
            self.wait(m)
            state = m.state(id_)
            self.assertEqual(state['status'], 'failed')
            self.assertIsNone(state['progress'])
            self.assertNotIn('result', state)

    def test_cancel_owns_only_current_worker_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, id_ = self.create(tmp)
            original = m.path(id_) / 'photo.jpg'
            original.write_bytes(b'original bytes')
            m.start_task(id_, 'test', lambda: m.process(id_, [sys.executable, '-u', '-c', 'import time;print("working",flush=True);time.sleep(30)'], lambda line: None))
            deadline = time.monotonic() + 4
            while m.proc is None and time.monotonic() < deadline:
                time.sleep(.02)
            with self.assertRaises(ValueError):
                m.start_task(id_, 'second', lambda: None)
            m.cancel()
            self.wait(m)
            self.assertEqual(m.state(id_)['status'], 'cancelled')
            self.assertEqual(original.read_bytes(), b'original bytes')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process-tree containment')
    def test_parser_failure_closes_worker_and_its_child(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handles=[]
        with tempfile.TemporaryDirectory() as tmp:
            m,id_=self.create(tmp)
            script='import subprocess,sys,time;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(45)"]);print(p.pid,flush=True);time.sleep(45)'
            def fail(line):
                handle=kernel.OpenProcess(0x100000,False,int(line))
                self.assertTrue(handle)
                handles.append(handle)
                raise ValueError('parser failed')
            try:
                m.start_task(id_,'test',lambda:m.process(id_,[sys.executable,'-u','-c',script],fail))
                self.wait(m)
                self.assertEqual(m.state(id_)['status'],'failed')
                self.assertIsNone(m.process_tree)
                self.assertEqual(len(handles),1)
                self.assertEqual(kernel.WaitForSingleObject(handles[0],2000),0)
            finally:
                m.cancel()
                for handle in handles:kernel.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
