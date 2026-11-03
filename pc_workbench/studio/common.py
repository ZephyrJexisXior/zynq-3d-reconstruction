from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
DIRECTIONS = ('front', 'left', 'back', 'right')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.03)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inside(root, relative):
    root = Path(root).resolve()
    p = (root / relative).resolve()
    if not p.is_relative_to(root) or p == root:
        raise ValueError('文件路径超出当前数据目录')
    return p


def config():
    base = read_json(ROOT / 'config/default.json')
    local = ROOT / 'config/local.json'
    if local.exists():
        base.update(read_json(local))
    for key in ('python', 'ai_python', 'segment_python', 'weights', 'segmentation_model', 'data_dir'):
        base[key] = str((ROOT / base[key]).resolve())
    for key in ('colmap', 'matching_model'):
        if base.get(key):
            base[key] = str((ROOT / base[key]).resolve())
    return base


def subprocess_environment():
    env = os.environ.copy()
    env.update(PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return env
