"""Select relative views from verified SfM; never convert filename order to angles."""
import math
import numpy as np


def validate_manual_directions(selection, calibrated):
    """Check labels without silently replacing the user's choices."""
    angles = {v['id']:v['relative_angle_deg'] for v in calibrated['candidates']}
    errors = {}
    for label, target in [('front',0),('left',90),('back',180),('right',270)]:
        pid = selection[label]
        if pid not in angles:
            raise ValueError('手选四方向必须全部来自已配准的主摄照片')
        errors[label] = abs((angles[pid]-target+180)%360-180)
    if any(e > 30 for e in errors.values()):
        import json
        raise ValueError('手选四方向与相机配准不符；请保留正面参考，改用自动计算其余方向。建议：'+json.dumps(calibrated['views']))
    return errors


def choose_directions(views, points, attempted_count, front_id):
    if len(views) < max(8, math.ceil(attempted_count*.6)) or len(points) < 100:
        raise ValueError('有效配准照片或三维特征不足，不能可靠判断四个方向')
    centers = np.asarray([v['center'] for v in views])
    target = np.median(np.asarray(points), axis=0)
    up_vectors = np.asarray([-v['R'][1] for v in views])
    up = np.median(up_vectors, axis=0)
    up /= max(np.linalg.norm(up), 1e-8)
    if np.median(up_vectors @ up) < .85:
        raise ValueError('照片相机竖直方向不一致，自动视角需要人工确认')
    directions = centers-target
    radii = np.linalg.norm(directions, axis=1)
    unit = directions/np.maximum(radii[:, None], 1e-9)
    facing = np.array([np.dot(v['R'][2], -unit[i]) for i, v in enumerate(views)])
    if np.median(facing) < .85 or np.quantile(radii, .9)/max(np.quantile(radii, .1), 1e-9) > 2.5:
        raise ValueError('相机分布与围绕主体拍摄不符，可能匹配到了背景')
    planar = directions - (directions @ up)[:, None]*up
    planar /= np.maximum(np.linalg.norm(planar, axis=1, keepdims=True), 1e-9)
    anchors = [i for i, v in enumerate(views) if v['photo_id'] == front_id]
    if not anchors:
        raise ValueError('作为正面的第一张未成功配准，请手动选择四方向')
    anchor = anchors[0]
    basis = planar[anchor]
    # Left in the reference camera image: dot product sign is derived from pose, not order.
    left = -views[anchor]['R'][0]
    left -= np.dot(left, up)*up
    left -= np.dot(left, basis)*basis
    left /= max(np.linalg.norm(left), 1e-9)
    if np.linalg.norm(left) < .9:
        raise ValueError('无法确定参考视角左右关系')
    angles = np.degrees(np.arctan2(planar @ left, planar @ basis)) % 360
    sorted_angles = np.sort(angles)
    max_gap = float(np.max(np.diff(np.r_[sorted_angles, sorted_angles[0]+360])))
    if max_gap > 100:
        raise ValueError(f'配准所得视角存在 {max_gap:.1f}° 缺口，未能可靠确定背面。若原片已有一圈，请检查转台或背景是否混入前景，再重试；这不等于实际拍摄不足一圈。')
    result, errors = {}, {}
    for label, expected in [('front', 0), ('left', 90), ('back', 180), ('right', 270)]:
        diff = np.abs((angles-expected+180)%360-180)
        index = int(np.argmin(diff))
        if diff[index] > 25:
            raise ValueError('缺少接近 '+label+' 的可靠照片')
        result[label] = views[index]['photo_id']
        errors[label] = round(float(diff[index]), 2)
    if len(set(result.values())) != 4:
        raise ValueError('自动选择了重复视角')
    return dict(views=result, source='foreground_sfm_first_is_front', semantic_front_identified=False,
                reference_note='用户约定本轮第一张为正面；其余方向由本轮前景特征配准计算。',
                angle_errors_deg=errors, registered_images=len(views), max_azimuth_gap_deg=max_gap,
                candidates=[dict(id=v['photo_id'], relative_angle_deg=round(float(a), 2))
                            for v, a in zip(views, angles)])


def read_sparse(directory, mapping):
    from pathlib import Path
    from scipy.spatial.transform import Rotation
    directory = Path(directory)
    lines = [s for s in (directory/'images.txt').read_text(encoding='utf-8').splitlines()
             if not s.startswith('#')]
    views = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        row = lines[i].split()
        q = np.array(list(map(float, row[1:5])))
        R = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
        t = np.array(list(map(float, row[5:8])))
        obs = lines[i+1].split()
        observations = sum(int(obs[k]) >= 0 for k in range(2, len(obs), 3))
        views.append(dict(photo_id=mapping[row[9]], center=-R.T@t, R=R, observations=observations))
        i += 2
    points, errors = [], []
    for line in (directory/'points3D.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        row = line.split()
        error = float(row[7])
        if len(row) >= 14 and error <= 2.5:
            points.append(list(map(float, row[1:4])))
            errors.append(error)
    if errors and np.median(errors) > 1.5:
        raise ValueError('特征重投影误差过大')
    return views, points
