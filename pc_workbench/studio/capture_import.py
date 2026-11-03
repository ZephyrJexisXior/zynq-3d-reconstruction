"""Queue only completed captures, independently of browser polling; never start the GPU."""
from pathlib import Path
import threading
from .common import read_json, save_json


class CaptureImporter:
    def __init__(self, cfg, receiver, jobs):
        self.receiver, self.jobs = receiver, jobs
        self.root = Path(cfg['data_dir'])/'receiver/captures'
        self.index_path = Path(cfg['data_dir'])/'capture_imports.json'
        self.index = read_json(self.index_path) if self.index_path.exists() else {}
        self.stop_event = threading.Event()
        self.guard = threading.RLock()
        self.status = dict(message='完整收图后自动导入；点击建模才使用 GPU。', latest_job=None, error=None)
        self.known = set()
        self.pending = {}

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                with self.guard:
                    self.status['error'] = str(exc)
            self.stop_event.wait(2)

    def tick(self):
        # Discovery runs infrequently and ignores snapshots and unfinished manifests.
        manifests = sorted(self.root.glob('session_*/capture_*/manifest.json'))
        for manifest in manifests:
            key = str(manifest.parent.resolve())
            if key in self.known:
                continue
            try:
                capture = read_json(manifest).get('capture', {})
            except (OSError, ValueError):
                continue
            if capture.get('state') == 'complete' and capture.get('end_event_received'):
                self.pending[key] = manifest.parent
                self.known.add(key)
        if self.jobs.active:
            return
        for key, path in list(self.pending.items()):
            existing = next((s for s in self.jobs.list() if s.get('capture_key') == key or s.get('source') == key), None)
            if existing:
                id_ = existing['id']
            elif key in self.index:
                id_ = self.index[key]
            else:
                id_ = self.jobs.new_import(path, capture_key=key)
            self.index[key] = id_
            save_json(self.index_path, self.index)
            with self.guard:
                self.status = dict(latest_job=id_, message='完整采集已自动导入，请点击一键建模。', error=None)
            del self.pending[key]
            break

    def snapshot(self):
        with self.guard:
            return dict(self.status)
