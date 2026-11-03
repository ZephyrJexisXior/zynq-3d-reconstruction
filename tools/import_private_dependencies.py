"""Import locally held FPGA dependencies; never download or redistribute them."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_dependencies(ip_repo=None, overlay_dir=None, check_only=False):
    spec = json.loads((ROOT/'fpga/private_dependencies.json').read_text(encoding='utf-8'))
    pending = []
    groups = []
    if ip_repo:
        groups.append((Path(ip_repo).resolve(), ROOT/'fpga/source/ip_repo', spec['vendor_ip']))
    if overlay_dir:
        groups.append((Path(overlay_dir).resolve(), ROOT/'board/vivado', spec['known_deployed_overlay']))
    if not groups:
        raise ValueError('Specify --ip-repo and/or --overlay-dir.')
    for source_root, destination_root, entries in groups:
        for entry in entries:
            source = (source_root/entry['file']).resolve()
            destination = (destination_root/entry['file']).resolve()
            if not source.is_relative_to(source_root) or not destination.is_relative_to(destination_root.resolve()):
                raise ValueError('Invalid dependency path')
            if source.suffix == '.ltx' and not source.exists():
                continue  # Observation-only debug probes are optional.
            if not source.is_file() or digest(source) != entry['sha256']:
                raise ValueError('Missing or different dependency: '+entry['file'])
            if destination.exists():
                if not destination.is_file() or digest(destination) != entry['sha256']:
                    raise FileExistsError('Existing different local dependency: '+entry['file'])
            else:
                pending.append((source, destination, entry['sha256']))
    # Validate the full set before copying any file; do not replace user files.
    for source, destination, expected in ([] if check_only else pending):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as output, source.open('rb') as original:
            shutil.copyfileobj(original, output)
        if digest(destination) != expected:
            raise RuntimeError('Copy failed verification: '+destination.name)
    return {'copied': 0 if check_only else len(pending), 'validated_files':sum(len(entries) for _,_,entries in groups),
            'check_only':check_only, 'public_distribution': False,
            'note': 'Imported dependencies are ignored by Git and the source packager.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip-repo', type=Path)
    parser.add_argument('--overlay-dir', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(import_dependencies(args.ip_repo, args.overlay_dir, args.check_only), indent=2))


if __name__ == '__main__':
    main()
