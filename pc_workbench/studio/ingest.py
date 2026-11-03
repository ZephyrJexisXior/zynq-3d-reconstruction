"""Immutable copies of decoded photos; completeness never inferred from count alone."""
from pathlib import Path
import shutil
from PIL import Image, ImageOps
from .common import inside, read_json, sha256, save_json, DIRECTIONS

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}
Image.MAX_IMAGE_PIXELS = 40_000_000


def primary_sequence(photos):
    reference = next((r for r in photos if r.get('camera_role') == 'primary'), photos[0])
    rows = [r for r in photos if r['camera'] == reference['camera']]
    if all(type(r.get('position_index')) is int for r in rows):
        rows.sort(key=lambda r:r['position_index'])
    elif all(type(r.get('capture_order')) is int for r in rows):
        rows.sort(key=lambda r:r['capture_order'])
    return rows


def default_front(report):
    """FPGA capture convention, not semantic recognition or an angle assumption."""
    rows = primary_sequence(report['photos'])
    return rows[-2] if report.get('capture_verified') and len(rows) >= 2 else rows[0]


def inspect_image(path):
    if path.stat().st_size > 80 * 1024 * 1024:
        raise ValueError('单张图片超过 80 MB：' + path.name)
    with Image.open(path) as im:
        im.load()
        if min(im.size) < 32 or im.width * im.height > 40_000_000:
            raise ValueError('不支持的图片尺寸：' + path.name)
        return list(ImageOps.exif_transpose(im).size)


def validate_capture(root, m):
    """Validate receiver v2, or imported dual-camera capture manifests."""
    frames = m.get('frames', [])
    if not frames:
        raise ValueError('采集清单没有图片')
    if 'capture' in m:
        c = m['capture']
        n = c.get('plan', {}).get('positions')
        cameras = c.get('plan', {}).get('camera_ids', [])
        dual = (isinstance(cameras, list) and len(cameras)==2 and
                all(isinstance(c,str) for c in cameras) and len(set(cameras))==2)
        if cameras and not dual:
            raise ValueError('双摄采集清单中的相机编号格式错误')
        if any(type(r.get('position_index')) is not int or not isinstance(r.get('camera_id',''),str) for r in frames):
            raise ValueError('采集位置或相机编号格式错误')
        if (c.get('state') != 'complete' or not c.get('end_event_received') or
                c.get('abort_event_received') or c.get('board_sent_images') != len(frames) or
                type(n) is not int or n*(2 if dual else 1) != len(frames) or c.get('missing_positions') != []):
            raise ValueError('采集未完整结束或缺帧，不能作为完整采集导入')
        if dual:
            pairs = [(r.get('camera_id'),r.get('position_index')) for r in frames]
            if len(set(pairs)) != len(pairs) or set(pairs) != {(cam,i) for cam in cameras for i in range(n)}:
                raise ValueError('双摄相机编号或位置缺失/重复')
        elif sorted(r.get('position_index', -1) for r in frames) != list(range(n)):
            raise ValueError('采集位置重复或缺失')
    elif m.get('schema') == 'qian-dual-camera-capture/v1':
        n = m.get('positions_requested')
        cameras = m.get('camera_ids', [])
        expected = {(c, i) for c in cameras for i in range(n or 0)}
        actual = [(r.get('camera_id'), r.get('position_index')) for r in frames]
        if (not m.get('capture_complete') or not expected or len(actual) != len(expected)
                or set(actual) != expected or m.get('positions_completed') != n):
            raise ValueError('双摄采集不完整或配对位置重复')
    else:
        raise ValueError('无法识别采集清单格式，请使用独立照片文件夹导入并自行核对完整性')
    for row in frames:
        p = inside(root, row['file'])
        if not p.is_file() or sha256(p) != row.get('sha256'):
            raise ValueError('照片缺失或校验失败：' + row['file'])
        w, h = inspect_image(p)
        q = row.get('fpga_quality', {})
        if (row.get('valid') is not True or q.get('frame_complete') is not True or
                q.get('frame_bad', False) or q.get('rows') != h or q.get('frame_pixels') != w * h):
            raise ValueError('原始帧质量信息不完整：' + row['file'])
    return frames


def import_folder(source, job, emit, cancelled):
    source, job = Path(source).resolve(), Path(job)
    if not source.is_dir():
        raise ValueError('照片文件夹不存在')
    manifest = source / 'manifest.json'
    orientation = {}
    if manifest.exists():
        rows = validate_capture(source, read_json(manifest))
        provenance = {'capture_verified': True, 'manifest_sha256': sha256(manifest)}
    else:
        rows = [{'file': p.relative_to(source).as_posix()} for p in sorted(source.rglob('*'))
                if p.is_file() and p.suffix.lower() in EXTENSIONS]
        provenance = {'capture_verified': False, 'note': '普通照片导入，未证明整轮完整或角度已标定'}
    if not 4 <= len(rows) <= 1000:
        raise ValueError('请选择含 4～1000 张照片的文件夹；四方向 AI 建模需至少四张不同照片')
    if (source / 'views.json').exists():
        orientation = read_json(source / 'views.json').get('views', {})
    photos = []
    seen = set()
    mapping = {}
    for i, row in enumerate(rows):
        if cancelled():
            raise InterruptedError('导入已取消')
        p = inside(source, row['file'])
        dimensions = inspect_image(p)
        digest = sha256(p)
        if digest in seen:
            continue  # Exact duplicate bytes do not add a camera observation.
        seen.add(digest)
        id_ = f'p{i:04d}'
        target = job / 'originals' / (id_ + p.suffix.lower())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        if sha256(target) != digest:
            raise ValueError('复制图片时校验失败')
        with Image.open(target) as im:
            im = ImageOps.exif_transpose(im).convert('RGBA')
            # Retain original alpha for pre-segmented photographs.
            im.thumbnail((360, 280))
            (job / 'thumbs').mkdir(exist_ok=True)
            im.save(job / 'thumbs' / (id_ + '.png'))
        photos.append(dict(id=id_, name=row['file'], file=target.relative_to(job).as_posix(),
                           size=dimensions, sha256=digest, camera=row.get('camera_id', '未标注'),
                           camera_role=row.get('camera_role'), position_index=row.get('position_index'),
                           capture_order=i))
        mapping[row['file']] = id_
        emit('导入与校验照片', (i + 1) / len(rows), f'{i + 1}/{len(rows)} 张')
    if len(photos) < 4:
        raise ValueError('去除完全相同的文件后不足四张不同照片')
    views = {k: mapping[v] for k, v in orientation.items() if k in DIRECTIONS and v in mapping}
    views.setdefault('front', default_front(dict(photos=photos, **provenance))['id'])
    report = dict(photos=photos, views=views, source=str(source), **provenance,
                  duplicate_files_skipped=len(rows) - len(photos), geometry_calibrated=False,
                  camera_counts={cam: sum(r['camera']==cam for r in photos) for cam in sorted({r['camera'] for r in photos})})
    report['front_reference_policy'] = 'input_manifest' if orientation.get('front') else ('primary_penultimate' if provenance['capture_verified'] else 'first')
    save_json(job / 'photos.json', report)
    return report


def validate_views(job, views):
    if set(views) != set(DIRECTIONS) or len(set(views.values())) != 4:
        raise ValueError('请分别选择正面、左侧、背面、右侧四张不同照片')
    photos = {r['id']: r for r in read_json(Path(job) / 'photos.json')['photos']}
    if any(id_ not in photos for id_ in views.values()):
        raise ValueError('所选照片不属于此任务')
    return {k: photos[v] for k, v in views.items()}
