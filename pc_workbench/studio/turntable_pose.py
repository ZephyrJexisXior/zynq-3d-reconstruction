"""Image-constrained fixed-camera turntable BA; nominal angles are initialization only."""
from pathlib import Path
import sqlite3
import numpy as np


def tracks_from_database(database):
    db=sqlite3.connect(database)
    names=dict(db.execute('select image_id,name from images'))
    keys={i:np.frombuffer(blob,np.float32).reshape(n,c)[:,:2] for i,n,c,blob in db.execute('select image_id,rows,cols,data from keypoints')}
    parent={};members={};conflicts=0
    def find(node):
        parent.setdefault(node,node)
        if parent[node]!=node:parent[node]=find(parent[node])
        return parent[node]
    edges=list(db.execute('select pair_id,rows,data from two_view_geometries where rows>=15 order by rows desc'))
    for pair,n,blob in edges:
        a,b=divmod(pair,2147483647)
        for ka,kb in np.frombuffer(blob,np.uint32).reshape(n,2):
            u,v=(a,int(ka)),(b,int(kb));ru,rv=find(u),find(v)
            if ru==rv:continue
            ma=members.setdefault(ru,{a:u});mb=members.setdefault(rv,{b:v})
            if any(ma[k]!=mb[k] for k in ma.keys()&mb.keys()):conflicts+=1;continue
            if len(ma)<len(mb):ru,rv,ma,mb=rv,ru,mb,ma
            parent[rv]=ru;ma.update(mb);members.pop(rv,None)
    tracks=[]
    for root,nodes in members.items():
        if find(root)!=root or len(nodes)<3:continue
        track=[(names[i],keys[i][k].astype(float)) for i,k in sorted(nodes.values())]
        # Static image coordinates alone are not a removal rule: retain for robust fitting.
        tracks.append(track)
    db.close()
    return tracks,dict(track_count=len(tracks),conflicting_merges_rejected=conflicts)


def rotations(angles,tilt,roll):
    from scipy.spatial.transform import Rotation
    c,s=np.cos(tilt),np.sin(tilt)
    base=np.array([[1,0,0],[0,-c,s],[0,-s,-c]])
    yaw=Rotation.from_rotvec(np.c_[np.zeros(len(angles)),angles,np.zeros(len(angles))]).as_matrix()
    rz=Rotation.from_rotvec([0,0,roll]).as_matrix()
    return np.einsum('ij,njk->nik',rz@base,yaw)


def triangulate(uv,ids,R,t,f,cx,cy):
    rows=[]
    for xy,i in zip(uv,ids):
        p=np.column_stack([R[i],t]);x,y=(xy-[cx,cy])/f
        rows.extend([x*p[2]-p[0],y*p[2]-p[1]])
    _,_,v=np.linalg.svd(rows);h=v[-1]
    return h[:3]/h[3] if abs(h[3])>1e-8 else np.full(3,np.nan)


def solve_turntable(database, image_names, front_name, size, output, sign=1, emit=lambda x:None):
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix
    from scipy.spatial.transform import Rotation
    from .common import save_json
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    tracks,evidence=tracks_from_database(database)
    ix={n:i for i,n in enumerate(image_names)};n=len(ix);front=ix[front_name]
    tracks=[[(ix[name],uv) for name,uv in track if name in ix] for track in tracks]
    tracks=[t for t in tracks if len(t)>=3]
    loop_tracks=sum(bool({i for i,_ in tr}&{0,1}) and bool({i for i,_ in tr}&{n-2,n-1}) for tr in tracks)
    if len(tracks)<100:raise ValueError('转台求解缺少跨三张照片的可靠特征轨迹')
    width,height=size;cx,cy=width/2,height/2;f0=1.2*width
    angles0=sign*(np.arange(n)-front)*2*np.pi/n
    R0=rotations(angles0,.12,0);t0=np.array([-.06,-.10,1.])
    initial=[];retained=[]
    for track in tracks:
        ids=np.array([i for i,_ in track]);uv=np.array([xy for _,xy in track]);point=triangulate(uv,ids,R0,t0,f0,cx,cy)
        z=(R0[ids]@point+t0)[:,2]
        if np.isfinite(point).all() and np.min(z)>.15 and np.linalg.norm(point)<2:
            initial.append(point);retained.append(track)
    tracks=retained;initial=np.array(initial)
    # Entire tracks held out: their points and observations never optimize cameras.
    val_indices=np.arange(len(tracks))%5==0
    train=[t for i,t in enumerate(tracks) if not val_indices[i]]
    initial_train=initial[~val_indices];validation=[t for i,t in enumerate(tracks) if val_indices[i]]
    if len(train)<80:raise ValueError('转台初始化后有效特征不足')
    obs_i=np.array([i for tr in train for i,_ in tr]);obs_p=np.repeat(np.arange(len(train)),[len(t) for t in train]);uv=np.array([xy for tr in train for _,xy in tr])
    free=[i for i in range(n) if i!=front];nc=5+len(free)
    def unpack(x):
        angles=angles0.copy();angles[free]=x[5:nc]
        return np.exp(x[0]),rotations(angles,x[1],x[2]),np.r_[x[3:5],1.],angles,x[nc:].reshape(-1,3)
    x0=np.r_[np.log(f0),.12,0,-.06,-.10,angles0[free],initial_train.ravel()]
    lower=np.r_[np.log(.45*width),-.5,-.25,-.7,-.7,angles0[free]-1.1,np.full(initial_train.size,-3.)]
    upper=np.r_[np.log(4*width),.7,.25,.7,.7,angles0[free]+1.1,np.full(initial_train.size,3.)]
    sparsity=lil_matrix((len(uv)*2,len(x0)),dtype=int)
    for k,(i,p) in enumerate(zip(obs_i,obs_p)):
        sparsity[2*k:2*k+2,:5]=1
        if i!=front:sparsity[2*k:2*k+2,5+free.index(i)]=1
        sparsity[2*k:2*k+2,nc+3*p:nc+3*p+3]=1
    def residual(x):
        f,R,t,angles,points=unpack(x)
        cam=np.einsum('nij,nj->ni',R[obs_i],points[obs_p])+t
        return (cam[:,:2]/np.maximum(cam[:,2:],.01)*f+[cx,cy]-uv).ravel()
    emit(f'TURNTABLE_FIT {len(train)} {len(validation)}')
    fit=least_squares(residual,x0,bounds=(lower,upper),jac_sparsity=sparsity.tocsr(),loss='soft_l1',f_scale=1.5,
                      max_nfev=120,ftol=1e-5,xtol=1e-5,gtol=1e-5,x_scale='jac')
    f,R,t,angles,points=unpack(fit.x);errors=np.linalg.norm(residual(fit.x).reshape(-1,2),axis=1)
    # Independent held-out tracks: leave one observation out, triangulate using
    # the other views only, then predict the unseen observation. Cameras never
    # fit any observation of these tracks.
    held=[[] for _ in range(n)];validation_points=[]
    for track in validation:
        ids=np.array([i for i,_ in track]);xy=np.array([v for _,v in track]);delta=np.abs(np.angle(np.exp(1j*(angles[ids,None]-angles[ids]))))
        for j,i in enumerate(ids):
            other=np.arange(len(ids))!=j
            baseline=delta[np.ix_(other,other)]
            if np.max(np.minimum(baseline,np.pi-baseline))<.15:continue
            point=triangulate(xy[other],ids[other],R,t,f,cx,cy)
            if not np.isfinite(point).all():continue
            cam=R[i]@point+t
            if cam[2]>.1:
                pred=cam[:2]/cam[2]*f+[cx,cy]
                held[i].append(float(np.linalg.norm(pred-xy[j])))
    per_view=[];accepted=[]
    for i,name in enumerate(image_names):
        e=errors[obs_i==i];h=np.array(held[i]);good=e<3
        row=dict(id=Path(name).stem,observations=len(e),inliers=int(good.sum()),median_px=float(np.median(e)) if len(e) else None,
                 validation_count=len(h),validation_median_px=float(np.median(h)) if len(h) else None,
                 validation_p90_px=float(np.quantile(h,.9)) if len(h) else None,angle_deg=float(np.degrees(angles[i])%360))
        row['accepted']=bool(good.sum()>=25 and len(h)>=5 and np.median(h)<2 and np.quantile(h,.9)<5)
        row['reason']='通过重投影与留出轨迹检查' if row['accepted'] else '特征不足或留出轨迹预测误差过大'
        if row['accepted']:accepted.append(i)
        per_view.append(row)
    allheld=np.array([v for group in held for v in group]);valid=bool(len(accepted)>=4 and front in accepted and len(allheld)>=50)
    sortedangles=np.sort(np.degrees(angles[accepted])%360) if accepted else np.array([0.])
    gap=float(np.max(np.diff(np.r_[sortedangles,sortedangles[0]+360])))
    reverse_steps=int(np.sum(sign*np.diff(angles)<-np.radians(3)))
    valid=valid and reverse_steps==0
    report=dict(method='fixed-axis turntable bundle adjustment with independent per-image angles',**evidence,train_tracks=len(train),validation_tracks=len(validation),
                focal_pixels=float(f),tilt_deg=float(np.degrees(fit.x[1])),roll_deg=float(np.degrees(fit.x[2])),
                median_reprojection_px=float(np.median(errors)),validation_median_px=float(np.median(allheld)) if len(allheld) else None,
                validation_p90_px=float(np.quantile(allheld,.9)) if len(allheld) else None,views=per_view,passed=valid,
                registered_images=len(accepted),attempted_images=n,max_azimuth_gap_deg=gap,full_ring=gap<=100,
                initial_angles_only=True,measured_rotation_angles=False,physical_scale_calibrated=False,
                loop_connection_tracks=int(loop_tracks),reversed_sequence_steps=reverse_steps,
                validation_note='整条轨迹留出；逐次用其余观测三角化，预测未参与三角化的那张照片。误差以1280匹配图像像素计。',
                coverage_warning='仅可靠视角参与颜色投影；未验证或未覆盖区域保留缺色标记',
                constraint_note='固定相机、共同转轴、固定距离；逐帧转角由图像残差优化，没有固定为照片编号角度')
    save_json(output/'pose_audit.json',report)
    if not valid:raise ValueError('转台约束求解未通过独立轨迹检查；灰模保留，详见配准诊断')
    # Save verified points/observations as standard COLMAP text without invented correspondences.
    goodpoints=[p for p in range(len(points)) if np.sum((obs_p==p)&(errors<3)&np.isin(obs_i,accepted))>=3]
    q=Rotation.from_matrix(R).as_quat();image_lines=[];point_lines=[];observation_lists={i:[] for i in accepted};tracks_out={}
    for new,p in enumerate(goodpoints,1):
        tracks_out[new]=[]
        for k in np.flatnonzero((obs_p==p)&(errors<3)&np.isin(obs_i,accepted)):
            i=int(obs_i[k]);idx=len(observation_lists[i]);observation_lists[i].append((uv[k],new));tracks_out[new].extend([i+1,idx])
        xyz=points[p];point_lines.append(f'{new} '+' '.join(map(str,xyz))+f' 128 128 128 {float(np.mean(errors[(obs_p==p)&(errors<3)]))} '+' '.join(map(str,tracks_out[new])))
    for i in accepted:
        image_lines.append(f'{i+1} '+ ' '.join(map(str,q[i,[3,0,1,2]]))+' '+' '.join(map(str,t))+f' 1 {image_names[i]}')
        image_lines.append(' '.join(f'{xy[0]} {xy[1]} {pid}' for xy,pid in observation_lists[i]))
    (output/'images.txt').write_text('\n'.join(image_lines)+'\n')
    (output/'cameras.txt').write_text(f'1 SIMPLE_PINHOLE {width} {height} {f} {cx} {cy}\n')
    (output/'points3D.txt').write_text('\n'.join(point_lines)+'\n')
    return report
