"""Download only documented public model assets; verify exact size and SHA-256."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.common import read_json, sha256


def download(source, destination):
    expected = source.get('size', source.get('bytes'))
    if destination.is_file() and destination.stat().st_size == expected and sha256(destination) == source['sha256']:
        print('VERIFIED', destination.name, flush=True)
        return
    destination.parent.mkdir(exist_ok=True)
    part = destination.with_suffix(destination.suffix + '.partial')
    # Keep interrupted downloads for retry; request a correctly verified range.
    offset = part.stat().st_size if part.exists() else 0
    if offset >= expected:
        if offset == expected and sha256(part) == source['sha256']:
            part.replace(destination)
            return
        offset = 0
    request = urllib.request.Request(source['url'], headers={'Range': f'bytes={offset}-'} if offset else {})
    with urllib.request.urlopen(request, timeout=60) as response:
        if offset and (response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
            raise ValueError('服务器未返回正确续传范围，请保留文件并检查下载源')
        with part.open('ab' if offset else 'wb') as f:
            total = offset
            for block in iter(lambda: response.read(4 * 1024 * 1024), b''):
                total += len(block)
                if total > expected:
                    raise ValueError('下载文件超出已记录大小')
                f.write(block)
                print('DOWNLOAD', destination.name, total, '/', expected, flush=True)
    if part.stat().st_size != expected or sha256(part) != source['sha256']:
        raise ValueError('下载不完整或 SHA256 不匹配：' + destination.name)
    part.replace(destination)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--matching-only', action='store_true', help='Download only the optional learned matching model')
    args = p.parse_args()
    if args.matching_only:
        download(read_json(ROOT/'config/lightglue_source.json'), ROOT/'models/superpoint_lightglue_pipeline.onnx')
    else:
        download(read_json(ROOT / 'vendor/hunyuan3d/weight_source.json'), ROOT / 'models/model.fp16.safetensors')
        download(read_json(ROOT / 'config/isnet_source.json'), ROOT / 'models/isnet-general-use.onnx')
