"""Segment four current views and publish one real example per image processing stage."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.common import read_json, save_json
from studio.ingest import validate_views
from studio.processing import Foreground, write_stages, subject_box, exclude_background
from studio.foreground_boundary import FOREGROUND_VERSION


def main(args):
    import numpy as np
    from PIL import Image, ImageOps
    selection = read_json(args.job / 'selection.json')
    photos = read_json(args.job / 'photos.json')['photos']
    selected = validate_views(args.job, selection['views'])
    output = args.job / 'masks'
    output.mkdir(exist_ok=True)
    model = Foreground(args.model)
    # This foreground check needs no SfM and is independent of color calibration.
    if args.exclude_turntable and not (selection.get('boxes',{}).get('front') or selection.get('reference_box')):
        from studio.turntable import support_proposal, consensus_subject_box
        proposals=[]; evidence=[]
        for row in selected.values():
            image=ImageOps.exif_transpose(Image.open(args.job/row['file'])).convert('RGBA')
            rgba,_=model.segment(np.asarray(image), refine=False)
            proposal=support_proposal(rgba[...,3]);proposals.append(proposal)
            evidence.append(dict(id=row['id'],proposal=proposal))
            print('SUBJECT_PREFLIGHT',len(proposals),4,flush=True)
        box=consensus_subject_box(proposals)
        save_json(output/'foreground_preflight.json',dict(samples=evidence,proposed_box=box))
        if box:
            selection['reference_box']=box
            save_json(args.job/'selection.json',selection)
    reports = {}
    exclusions = read_json(args.job/'background_regions.json') if (args.job/'background_regions.json').exists() else {}
    calibration = read_json(args.job/'direction/result.json') if (args.job/'direction/result.json').exists() else {}
    cache_valid = (calibration.get('foreground_version') == FOREGROUND_VERSION and
                   calibration.get('background_regions', {}) == exclusions and
                   calibration.get('selection_signature') == {k:selection.get(k) for k in ('views','boxes','reference_box')})
    print('SEGMENT_LOADING', flush=True)
    for index, (direction, row) in enumerate(selected.items()):
        original = ImageOps.exif_transpose(Image.open(args.job / row['file'])).convert('RGBA')
        cached = args.job / 'direction/masks' / (row['id']+'.png')
        if cache_valid and selection.get('masks_source') == 'automatic' and cached.exists() and not selection.get('boxes'):
            foreground = Image.open(cached).convert('RGBA')
            if foreground.size != original.size:
                raise ValueError('自动分割缓存坐标不一致')
            info = dict(method='本轮多范围识别与原尺寸边缘校正前景', foreground_version=FOREGROUND_VERSION,
                        foreground_pixels=int(np.count_nonzero(np.asarray(foreground)[...,3])))
        else:
            rgba, info = model.segment(np.asarray(original), subject_box(selection, row, photos))
            foreground = Image.fromarray(rgba)
        foreground = Image.fromarray(exclude_background(np.asarray(foreground), exclusions.get(row['id'], [])))
        foreground.save(output / f'{direction}.png')
        reports[direction] = dict(photo_id=row['id'], source_sha256=row['sha256'], **info)
        if direction == 'front':
            write_stages(args.job, original, foreground, info, row)
            print('PREVIEW_READY', flush=True)
        print('SEGMENT_DONE', index+1, flush=True)
    save_json(output / 'report.json', reports)
    selection['foreground_version'] = FOREGROUND_VERSION
    save_json(args.job/'selection.json', selection)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('--job', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--exclude-turntable', action='store_true')
    main(p.parse_args())
