"""Estimate directions from this capture's foreground, anchored by its selected front."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.common import read_json, save_json, ROOT, HIDDEN, sha256
from studio.processing import Foreground, save_image_preview, write_stages, subject_box, exclude_background
from studio.foreground_boundary import FOREGROUND_VERSION
from studio.direction_geometry import read_sparse, choose_directions, validate_manual_directions
from studio.ingest import primary_sequence, default_front


def command(exe, work, label, args, timeout=240):
    print('DIRECTION_STAGE', label, flush=True)
    log = work/(label+'.log')
    with log.open('w', encoding='utf-8') as stream:
        try:
            result = subprocess.run([str(exe), *map(str, args)], stdout=stream, stderr=subprocess.STDOUT,
                timeout=timeout, creationflags=HIDDEN)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('自动方向计算超时，可改为手动选择四方向') from exc
    if result.returncode:
        raise ValueError('自动方向计算失败：'+label+'，请查看方向日志或手动选择')


def main(args):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    cv2.setNumThreads(4)
    report = read_json(args.job/'photos.json')
    photos = report['photos']
    rows = primary_sequence(photos)
    first = default_front(report)
    selection = read_json(args.job/'selection.json') if args.preserve_selection else {}
    reference_id = selection.get('views', {}).get('front') or args.reference_photo
    if reference_id:
        first = next((r for r in rows if r['id'] == reference_id), None)
        if first is None:
            raise ValueError('正面参考必须来自本轮主摄照片')
    if len(rows) < 8:
        raise ValueError('自动方向识别需要同一主相机至少 8 张不同照片；可手动选择四方向')
    # Subsampling proposes work only. It does not assign angles to frame indices.
    groups = {cam: [r for r in photos if r['camera']==cam] for cam in sorted({r['camera'] for r in photos})}
    candidates = []
    camera_folders = {}
    for index, (camera, group) in enumerate(groups.items()):
        if all(type(r.get('position_index')) is int for r in group):
            group.sort(key=lambda r:r['position_index'])
        limit = max(12, 48//len(groups))
        indexes = np.unique(np.linspace(0, len(group)-1, min(limit, len(group))).round().astype(int))
        candidates.extend(group[i] for i in indexes)
        camera_folders[camera] = f'camera{index}'
    required = set(selection.get('views', {}).values()) | {first['id']}
    candidate_ids = {r['id'] for r in candidates}
    candidates.extend(r for r in photos if r['id'] in required-candidate_ids)
    primary_ids = {r['id'] for r in candidates if r['camera']==first['camera']}
    work = Path(tempfile.gettempdir()) / ('qian_views_'+uuid.uuid4().hex[:12])
    if not str(work).isascii():
        raise ValueError('COLMAP 需要英文临时目录，请在配置中指定英文工作路径')
    work.mkdir()
    for sub in ['sparse'] + [a+'/'+c for c in camera_folders.values() for a in ('images','masks')]:
        (work/sub).mkdir(parents=True, exist_ok=True)
    output = args.job/'direction'
    output.mkdir(exist_ok=True)
    for stale in ('result.json', 'error.json'):
        (output/stale).unlink(missing_ok=True)
    save_json(output/'work.json', dict(path=str(work), candidates=len(candidates)))
    model = Foreground(args.model)
    # Infer a subject ROI from this capture; do not reuse another object's crop.
    effective_box = (selection.get('boxes',{}).get('front') or selection.get('reference_box')) if args.preserve_selection else args.reference_box
    if args.exclude_turntable and not effective_box:
        from studio.turntable import support_proposal, consensus_subject_box
        sample_ids = sorted(set([0,len(rows)//3,2*len(rows)//3,rows.index(first)]))
        proposals=[];evidence=[]
        for ix in sample_ids:
            row=rows[ix]
            image=ImageOps.exif_transpose(Image.open(args.job/row['file'])).convert('RGBA')
            rgba,info=model.segment(np.asarray(image), refine=False)
            proposal=support_proposal(rgba[...,3]);proposals.append(proposal)
            evidence.append(dict(id=row['id'],proposal=proposal))
            print('DIRECTION_PREFLIGHT',len(proposals),len(sample_ids),flush=True)
        effective_box=consensus_subject_box(proposals)
        save_json(output/'foreground_preflight.json',dict(method='multi-view wide-support profile',
            samples=evidence,proposed_box=effective_box,not_semantic_recognition=True))
        if effective_box:
            print('DIRECTION_SUBJECT_ROI',json.dumps(effective_box),flush=True)
            if args.preserve_selection:
                selection['reference_box']=effective_box
            else:
                args.reference_box=effective_box
    exclusions = read_json(args.job/'background_regions.json') if (args.job/'background_regions.json').exists() else {}
    mapping = {}
    masks = output/'masks'
    masks.mkdir(exist_ok=True)
    for i, row in enumerate(candidates):
        image = ImageOps.exif_transpose(Image.open(args.job/row['file'])).convert('RGBA')
        box = subject_box(selection, row, photos) if args.preserve_selection else (args.reference_box if row['camera'] == first['camera'] else None)
        rgba, info = model.segment(np.asarray(image), box)
        rgba = exclude_background(rgba, exclusions.get(row['id'], []))
        if row['id'] == first['id']:
            save_image_preview(image, output/'first_photo.png')
            write_stages(args.job, image, Image.fromarray(rgba), info, row)
            print('DIRECTION_PREVIEW_READY', flush=True)
        mask = rgba[..., 3]
        # Feature-only erosion avoids matching background edges; output masks remain unchanged.
        feature_mask = cv2.erode((mask > 0).astype(np.uint8)*255, np.ones((7, 7), np.uint8))
        scale = min(1, 1280/max(image.size))
        size = tuple(round(d*scale) for d in image.size)
        name = camera_folders[row['camera']]+'/'+row['id']+'.png'
        Image.fromarray(rgba[..., :3]).resize(size).save(work/'images'/name)
        Image.fromarray(feature_mask).resize(size, Image.Resampling.NEAREST).save(work/'masks'/(name+'.png'))
        Image.fromarray(rgba).save(masks/(row['id']+'.png'))
        mapping[name] = row['id']
        print('DIRECTION_MASK', i+1, len(candidates), flush=True)
    exe = args.colmap
    if not exe.is_file():
        raise ValueError('未找到 COLMAP 自动视角组件，请配置或手动选择四方向')
    db = work/'database.db'
    command(exe, work, 'features', ['feature_extractor', '--database_path', db, '--image_path', work/'images',
        '--ImageReader.mask_path', work/'masks', '--ImageReader.single_camera_per_folder', '1',
        '--ImageReader.camera_model', 'SIMPLE_RADIAL', '--SiftExtraction.use_gpu', '0' if args.cpu else '1',
        '--SiftExtraction.max_image_size', '1280', '--SiftExtraction.max_num_features', '8192',
        '--SiftExtraction.num_threads', '6'])
    command(exe, work, 'matching', ['exhaustive_matcher', '--database_path', db,
        '--SiftMatching.use_gpu', '0' if args.cpu else '1', '--SiftMatching.num_threads', '6'])
    diagnostics=[]
    def check_models(sparse, prefix):
        errors = []
        models = []
        for path in sparse.iterdir():
            if not path.is_dir():
                continue
            text = work/(prefix+'text_'+path.name)
            text.mkdir(exist_ok=True)
            command(exe, work, prefix+'convert_'+path.name, ['model_converter', '--input_path', path,
                '--output_path', text, '--output_type', 'TXT'])
            views, points = read_sparse(text, mapping)
            models.append((views, points, text))
        for views, points, text in sorted(models, key=lambda m:len(m[0]), reverse=True):
            main_views = [v for v in views if v['photo_id'] in primary_ids]
            diagnostic=dict(model=text.name,registered_images=len(views),verified_points=len(points),
                registered_ids=[v['photo_id'] for v in views],missing_ids=sorted(primary_ids-{v['photo_id'] for v in views}))
            try:
                if not required.issubset({v['photo_id'] for v in views}):
                    raise ValueError('正面参考或手选四方向未全部配准，请调整选图或主体范围')
                result = choose_directions(main_views, points, len(primary_ids), first['id'])
                if args.preserve_selection:
                    manual_errors = validate_manual_directions(selection['views'], result)
                    result['recommended_views'] = result['views']
                    result['views'] = selection['views']
                    result['manual_angle_errors_deg'] = manual_errors
                result['auxiliary_registered_images'] = len(views)-len(main_views)
                result['verified_feature_points'] = len(points)
                result['sparse_path'] = str(text)
                diagnostic['accepted']=True;diagnostics.append(diagnostic)
                save_json(output/'sfm_audit.json',dict(models=diagnostics))
                return result
            except ValueError as exc:
                diagnostic.update(accepted=False,reason=str(exc));diagnostics.append(diagnostic)
                save_json(output/'sfm_audit.json',dict(models=diagnostics))
                errors.append(str(exc))
        raise ValueError(errors[0] if errors else '主体特征匹配不足，未算出可靠方向，请手动选择四张照片')
    try:
        command(exe, work, 'poses', ['mapper', '--database_path', db, '--image_path', work/'images',
            '--output_path', work/'sparse', '--Mapper.num_threads', '6', '--Mapper.min_model_size', '8',
            '--Mapper.max_num_models', '3', '--Mapper.init_min_num_inliers', '40',
            '--Mapper.init_min_tri_angle', '6', '--Mapper.abs_pose_min_num_inliers', '20',
            '--Mapper.filter_max_reproj_error', '3', '--Mapper.ba_use_gpu', '0' if args.cpu else '1'])
        result = check_models(work/'sparse', '')
        result['matcher'] = 'SIFT'
    except ValueError as initial_error:
        save_json(output/'sift_attempt.json', {'error':str(initial_error)})
        if not args.matching_model or not args.matching_model.is_file():
            raise
        from studio.learned_directions import match_foregrounds
        match_foregrounds(work, list(mapping), args.matching_model, reliable_foreground=args.texture_only)
        command(exe, work, 'learned_verify', ['matches_importer', '--database_path', db,
            '--match_list_path', work/'learned_matches.txt', '--match_type', 'raw',
            '--SiftMatching.use_gpu', '0' if args.cpu else '1', '--SiftMatching.num_threads', '6', '--TwoViewGeometry.min_num_inliers', '15',
            '--TwoViewGeometry.max_error', '3', '--TwoViewGeometry.compute_relative_pose', '1'])
        learned = work/'learned_sparse'
        learned.mkdir()
        command(exe, work, 'learned_poses', ['mapper', '--database_path', db, '--image_path', work/'images',
            '--output_path', learned, '--Mapper.num_threads', '6', '--Mapper.min_model_size', '8',
            '--Mapper.max_num_models', '3', '--Mapper.init_min_num_inliers', '40',
            '--Mapper.init_min_tri_angle', '6', '--Mapper.abs_pose_min_num_inliers', '20',
            '--Mapper.filter_max_reproj_error', '3', '--Mapper.ba_use_gpu', '0' if args.cpu else '1'])
        try:
            result = check_models(learned, 'learned_')
            result['matcher'] = 'SuperPoint + LightGlue with COLMAP geometric verification'
        except ValueError:
            if not (args.texture_only and args.turntable and report.get('capture_verified')):raise
            from studio.turntable_pose import solve_turntable
            if len(groups)!=1:
                raise ValueError('当前转台约束求解仅验证了单主摄；双摄需要分别标定后联合求解')
            names=[next(name for name,pid in mapping.items() if pid==row['id']) for row in rows if row['id'] in primary_ids]
            front_name=next(name for name,pid in mapping.items() if pid==first['id'])
            pose_output=work/'turntable_text'
            try:
                audit=solve_turntable(db,names,front_name,Image.open(work/'images'/front_name).size,
                    pose_output,sign=-1 if args.order_direction=='right' else 1,emit=lambda s:print(s,flush=True))
            finally:
                if (pose_output/'pose_audit.json').exists():shutil.copy2(pose_output/'pose_audit.json',output/'pose_audit.json')
            result=dict(views=selection['views'],source='turntable_constrained_image_fit',semantic_front_identified=False,
                matcher='SuperPoint + LightGlue; image-constrained turntable optimization',sparse_path=str(pose_output),
                registered_images=audit['registered_images'],verified_feature_points=audit['train_tracks'],
                max_azimuth_gap_deg=audit['max_azimuth_gap_deg'],partial_texture=True,pose_audit=audit,
                accepted_photo_ids=[v['id'] for v in audit['views'] if v['accepted']],
                candidates=[dict(id=v['id'],relative_angle_deg=v['angle_deg']) for v in audit['views']],auxiliary_registered_images=0)
    result.update(source_photo_sha256={r['id']: r['sha256'] for r in candidates},
                  first_photo_id=first['id'], work_path=str(work), automatic=True,
                  reference_box=effective_box, reference_box_applies_to='this capture primary camera only',
                  used_for_ai_shape=4, candidate_camera_counts=dict(Counter(r['camera'] for r in candidates)))
    result['reference_note'] = '以本轮选定照片 '+first['id']+' 为正面参考；其余方向由主体特征配准计算。'
    if result.get('partial_texture'):
        result['reference_note']='转台约束求解：仅通过重投影和独立轨迹检查的照片参与上色；不要求四张形状输入全部配准。'
    if args.texture_only and not result.get('pose_audit'):
        from studio.pose_audit import audit_sparse,write_match_preview
        audit=audit_sparse(result['sparse_path'],[r['id'] for r in candidates],
                           {v['id']:v['relative_angle_deg'] for v in result['candidates']})
        audit.update(max_azimuth_gap_deg=result['max_azimuth_gap_deg'],full_ring=result['max_azimuth_gap_deg']<=100)
        front_name=next(name for name,pid in mapping.items() if pid==first['id'])
        write_match_preview(work/'images'/front_name,audit,first['id'],output/'matching_preview.png')
        # Keep point lists in the downloadable audit; use compact rows in live state.
        save_json(output/'pose_audit.json',audit)
        result['pose_audit']={**audit,'views':[{k:v for k,v in row.items() if k!='points'} for row in audit['views']]}
        result['accepted_photo_ids']=[v['id'] for v in audit['views'] if v['accepted']]
        if first['id'] not in result['accepted_photo_ids'] or len(result['accepted_photo_ids'])<4:
            raise ValueError('相机模型存在，但可靠照片不足或正面锚点未通过重投影检查，停止照片贴图')
        if len(result['accepted_photo_ids'])<len(candidates):result['partial_texture']=True
    # Preserve calibrated cameras beside the photos rather than depending on Temp.
    (output/'sparse').mkdir(exist_ok=True)
    for name in ('cameras.txt', 'images.txt', 'points3D.txt'):
        shutil.copy2(Path(result['sparse_path'])/name, output/'sparse'/name)
    result['camera_files_sha256'] = {name:sha256(output/'sparse'/name) for name in ('cameras.txt','images.txt','points3D.txt')}
    save_json(output/'result.json', result)
    if args.preserve_selection:
        selection['calibrated'] = True
        save_json(args.job/'selection.json', selection)
    else:
        selection = dict(views=result['views'], boxes={}, reviewed=False,
            reference_box=args.reference_box,
            direction_source=result['source'], first_is_front=first['id']==rows[0]['id'], masks_source='automatic')
        save_json(args.job/'selection.json', selection)
    result['selection_signature'] = {k:selection.get(k) for k in ('views','boxes','reference_box')}
    result['background_regions'] = exclusions
    result['foreground_version'] = FOREGROUND_VERSION
    save_json(output/'result.json', result)
    for log in work.glob('*.log'):
        shutil.copy2(log, output/log.name)
    print('DIRECTIONS_READY', json.dumps(result['views']), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('--job', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--colmap', type=Path, required=True)
    p.add_argument('--matching-model', type=Path)
    p.add_argument('--reference-box', type=json.loads, help='Optional subject ROI for this capture primary camera')
    p.add_argument('--reference-photo', help='Explicit main-camera front reference for this capture')
    p.add_argument('--preserve-selection', action='store_true', help='Register photographs without replacing manual inputs')
    p.add_argument('--exclude-turntable', action='store_true', help='Infer a conservative capture-local subject ROI in a fixed turntable scene')
    p.add_argument('--cpu', action='store_true', help='Keep CUDA available for parallel shape inference')
    p.add_argument('--texture-only',action='store_true',help='Keep shape inputs; allow independently verified partial color views')
    p.add_argument('--turntable',action='store_true',help='Explicit fixed-camera turntable scene')
    p.add_argument('--order-direction',choices=['left','right'],default='left')
    args = p.parse_args()
    try:
        main(args)
    except Exception as exc:
        save_json(args.job/'direction/error.json', dict(error=str(exc), needs_manual_directions=True))
        print('DIRECTION_ERROR', str(exc), flush=True)
        raise

    finally:
        record = args.job/'direction/work.json'
        if record.exists():
            work = Path(read_json(record)['path'])
            for log in [*work.glob('*.log'), *work.glob('learned_matching.json')]:
                shutil.copy2(log, args.job/'direction'/log.name)
