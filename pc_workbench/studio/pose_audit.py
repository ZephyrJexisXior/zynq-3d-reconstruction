"""Per-view evidence for photo projection, separate from whole-ring coverage."""
from pathlib import Path
import numpy as np


def audit_sparse(directory, candidates, angles):
    from .texture_geometry import read_cameras,project
    directory=Path(directory);views,_=read_cameras(directory)
    by_id={v['id']:v for v in views};xyz={}
    for line in (directory/'points3D.txt').read_text().splitlines():
        if line and not line.startswith('#'):
            row=line.split();xyz[int(row[0])]=list(map(float,row[1:4]))
    lines=[s for s in (directory/'images.txt').read_text().splitlines() if not s.startswith('#')]
    measured={}
    for k in range(0,len(lines),2):
        if not lines[k].strip():continue
        row=lines[k].split();id_=Path(row[9]).stem;observed=lines[k+1].split();points=[];pixels=[]
        for j in range(0,len(observed),3):
            pid=int(observed[j+2])
            if pid in xyz:points.append(xyz[pid]);pixels.append(list(map(float,observed[j:j+2])))
        if not points:continue
        uv,z=project(points,by_id[id_]);e=np.linalg.norm(uv-np.array(pixels),axis=1)
        good=(e<3)&(z>0)
        measured[id_]=dict(observations=len(e),inliers=int(good.sum()),median_px=float(np.median(e)),p90_px=float(np.quantile(e,.9)),
            accepted=bool(good.sum()>=25 and np.median(e)<1.5 and np.quantile(e,.9)<3),points=np.array(pixels)[good].tolist())
    rows=[]
    for id_ in candidates:
        row=dict(id=id_,angle_deg=angles.get(id_),**measured.get(id_,dict(observations=0,inliers=0,accepted=False)))
        row['reason']='通过特征重投影检查' if row['accepted'] else ('未成功配准' if id_ not in by_id else '可靠特征不足或重投影误差过大')
        rows.append(row)
    return dict(method='COLMAP foreground feature reprojection audit',views=rows,attempted_images=len(candidates),
        registered_images=sum(r['accepted'] for r in rows),validation_note='该分支为已拟合特征重投影检查，不是独立相机精度标定。误差按匹配图像像素计。')


def write_match_preview(image_path, report, photo_id, output):
    from PIL import Image,ImageDraw
    im=Image.open(image_path).convert('RGB');draw=ImageDraw.Draw(im)
    row=next((r for r in report['views'] if r['id']==photo_id),{})
    for x,y in row.get('points',[]):draw.ellipse((x-2,y-2,x+2,y+2),fill=(20,230,120))
    draw.rectangle((0,0,im.width,32),fill=(15,25,35))
    draw.text((10,10),photo_id+' : reliable projected feature points (green)',fill='white')
    im.thumbnail((960,960));im.save(output)
