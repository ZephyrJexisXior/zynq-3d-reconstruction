import copy
from pathlib import Path
import socket
import threading
import time
from vendor.fpga import receiver as protocol_receiver


class CaptureService:
    def __init__(self, cfg):
        self.cfg = cfg
        self.receiver = None
        self.thread = None
        self.sock = None
        self.error = None
        self.last_complete_at = None
        self.started_at = None
        self.guard = threading.RLock()
        self.stopping = threading.Event()

    def start(self):
        with self.guard:
            if self.thread and self.thread.is_alive():
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
                sock.bind(('0.0.0.0', self.cfg['udp_port']))
                sock.settimeout(.25)
                state_dir = Path(self.cfg['data_dir']) / 'receiver'
                state_dir.mkdir(parents=True, exist_ok=True)
                protocol_receiver.ROOT = state_dir
                self.receiver = protocol_receiver.Receiver(state_dir / 'captures', self.cfg['board_ip'])
                self.receiver.status['port'] = self.cfg['udp_port']
            except Exception:
                sock.close()
                raise
            self.sock = sock
            self.error = None
            self.last_complete_at = None
            self.started_at = time.time()
            self.stopping.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        try:
            while not self.stopping.is_set():
                try:
                    packet, source = self.sock.recvfrom(65535)
                except socket.timeout:
                    packet = None
                with self.guard:
                    if packet is not None:
                        before = self.receiver.status['frames']
                        self.receiver.status['packets'] += 1
                        self.receiver.consume(packet, source)
                        if self.receiver.status['frames'] > before:
                            self.last_complete_at = time.time()
                    self.receiver.assembler.expire()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.sock.close()
            with self.guard:
                if self.receiver.round and self.receiver.round['manifest']['capture']['state'] == 'capturing':
                    self.receiver.round['manifest']['capture']['state'] = 'incomplete'
                    self.receiver.round_manifest()
                self.receiver.status['state'] = 'failed' if self.error else 'stopped'
                self.receiver.flush()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)

    def snapshot(self):
        with self.guard:
            result = dict(running=bool(self.thread and self.thread.is_alive()), error=self.error,
                          started_at=self.started_at, last_complete_at=self.last_complete_at)
            if self.receiver:
                result.update(copy.deepcopy(self.receiver.status))
                result['bad_packets'] = self.receiver.assembler.bad_packets
                result['expired_frames'] = self.receiver.assembler.expired_frames
                if self.receiver.round:
                    result['capture'] = copy.deepcopy(self.receiver.round['manifest']['capture'])
                    result['capture_path'] = str(self.receiver.round['path'])
            result['image_link_verified_this_session'] = self.last_complete_at is not None
            if result['running'] and self.last_complete_at is None and time.time() - self.started_at > 15:
                result['warning'] = '尚未收到完整照片：检查板端目标 IP、UDP 端口及防火墙；监听成功不代表板端已传通。'
            return result
