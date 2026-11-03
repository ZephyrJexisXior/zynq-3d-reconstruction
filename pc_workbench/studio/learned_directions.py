"""Learned foreground correspondences; camera angles still come from verified SfM."""
from collections import defaultdict
from pathlib import Path
import sqlite3
import time
from .common import read_json, sha256, save_json, ROOT


def pairs_for_groups(names):
    # Sequence is only a proposal for overlap, never an angle or pose constraint.
    groups = defaultdict(list)
    for name in sorted(names):
        groups[str(Path(name).parent)].append(name)
    pairs = set()
    for group in groups.values():
        for i, name in enumerate(group):
            for step in range(1, min(4, len(group))):
                pairs.add(tuple(sorted((name, group[(i+step) % len(group)]))))
    groups = list(groups.values())
    for i, first in enumerate(groups):
        for second in groups[i+1:]:
            for j, name in enumerate(first):
                mid = round(j*len(second)/len(first))
                for step in range(-2, 3):
                    pairs.add(tuple(sorted((name, second[(mid+step) % len(second)]))))
    return sorted(p for p in pairs if p[0] != p[1])


def match_foregrounds(work, names, model, reliable_foreground=False):
    import cv2
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    expected = read_json(ROOT/'config/lightglue_source.json')
    if sha256(model) != expected['sha256']:
        raise ValueError('学习型匹配模型校验失败')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 6
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), options, providers=['CPUExecutionProvider'])
    inputs, tables, lookup = {}, {}, {}
    for name in names:
        rgb = np.array(Image.open(work/'images'/name).convert('RGB'))
        mask = np.array(Image.open(work/'masks'/(name+'.png')).convert('L'))
        yy, xx = np.where(mask > 0)
        if not len(xx):
            raise ValueError('前景没有可匹配区域')
        x0, x1 = max(0, int(xx.min())-16), min(rgb.shape[1], int(xx.max())+17)
        y0, y1 = max(0, int(yy.min())-16), min(rgb.shape[0], int(yy.max())+17)
        gray = cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
        if reliable_foreground:
            # Suppress background in the descriptor's receptive field as well as
            # rejecting keypoints there; saved subject masks remain unchanged.
            gray=gray.copy();gray[mask[y0:y1,x0:x1]==0]=128
        scale = 768/max(gray.shape)
        width, height = round(gray.shape[1]*scale), round(gray.shape[0]*scale)
        offset = np.array([(768-width)//2, (768-height)//2])
        canvas = np.full((768, 768), .5, np.float32)
        canvas[offset[1]:offset[1]+height, offset[0]:offset[0]+width] = cv2.resize(gray, (width,height))/255
        inputs[name] = (canvas[None], offset, np.array([width/(x1-x0), height/(y1-y0)]), np.array([x0,y0]), mask)
        tables[name], lookup[name] = [], {}
    def indices(name, points):
        ids = []
        for point in points:
            key = tuple(np.round(point,3))
            if key not in lookup[name]:
                lookup[name][key] = len(tables[name])
                tables[name].append(point)
            ids.append(lookup[name][key])
        return np.asarray(ids, np.int32)
    pairs = pairs_for_groups(names)
    start, evidence = time.monotonic(), []
    with (work/'learned_matches.txt').open('w', encoding='utf-8') as stream:
        for i, (first, second) in enumerate(pairs):
            a, b = inputs[first], inputs[second]
            keypoints, matches, scores = session.run(None, {'images':np.stack([a[0],b[0]])})
            matches = matches[scores > .2]
            aa = (keypoints[0,matches[:,1]]-a[1])/a[2]+a[3]
            bb = (keypoints[1,matches[:,2]]-b[1])/b[2]+b[3]
            keep = np.ones(len(aa), bool)
            for points, mask in ((aa,a[4]), (bb,b[4])):
                xy = np.rint(points).astype(int)
                ok = (xy[:,0]>=0)&(xy[:,0]<mask.shape[1])&(xy[:,1]>=0)&(xy[:,1]<mask.shape[0])
                ids = np.flatnonzero(ok)
                ok[ids] &= mask[xy[ids,1],xy[ids,0]]>0
                keep &= ok
            ia, ib = indices(first, aa[keep]+.5), indices(second, bb[keep]+.5)
            evidence.append(dict(first=first,second=second,matches=len(ia)))
            if len(ia) >= 8:
                stream.write(first+' '+second+'\n')
                np.savetxt(stream, np.c_[ia,ib], fmt='%d')
                stream.write('\n')
            if i % 5 == 0 or i+1 == len(pairs):
                print('DIRECTION_MATCH', i+1, len(pairs), flush=True)
    # This is a task-owned database in a fresh temporary directory.
    with sqlite3.connect(work/'database.db') as db:
        images = db.execute('SELECT image_id,name FROM images').fetchall()
        if set(n for _,n in images) != set(names):
            raise ValueError('匹配数据库和本轮照片不一致')
        for table in ('keypoints','descriptors','matches','two_view_geometries'):
            db.execute('DELETE FROM '+table)
        for iid, name in images:
            points = np.asarray(tables[name],np.float32).reshape(-1,2)
            db.execute('INSERT INTO keypoints(image_id,rows,cols,data) VALUES (?,?,?,?)',
                       (iid,len(points),2,points.tobytes()))
    save_json(work/'learned_matching.json', dict(model_sha256=expected['sha256'], pairs=evidence,
        seconds=round(time.monotonic()-start,2), angles_assumed=False,
        background_context_suppressed=reliable_foreground))
