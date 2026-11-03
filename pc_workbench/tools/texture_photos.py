"""Bake this capture's observed colors to UVs; unobserved surface stays explicitly neutral."""
import argparse
from pathlib import Path
import shutil
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.common import read_json,save_json,sha256


def main(a):
    import cv2
    import numpy as np
    import trimesh
    import xatlas
    from PIL import Image,ImageDraw,ImageOps
    from scipy.ndimage import distance_transform_edt
    from studio.texture_geometry import read_cameras,project,depth_buffer,raster_samples,fit_similarity,fit_reliable_similarity,silhouette
    cv2.setNumThreads(4)
    start=time.perf_counter(); job=a.job.resolve(); run=a.run.resolve(); delivery=run/'delivery'
    output=run/'photo_texture'
    if output.exists(): raise ValueError('贴图结果已存在，请保留原结果并使用新的运行目录')
    output.mkdir()
    calibration=run.parent/'inputs/calibration'
    if not calibration.exists():calibration=job/'direction'
    if not (calibration/'result.json').exists():
        raise ValueError('尚无本轮相机配准；可保留手动四方向灰模，照片贴图需要先完成自动配准')
    direction=read_json(calibration/'result.json')
    input_manifest=read_json(run.parent/'inputs/views.json')
    selected=input_manifest['original_selection']['views']
    for name,digest in input_manifest['mask_sha256'].items():
        if sha256(run.parent/'inputs'/name)!=digest: raise ValueError('模型输入前景已改变，请重新生成模型')
    if calibration==job/'direction' and selected!=read_json(job/'selection.json')['views']: raise ValueError('四方向已改变，不能对旧模型使用新的配准')
    photos={r['id']:r for r in read_json(job/'photos.json')['photos']}
    sparse=calibration/'sparse'
    if sparse.exists() and direction.get('camera_files_sha256'):
        for name,digest in direction['camera_files_sha256'].items():
            if sha256(sparse/name)!=digest: raise ValueError('相机配准文件校验不一致')
    if not sparse.exists():
        # Compatibility with runs created before persistent camera export.
        work=Path(direction['work_path'])
        prefix='learned_text_' if direction['matcher'].startswith('SuperPoint') else 'text_'
        candidates=list(work.glob(prefix+'*'))
        eligible=[]
        for path in candidates:
            vs,pts=read_cameras(path)
            if set(selected.values()).issubset({v['id'] for v in vs}): eligible.append(path)
        if len(eligible)!=1: raise ValueError('缺少本轮唯一、完整的相机配准，无法自动贴图')
        sparse=eligible[0]
    snapshot=output/'inputs'; snapshot.mkdir()
    shutil.copytree(sparse,snapshot/'sparse')
    views,points=read_cameras(snapshot/'sparse')
    views=[v for v in views if v['id'] in direction['source_photo_sha256']]
    if direction.get('accepted_photo_ids'):
        views=[v for v in views if v['id'] in direction['accepted_photo_ids']]
    registered={v['id'] for v in views}
    if direction.get('partial_texture'):
        if len(views)<4 or selected['front'] not in registered:
            raise ValueError('可靠照片不足或缺少正面锚点，无法建立照片和灰模的投影关系')
    elif not set(selected.values()).issubset(registered):raise ValueError('选中的四方向没有全部配准')
    masks={}; images={}; provenance=[]
    for v in views:
        id_=v['id']; row=photos[id_]
        if sha256(job/row['file'])!=direction['source_photo_sha256'][id_]: raise ValueError('照片和配准版本不一致')
        maskpath=calibration/'masks'/(id_+'.png')
        # Use the immutable, possibly user-corrected input mask for the four anchors.
        for label,pid in selected.items():
            if pid==id_: maskpath=run.parent/'inputs'/(label+'.png')
        rgba=np.asarray(Image.open(maskpath).convert('RGBA')); masks[id_]=rgba[...,3]>0
        image=np.asarray(ImageOps.exif_transpose(Image.open(job/row['file'])).convert('RGB')); images[id_]=image
        if image.shape[:2]!=rgba.shape[:2]: raise ValueError('前景和原图尺寸不一致')
        shutil.copy2(maskpath,snapshot/(id_+'.png'))
        provenance.append(dict(id=id_,photo_sha256=row['sha256'],mask_sha256=sha256(maskpath)))
    save_json(snapshot/'provenance.json',dict(photos=provenance,shape_views=selected,pose_source='this capture independently registered images'))
    mesh=trimesh.load(delivery/'model_untextured.glb',force='mesh',process=False)
    print('TEXTURE_STAGE align',flush=True)
    if direction.get('partial_texture'):
        world,matrix,alignment,views=fit_reliable_similarity(mesh.vertices,mesh.faces,views,masks,points,selected['front'])
    else:world,matrix,alignment=fit_similarity(mesh.vertices,mesh.faces,views,masks,points,selected['front'])
    alignment['pose_audit']=direction.get('pose_audit')
    save_json(output/'alignment.json',dict(**alignment,matrix=matrix.tolist()))
    # The diagnostic overlays the exact unchanged surface onto original photographs.
    tiles=[]
    for label,id_ in selected.items():
        if id_ not in {v['id'] for v in views}:
            tile=Image.new('RGB',(640,480),(220,220,220))
            ImageDraw.Draw(tile).text((18,18),label+' / '+id_+' : excluded (unverified pose)',fill=(150,30,30))
            tiles.append(tile);continue
        view=next(v for v in views if v['id']==id_); im=Image.fromarray(images[id_]); im.thumbnail((640,480))
        pix=np.asarray(im).copy(); pred=silhouette(world,mesh.faces,view,im.width)
        contours,_=cv2.findContours(pred.astype('uint8'),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(pix,contours,-1,(0,220,255),2)
        tile=Image.fromarray(pix); ImageDraw.Draw(tile).text((12,12),label+' / '+id_,fill=(255,40,40)); tiles.append(tile)
    review=Image.new('RGB',(1280,960),(240,240,240))
    for i,tile in enumerate(tiles): review.paste(tile,((i%2)*640,(i//2)*480))
    review.save(output/'projection_review.png')
    shutil.copy2(output/'projection_review.png',job/'processing/projection_review.png')
    stages=read_json(job/'processing/stages.json')
    stages['stages']=[s for s in stages['stages'] if s['key']!='projection']
    stages['stages'].append(dict(key='projection',label='模型投影检查',file='processing/projection_review.png',detail='青色线为模型投影；和本轮照片比较外轮廓，不代表内部图案已对齐。'))
    save_json(job/'processing/stages.json',stages)
    print('TEXTURE_PREVIEW_READY',flush=True)
    if not alignment['passed']: raise ValueError('模型和照片轮廓对齐不足，已保留灰模与投影检查图，停止自动贴图')
    print('TEXTURE_STAGE uv',flush=True)
    # Real non-overlapping UV layout. Original geometry/normals survive seam duplication.
    atlas=xatlas.Atlas(); atlas.add_mesh(np.asarray(mesh.vertices,np.float32),np.asarray(mesh.faces,np.uint32))
    pack=xatlas.PackOptions(); pack.resolution=a.resolution; pack.padding=6; pack.bruteForce=False
    atlas.generate(pack_options=pack)
    mapping,faces,uv=atlas[0]; faces=np.asarray(faces,dtype=np.int64); uv=np.asarray(uv)
    mapped_faces=np.sort(np.asarray(mapping)[faces],axis=1)
    original_faces=np.sort(np.asarray(mesh.faces),axis=1)
    sort_rows=lambda f:f[np.lexsort((f[:,2],f[:,1],f[:,0]))]
    if not np.array_equal(sort_rows(mapped_faces),sort_rows(original_faces)):
        raise ValueError('UV 展开改变了原始三角形连接')
    original_index=np.empty(len(faces),int)
    original_index[np.lexsort((mapped_faces[:,2],mapped_faces[:,1],mapped_faces[:,0]))]=np.lexsort((original_faces[:,2],original_faces[:,1],original_faces[:,0]))
    width=height=a.resolution
    pos=np.zeros((height,width,3),np.float32); normal=np.zeros_like(pos); occupied=np.zeros((height,width),bool)
    texel_face=np.full((height,width),-1,np.int32)
    verts=world[mapping]; ns=np.asarray(mesh.vertex_normals)[mapping]@matrix[:3,:3].T
    ns/=np.maximum(np.linalg.norm(ns,axis=1,keepdims=True),1e-8)
    pixeluv=uv*np.array([width-1,-(height-1)])+[0,height-1]
    for ids,x,y,w in raster_samples(pixeluv,faces,width,height):
        texel_face[y,x]=original_index[ids]
        pos[y,x]=(verts[faces[ids]]*w[:,:,None]).sum(1); normal[y,x]=(ns[faces[ids]]*w[:,:,None]).sum(1); occupied[y,x]=True
    positions=pos[occupied]; normals=normal[occupied]; normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-8)
    del pos,normal
    from studio.texture_refinement import refine_bake,linear_to_srgb
    world_normals=np.asarray(mesh.vertex_normals)@matrix[:3,:3].T
    world_normals/=np.maximum(np.linalg.norm(world_normals,axis=1,keepdims=True),1e-8)
    colors,covered,source_index,vertex_colors,vertex_supported,refinement=refine_bake(
        world,mesh.faces,world_normals,views,images,masks,positions,normals,texel_face[occupied],selected['front'])
    used=refinement['photos']
    rgb=linear_to_srgb(colors[covered])
    texture=np.full((height,width,3),[160,167,177],np.uint8)
    yy,xx=np.where(occupied); texture[yy[covered],xx[covered]]=np.clip(rgb*255,0,255).astype('uint8')
    validmap=np.zeros((height,width),bool); validmap[yy[covered],xx[covered]]=True
    # Preserve the photo-only atlas before extrapolating missing surface colors.
    observed_texture=texture.copy()
    inferredmap=np.zeros((height,width),bool)
    for ids,x,y,w in raster_samples(pixeluv,faces,width,height):
        original_tri=np.asarray(mapping)[faces[ids]]
        fill=(~validmap[y,x])&vertex_supported[original_tri].all(1)
        color=(vertex_colors[original_tri[fill]]*w[fill,:,None]).sum(1)
        texture[y[fill],x[fill]]=np.clip(linear_to_srgb(color)*255,0,255).astype('uint8')
        inferredmap[y[fill],x[fill]]=True
    dist,nearest=distance_transform_edt(~occupied,return_indices=True)
    gutter=(~occupied)&(dist<=6)
    for image in (texture,observed_texture):
        image[gutter]=image[nearest[0][gutter],nearest[1][gutter]]
    tex=Image.fromarray(texture); tex.save(delivery/'texture_basecolor.png')
    Image.fromarray(observed_texture).save(delivery/'texture_observed.png')
    coverage=np.zeros((height,width,3),np.uint8); coverage[occupied]=[235,90,35]
    coverage[inferredmap]=[165,85,210]; coverage[validmap]=[40,190,100]
    Image.fromarray(coverage).save(delivery/'texture_coverage.png')
    material=trimesh.visual.material.PBRMaterial(baseColorTexture=tex,metallicFactor=0,roughnessFactor=.85)
    colored=trimesh.Trimesh(mesh.vertices[mapping],faces,vertex_normals=np.asarray(mesh.vertex_normals)[mapping],process=False,
        visual=trimesh.visual.TextureVisuals(uv=uv,material=material))
    colored.export(delivery/'model_colored.glb')
    observed_model=colored.copy()
    observed_model.visual.material=trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(observed_texture),metallicFactor=0,roughnessFactor=.85)
    observed_model.export(delivery/'model_observed.glb')
    colored.export(delivery/'model_textured.obj')
    import zipfile
    with zipfile.ZipFile(delivery/'model_textured.zip','w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ('model_textured.obj','material.mtl','material_0.png'):
            bundle.write(delivery/name,name)
    check=trimesh.load(delivery/'model_colored.glb',force='mesh',process=False)
    if len(check.faces)!=len(mesh.faces) or check.visual.uv is None or check.visual.material.baseColorTexture is None:
        raise ValueError('彩色 GLB 未通过重读验证')
    if not np.allclose(check.vertices[check.faces],colored.vertices[colored.faces],atol=1e-5):
        raise ValueError('贴图导出意外改变了几何')
    # Source labels are diagnostics, not proof that any color is physically measured accurately.
    np.savez_compressed(output/'coverage.npz',occupied=occupied,observed=validmap,inferred=inferredmap,source_index=source_index)
    report=dict(method='visibility-checked coherent photo patches, validated exposure and mesh-connected color extrapolation',
        photo_count=len(views),photos=used,input_hashes=provenance,ai_shape_input_count=4,
        input_photo_count=len(photos),pose_audit=direction.get('pose_audit'),partial_calibration=bool(direction.get('partial_texture')),
        texture_size=[width,height],observed_uv_texel_fraction=float(covered.mean()),
        missing_uv_texel_fraction=float(1-covered.mean()),inferred_uv_texel_fraction=float(inferredmap.sum()/occupied.sum()),
        unfilled_uv_texel_fraction=float((occupied&~(validmap|inferredmap)).sum()/occupied.sum()),
        unobserved_color='display model: mesh-connected interpolation; observed model: neutral gray',refinement=refinement,
        completion_is_inferred=True,
        upper_photo_count=sum(photos[v['id']]['camera']!=photos[selected['front']]['camera'] for v in views),
        alignment=alignment,seconds=round(time.perf_counter()-start,2),geometry_unchanged=True,
        limitations=['轮廓对齐不能验证内部图案完全对应。','展示版含邻近颜色推断，不能当作真实花纹；仅照片版保留缺色。UV 占比不是准确率。','底色含拍摄光照，未分离真实材质与阴影。'])
    save_json(delivery/'texture_report.json',report)
    existing=read_json(delivery/'model_report.json')
    existing.update(texture_generated=True,texture_completed=True,texture_report=report,default_preview='color',
        limitations=['四方向 AI 形状推断，未恢复实测尺寸、骨骼或动画拓扑。',*report['limitations']])
    existing['artifacts']['model_colored']=dict(file='model_colored.glb',sha256=sha256(delivery/'model_colored.glb'),faces=len(check.faces))
    existing['artifacts']['model_observed']=dict(file='model_observed.glb',sha256=sha256(delivery/'model_observed.glb'),faces=len(check.faces))
    save_json(delivery/'model_report.json',existing)
    stages=read_json(job/'processing/stages.json')
    for key,label,name,detail in [('projection','模型投影检查','projection_review.png','青色线为模型投影；和原照片比较轮廓对齐。'),('texture','照片颜色贴图','texture_basecolor.png','原片颜色＋缺色区域的表面邻域补色；可下载仅照片版对照。'),('coverage','贴图覆盖检查','texture_coverage.png','绿色：照片观测；紫色：推断补色；橙色：仍无颜色；黑色：UV 空白。')]:
        source=output/name if key=='projection' else delivery/name
        target=job/'processing'/name; shutil.copy2(source,target)
        stages['stages']=[s for s in stages['stages'] if s['key']!=key]
        stages['stages'].append(dict(key=key,label=label,file='processing/'+name,detail=detail))
    save_json(job/'processing/stages.json',stages)
    print('TEXTURE_COMPLETE',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(); p.add_argument('--job',type=Path,required=True); p.add_argument('--run',type=Path,required=True)
    p.add_argument('--resolution',type=int,choices=[1024,2048,4096],default=2048)
    args=p.parse_args()
    try:
        main(args)
    except Exception as exc:
        save_json(args.run/'photo_texture/error.json',dict(error=str(exc)))
        raise
