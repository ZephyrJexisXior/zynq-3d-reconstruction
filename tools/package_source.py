"""Make a source-only ZIP from the verified release inventory."""
from pathlib import Path
import argparse
import json
import zipfile
from verify_source import ROOT, MANIFEST, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    if not result['passed']:
        raise RuntimeError('Source verification failed: '+json.dumps(result))
    target = args.output.resolve()
    if target.is_relative_to(ROOT):
        raise ValueError('Write release archives outside the source repository')
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(MANIFEST.read_text(encoding='utf-8'))
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for row in rows:
            archive.write(ROOT/row['file'], ROOT.name+'/'+row['file'])
        archive.write(MANIFEST, ROOT.name+'/'+MANIFEST.name)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip():
            raise RuntimeError('ZIP failed its integrity check')
    print(json.dumps({'file':str(target),'bytes':target.stat().st_size,'files':len(rows)+1}))


if __name__ == '__main__':
    main()
