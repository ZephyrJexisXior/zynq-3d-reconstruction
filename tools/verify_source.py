"""Validate the public source inventory, frozen motor plan and Python syntax."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'SOURCE_MANIFEST.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private(relative):
    parts = relative.parts
    if any(p in ('.git', '__pycache__', '.venv', 'runtime', 'runtime-ui', 'environment',
                 'data', 'datasets', 'captures', 'captures_front18_top4', 'outputs',
                 'results', 'verification', 'archives', 'node_modules', 'build', '.pytest_cache') for p in parts):
        return True
    text = relative.as_posix()
    return (text.startswith(('fpga/source/ip_repo/rgb2lcd/', 'fpga/source/ip_repo/ov5640_capture_data/'))
            or text == 'pc_workbench/config/local.json'
            or text.startswith('board/vivado/') and relative.suffix.lower() in ('.bit','.hwh','.ltx'))


def source_files():
    return sorted((p for p in ROOT.rglob('*') if p.is_file() and p != MANIFEST
                   and not private(p.relative_to(ROOT))), key=lambda p:p.relative_to(ROOT).as_posix())


def inventory():
    return [{'file':p.relative_to(ROOT).as_posix(), 'bytes':p.stat().st_size,
             'sha256':digest(p)} for p in source_files()]


def verify(check_manifest=True):
    files = source_files()
    errors = []
    syntax_count = 0
    forbidden = {'.img','.exe','.dll','.dcp','.bit','.hwh','.ltx','.wdb','.onnx','.safetensors',
                 '.pt','.pth','.ckpt','.glb','.gltf','.obj','.ply','.stl','.png','.jpg','.jpeg','.log','.zip','.tgz'}
    # Match actual credential formats, not variable names such as server.token.
    credentials = re.compile(r'gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,}|'
                             r'AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')
    personal_path = re.compile(r'[A-Za-z]:[/\\](?:huawei[/\\]Desktop|Users[/\\]Huawei|codex[/\\]\.codex)', re.I)
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > 50*1024*1024 or path.suffix.lower() in forbidden:
            errors.append('Unexpected generated/large artifact: '+rel)
        if path.suffix.lower() == '.dtbo':
            continue
        try:
            value = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            errors.append('Unexpected non-UTF8 text: '+rel)
            continue
        if credentials.search(value):
            errors.append('Credential-shaped material: '+rel)
        if personal_path.search(value):
            errors.append('Machine-specific private path: '+rel)
        if path.suffix == '.py':
            try:
                ast.parse(value, filename=rel)
                syntax_count += 1
            except SyntaxError as exc:
                errors.append('Invalid Python syntax: %s:%s' % (rel, exc.lineno))
    original = json.loads((ROOT/'SOURCE_ORIGINS.json').read_text(encoding='utf-8'))
    frozen = 0
    for name, entry in original['original_files'].items():
        if name.startswith(('board/pynq_app/', 'fpga/source/ip_repo/')) or name == 'board/plan_front18_top4.json':
            if not (ROOT/name).is_file() or digest(ROOT/name) != entry['source_sha256']:
                errors.append('Frozen source changed: '+name)
            frozen += 1
    run = subprocess.run([sys.executable, '-B', str(ROOT/'board/pynq_app/main_front18_top4.py'), '--show-plan'],
                         capture_output=True, text=True, timeout=20)
    try:
        plan = json.loads(run.stdout)
        if run.returncode or not (
            plan['positions'] == 18 and plan['front_photos_requested'] == 18 and
            plan['upper_photos_requested'] == 4 and plan['upper_positions'] == [0,4,9,13] and
            plan['move_durations_s'] == [.58]*18 and plan['signal_pin'] == 'V7' and
            plan['motion_command_us'] == 1612 and plan['stop_command_us'] == 1500 and
            plan['resolution'] == [2592,1944]):
            errors.append('Frozen capture plan mismatch')
    except (ValueError, KeyError):
        errors.append('Frozen entry could not report its plan')
    config = json.loads((ROOT/'pc_workbench/config/default.json').read_text(encoding='utf-8'))
    if config['udp_port'] != 5012:
        errors.append('Workbench UDP port differs from frozen board default')
    if check_manifest:
        if not MANIFEST.is_file():
            errors.append('SOURCE_MANIFEST.json is missing')
        elif json.loads(MANIFEST.read_text(encoding='utf-8')) != inventory():
            errors.append('Source inventory/hash mismatch; inspect changes and regenerate intentionally')
    return {'passed':not errors, 'files':len(files), 'bytes':sum(p.stat().st_size for p in files),
            'python_files_parsed':syntax_count, 'frozen_source_files_checked':frozen,
            'hardware_started':False, 'errors':errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest', action='store_true')
    args = parser.parse_args()
    report = verify(check_manifest=not args.write_manifest)
    if args.write_manifest and report['passed']:
        MANIFEST.write_text(json.dumps(inventory(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
