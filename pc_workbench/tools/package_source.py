"""Allowlisted source ZIP; excludes all photographs, environments, weights and local config."""
from pathlib import Path
import hashlib
import json
import zipfile
ROOT = Path(__file__).resolve().parents[1]


def main():
    folders = ('studio', 'web', 'tools', 'tests', 'licenses', 'vendor', 'config')
    names = ('server.py', 'README.md', '.gitignore', 'requirements.txt', 'requirements-startup.txt', 'Launch.ps1',
             'Launch.vbs', '启动建模.cmd', 'CreateShortcut.ps1', 'Install.ps1', '验证说明.md')
    allowed = {'.py', '.js', '.cjs', '.css', '.html', '.md', '.txt', '.json', '.yaml'}
    files = [ROOT / n for n in names if (ROOT / n).is_file()]
    for name in folders:
        for p in (ROOT / name).rglob('*'):
            if (p.is_file() and p.suffix.lower() in allowed and '__pycache__' not in p.parts
                    and p != ROOT / 'config/local.json'):
                files.append(p)
    manifest = [dict(file=p.relative_to(ROOT).as_posix(), bytes=p.stat().st_size,
                     sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files)]
    target = ROOT.parent / (ROOT.name + '_源码.zip')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            z.write(p, ROOT.name + '/' + p.relative_to(ROOT).as_posix())
        z.writestr(ROOT.name + '/source_manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed')
        for name in z.namelist():
            rel = Path(name).parts[1:]
            if (rel[0] in ('data', 'runtime', 'runtime-ui', 'models', 'verification') or '__pycache__' in rel
                    or name.endswith('config/local.json')):
                raise RuntimeError('Private/large runtime artifact in source package')
    report = dict(zip=str(target), zip_bytes=target.stat().st_size,
                  source_bytes=sum(x['bytes'] for x in manifest), files=len(files),
                  sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    (ROOT / 'verification').mkdir(exist_ok=True)
    (ROOT / 'verification/package_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
