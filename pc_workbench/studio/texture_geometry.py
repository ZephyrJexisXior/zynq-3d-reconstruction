"""Camera projection, visibility and whole-object alignment for photograph textures."""
from pathlib import Path
import cv2
import numpy as np


def read_cameras(directory):
    from scipy.spatial.transform import Rotation
    cameras, views, points = {}, [], []
    directory = Path(directory)
    for line in (directory/'cameras.txt').read_text().splitlines():
        if not line or line.startswith('#'): continue
        r=line.split(); cameras[int(r[0])]=dict(model=r[1], size=list(map(int,r[2:4])), params=list(map(float,r[4:])))
    lines=[s for s in (directory/'images.txt').read_text().splitlines() if not s.startswith('#')]
    for i in range(0,len(lines),2):
        r=lines[i].split()
        if not r: continue
        q=np.array(list(map(float,r[1:5]))); R=Rotation.from_quat(q[[1,2,3,0]]).as_matrix(); t=np.array(list(map(float,r[5:8])))
        views.append(dict(id=Path(r[9]).stem, R=R, t=t, center=-R.T@t, camera=cameras[int(r[8])]))
    for line in (directory/'points3D.txt').read_text().splitlines():
        if not line or line.startswith('#'): continue
        r=line.split()
        if float(r[7])<2.5 and len(r)>=14: points.append(list(map(float,r[1:4])))
    return views,np.array(points)


def project(vertices, view):
    p=np.asarray(vertices)@view['R'].T+view['t']; z=p[:,2]
    xy=p[:,:2]/np.maximum(z[:,None],1e-8)
    camera=view['camera']; params=camera['params']; model=camera['model']
    if model=='SIMPLE_RADIAL':
        f,cx,cy,k=params; xy*=1+k*np.sum(xy*xy,axis=1,keepdims=True); uv=xy*f+[cx,cy]
    elif model=='SIMPLE_PINHOLE':
        f,cx,cy=params; uv=xy*f+[cx,cy]
    elif model=='PINHOLE':
        fx,fy,cx,cy=params; uv=xy*[fx,fy]+[cx,cy]
    else: raise ValueError('Unsupported calibrated camera: '+model)
    return uv,z


def raster_samples(uv,faces,width,height,block=512):
    """Yield covered pixel indices and barycentric coordinates, with bounded memory."""
    for start in range(0,len(faces),block):
        tri=faces[start:start+block]; p=uv[tri]
        low=np.maximum(np.ceil(p.min(1)).astype(int),[0,0]); high=np.minimum(np.floor(p.max(1)).astype(int),[width-1,height-1])
        size=np.maximum(high-low+1,0); count=size[:,0]*size[:,1]
        if count.sum()>2000000 and len(tri)>1:
            for offset in range(0,len(tri),max(1,len(tri)//8)):
                sub=tri[offset:offset+max(1,len(tri)//8)]
                for ids,x,y,w in raster_samples(uv,sub,width,height,block=64):
                    yield ids+start+offset,x,y,w
            continue
        ids=np.repeat(np.arange(len(tri)),count)
        if not len(ids): continue
        local=np.arange(len(ids))-np.repeat(np.cumsum(count)-count,count)
        x=low[ids,0]+local%size[ids,0]; y=low[ids,1]+local//size[ids,0]
        a,b,c=p[ids,0],p[ids,1],p[ids,2]
        den=(b[:,1]-c[:,1])*(a[:,0]-c[:,0])+(c[:,0]-b[:,0])*(a[:,1]-c[:,1])
        valid=abs(den)>1e-10; den=np.where(valid,den,1)
        u=((b[:,1]-c[:,1])*(x-c[:,0])+(c[:,0]-b[:,0])*(y-c[:,1]))/den
        v=((c[:,1]-a[:,1])*(x-c[:,0])+(a[:,0]-c[:,0])*(y-c[:,1]))/den
        w=1-u-v; valid&=(u>=-1e-6)&(v>=-1e-6)&(w>=-1e-6)
        yield ids[valid]+start,x[valid],y[valid],np.c_[u,v,w][valid]


def depth_buffer(vertices,faces,view,width):
    uv,z=project(vertices,view); ow,oh=view['camera']['size']; h=round(oh*width/ow)
    depth=np.full((h,width),np.inf,np.float32)
    valid=np.isfinite(uv).all(1)&(z>1e-6)
    faces=faces[valid[faces].all(1)]
    for ids,x,y,w in raster_samples(uv*width/ow,faces,width,h):
        d=1/np.maximum((w/z[faces[ids]]).sum(1),1e-12)
        np.minimum.at(depth,(y,x),d)
    return depth


def silhouette(vertices,faces,view,width):
    uv,z=project(vertices,view); ow,oh=view['camera']['size']
    image=np.zeros((round(oh*width/ow),width),np.uint8)
    triangles=np.rint(np.clip(uv*width/ow,-10000,10000)).astype(np.int32)[faces]
    for tri in triangles[(z[faces]>0).all(1)]: cv2.fillConvexPoly(image,tri,1)
    return image.astype(bool)


def overlap(a,b):
    return float(np.count_nonzero(a&b)/max(np.count_nonzero(a|b),1))


def fit_similarity(vertices, faces, views, masks, points, front):
    """One similarity transform for every view; no anatomy rules or image stretching."""
    from scipy.spatial.transform import Rotation
    from scipy.optimize import least_squares, minimize
    up=np.median([-v['R'][1] for v in views],axis=0); up/=np.linalg.norm(up)
    ref=next(v for v in views if v['id']==front)
    right=ref['R'][0]-np.dot(ref['R'][0],up)*up; right/=np.linalg.norm(right)
    basis=np.column_stack([right,up,np.cross(right,up)])
    origin=(vertices.min(0)+vertices.max(0))/2
    target=np.median(points,axis=0)
    dist=np.linalg.norm(ref['center']-target)
    ys,xs=np.where(masks[front]); full_h=masks[front].shape[0]
    f=ref['camera']['params'][0]
    estimated=(ys.max()-ys.min())/full_h*ref['camera']['size'][1]*dist/f
    scale=estimated/max(np.ptp(vertices[:,1]),1e-8)
    span=scale*np.ptp(vertices,axis=0).max()
    def transform(p):
        rotation=Rotation.from_rotvec(p[:3]).as_matrix()@basis
        s=scale*np.exp(p[6]); offset=target+p[3:6]*span
        return (vertices-origin)@rotation.T*s+offset
    small={v['id']:cv2.resize(masks[v['id']].astype('uint8'),(256,round(v['camera']['size'][1]*256/v['camera']['size'][0])),interpolation=cv2.INTER_NEAREST)>0 for v in views}
    bounds=[]
    for v in views:
        yy,xx=np.where(small[v['id']]); bounds.append(np.array([xx.min(),yy.min(),xx.max(),yy.max()]))
    def residual(p):
        world=transform(p); result=[]
        for v,b in zip(views,bounds):
            uv,_=project(world,v); uv*=256/v['camera']['size'][0]
            result.extend((np.r_[uv.min(0),uv.max(0)]-b)/256)
        return np.r_[result,p[:3]*.015]
    lower=[-.35,-.35,-.35,-1.5,-1.5,-1.5,-.7]; upper=[.35,.35,.35,1.5,1.5,1.5,.7]
    fit=least_squares(residual,np.zeros(7),bounds=(lower,upper),max_nfev=100,loss='soft_l1',f_scale=.025)
    # Refine against the entire outline, using a reduced mesh only for fitting.
    import pymeshlab
    ms=pymeshlab.MeshSet(); ms.add_mesh(pymeshlab.Mesh(vertices,faces))
    ms.apply_filter('meshing_decimation_quadric_edge_collapse',targetfacenum=2500,preservetopology=True,preserveboundary=True)
    reduced=ms.current_mesh(); rv=reduced.vertex_matrix(); rf=reduced.face_matrix()
    def reduced_world(p):
        rotation=Rotation.from_rotvec(p[:3]).as_matrix()@basis
        return (rv-origin)@rotation.T*(scale*np.exp(p[6]))+target+p[3:6]*span
    def objective(p):
        vtx=reduced_world(p)
        return np.mean([1-overlap(silhouette(vtx,rf,v,256),small[v['id']]) for v in views])+.003*np.dot(p[:3],p[:3])
    before=objective(fit.x)
    refined=minimize(objective,fit.x,method='Powell',bounds=list(zip(lower,upper)),options={'maxfev':250,'xtol':.001,'ftol':.0005})
    p=refined.x if refined.fun<before else fit.x
    rotation=Rotation.from_rotvec(p[:3]).as_matrix()@basis; s=scale*np.exp(p[6])
    matrix=np.eye(4); matrix[:3,:3]=rotation*s; matrix[:3,3]=target+p[3:6]*span-origin@rotation.T*s
    world=transform(p)
    audit=[dict(id=v['id'],silhouette_iou=overlap(silhouette(world,faces,v,384),cv2.resize(masks[v['id']].astype('uint8'),(384,round(v['camera']['size'][1]*384/v['camera']['size'][0])),interpolation=cv2.INTER_NEAREST)>0)) for v in views]
    mean=float(np.mean([a['silhouette_iou'] for a in audit])); worst=min(a['silhouette_iou'] for a in audit)
    return world, matrix, dict(method='single global similarity fitted to all registered foreground outlines',views=audit,mean_silhouette_iou=mean,minimum_silhouette_iou=worst,
        geometry_modified=False,physical_scale_calibrated=False,passed=mean>=.72 and worst>=.55,
        warning='轮廓重合不能证明内部图案、凹凸或细节对齐。')


def fit_reliable_similarity(vertices,faces,views,masks,points,front):
    world,matrix,audit=fit_similarity(vertices,faces,views,masks,points,front)
    excluded=[v['id'] for v in audit['views'] if v['silhouette_iou']<.55]
    kept=[v for v in views if v['id'] not in excluded]
    if excluded and len(kept)>=4 and front not in excluded:
        world,matrix,refit=fit_similarity(vertices,faces,kept,masks,points,front)
        refit['excluded_views']=[dict(v,reason='照片与当前灰模投影轮廓不一致') for v in audit['views'] if v['id'] in excluded]
        audit=refit
        views=kept
    else:audit['excluded_views']=[]
    return world,matrix,audit,views
