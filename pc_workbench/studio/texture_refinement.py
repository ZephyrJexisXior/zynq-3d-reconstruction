"""Whole-surface photo selection and explicitly inferred color completion."""
import numpy as np


def srgb_to_linear(rgb):
    return np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)


def linear_to_srgb(rgb):
    return np.where(rgb<=.0031308,rgb*12.92,1.055*np.maximum(rgb,0)**(1/2.4)-.055)


def sample_observation(points, normals, view, image, distance, depth, span, sharpness=1):
    from scipy.ndimage import map_coordinates
    from .texture_geometry import project
    xy,z=project(points,view); ow,oh=view['camera']['size']; ih,iw=image.shape[:2]
    xy*=np.array([iw/ow,ih/oh]); dw=depth.shape[1]
    inside=(xy[:,0]>=1)&(xy[:,0]<iw-2)&(xy[:,1]>=1)&(xy[:,1]<ih-2)&(z>0)
    interior=map_coordinates(distance,[xy[:,1],xy[:,0]],order=1,mode='constant',cval=0)
    dx=np.clip(np.rint(xy[:,0]*dw/iw).astype(int),0,dw-1)
    dy=np.clip(np.rint(xy[:,1]*dw/iw).astype(int),0,depth.shape[0]-1)
    zr=depth[dy,dx]; direction=view['center']-points; direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-8)
    cosine=np.sum(normals*direction,axis=1)
    seen=inside&np.isfinite(zr)&(abs(z-zr)<span*.006)&(cosine>.12)&(interior>1)
    score=np.maximum(cosine,0)**5*np.clip(interior/6,0,1)*sharpness*seen
    rgb=np.stack([map_coordinates(image[...,k].astype('float32')/255,[xy[:,1],xy[:,0]],order=1,mode='constant',cval=0) for k in range(3)],axis=1)
    return srgb_to_linear(rgb),score.astype(np.float32)


def exposure_gains(colors, scores, reference):
    """Bounded scalar exposure correction, accepted only on held-out shared samples."""
    from scipy.optimize import least_squares
    count=len(colors); luminance=colors@np.array([.2126,.7152,.0722])
    equations=[]; validation=[]
    for i in range(count):
        for j in range(i):
            good=(scores[i]>.04)&(scores[j]>.04)&(luminance[i]>.025)&(luminance[j]>.025)&(luminance[i]<.8)&(luminance[j]<.8)
            # Reject material disagreement instead of interpreting pattern displacement as exposure.
            chromai=colors[i]/np.maximum(luminance[i,:,None],.01); chromaj=colors[j]/np.maximum(luminance[j,:,None],.01)
            good&=np.linalg.norm(chromai-chromaj,axis=1)<.15
            ids=np.flatnonzero(good)[::7]
            if len(ids)<30: continue
            delta=np.log(luminance[j,ids])-np.log(luminance[i,ids])
            train=np.arange(len(ids))%5!=0
            equations.append((i,j,float(np.median(delta[train]))))
            validation.extend((i,j,float(x)) for x in delta[~train])
    if not equations or not validation:
        return np.ones(count),dict(applied=False,reason='insufficient shared color samples')
    def residual(p):
        return np.r_[[p[i]-p[j]-d for i,j,d in equations],p[reference]*10,p*.1]
    fit=least_squares(residual,np.zeros(count),bounds=(-.12,.12),loss='soft_l1',f_scale=.02)
    before=float(np.median([abs(d) for i,j,d in validation])); after=float(np.median([abs(fit.x[i]-fit.x[j]-d) for i,j,d in validation]))
    accepted=before>.008 and after<before*.9
    return np.exp(fit.x) if accepted else np.ones(count),dict(applied=bool(accepted),held_out_log_luminance_mae_before=before,held_out_log_luminance_mae_after=after,pairs=len(equations),maximum_log_gain=.12)


def coherent_labels(colors,scores,adjacency,face_normals,iterations=6):
    """Choose contiguous visible photo patches, penalizing unsafe color-switch seams."""
    import cv2
    nviews,nfaces=scores.shape
    valid=scores>1e-6; best=scores.max(0); covered=best>1e-6
    costs=-np.log(np.maximum(scores/np.maximum(best[None],1e-8),1e-8))*.14
    costs[~valid]=10000
    labels=np.argmax(scores,axis=0); labels[~covered]=-1
    neighbors=np.full((nfaces,3),-1,int); degree=np.zeros(nfaces,int)
    for i,j in adjacency:
        if degree[i]<3: neighbors[i,degree[i]]=j; degree[i]+=1
        if degree[j]<3: neighbors[j,degree[j]]=i; degree[j]+=1
    safe=neighbors.clip(0); legal=neighbors>=0
    weights=np.clip(np.einsum('ij,ikj->ik',face_normals,face_normals[safe]),0,1)**3*legal
    initial=labels.copy(); changes=[]
    # Each pass updates an independent set. Adjacent labels never switch
    # simultaneously, preventing oscillation between two source photos.
    groups=np.full(nfaces,-1,np.int8)
    for face in range(nfaces):
        used=set(groups[neighbors[face][legal[face]]])
        groups[face]=next(g for g in range(4) if g not in used)
    lab=cv2.cvtColor(np.clip(linear_to_srgb(colors),0,1).astype('float32'),cv2.COLOR_RGB2LAB)/np.array([100.,128.,128.],np.float32)
    for iteration in range(iterations):
        changed=0
        for group in range(4):
            ids=np.flatnonzero(groups==group);nb=safe[ids];neighbor_labels=labels[nb]
            energy=costs[:,ids].copy()
            for i in range(nviews):
                # Symmetric same-surface-point disagreement gives one consistent pair energy.
                jump_a=np.linalg.norm(lab[i,ids,None,:]-lab[neighbor_labels.clip(0),ids[:,None]],axis=2)
                jump_b=np.linalg.norm(lab[i,nb]-lab[neighbor_labels.clip(0),nb],axis=2)
                jump=(jump_a+jump_b)*.5
                penalty=(.20+np.minimum(jump,.4)*1.6)*weights[ids]*(neighbor_labels>=0)*(neighbor_labels!=i)
                energy[i]+=penalty.sum(1)
            choice=energy.argmin(0)
            improvement=energy[labels[ids].clip(0),np.arange(len(ids))]-energy[choice,np.arange(len(ids))]
            change=covered[ids]&(choice!=labels[ids])&(improvement>.003)
            labels[ids[change]]=choice[change];changed+=int(change.sum())
        changes.append(changed)
        if changed==0:break
    def switches(x):
        both=(x[adjacency[:,0]]>=0)&(x[adjacency[:,1]]>=0)
        return int(np.sum(both&(x[adjacency[:,0]]!=x[adjacency[:,1]])))
    return labels,dict(method='visibility-constrained photo patches with adjacency and same-point color costs',label_changes=changes,source_switch_edges_before=switches(initial),source_switch_edges_after=switches(labels))


def complete_vertex_colors(vertices, faces, normals, color, known):
    """Harmonic interpolation along mesh edges. Disconnected unobserved parts stay unknown."""
    from scipy.sparse import coo_matrix,diags
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse.linalg import spsolve
    edges=np.vstack([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]); edges=np.unique(np.sort(edges,axis=1),axis=0)
    length=np.linalg.norm(vertices[edges[:,0]]-vertices[edges[:,1]],axis=1)
    cosine=np.einsum('ij,ij->i',normals[edges[:,0]],normals[edges[:,1]])
    weights=(.03+np.maximum(cosine,0)**4)/np.maximum(length,1e-8)
    rows=np.r_[edges[:,0],edges[:,1]]; cols=np.r_[edges[:,1],edges[:,0]]
    graph=coo_matrix((np.r_[weights,weights],(rows,cols)),shape=(len(vertices),len(vertices))).tocsr()
    components,groups=connected_components(graph,directed=False)
    has_anchor=np.bincount(groups[known],minlength=components)>0
    fill=(~known)&has_anchor[groups]; output=color.copy()
    if fill.any():
        laplacian=diags(np.asarray(graph.sum(1)).ravel())-graph
        unknown=np.flatnonzero(fill); observed=np.flatnonzero(known)
        rhs=graph[unknown][:,observed]@color[observed]
        output[unknown]=np.clip(spsolve(laplacian[unknown][:,unknown],rhs),0,1)
    return output,fill


def refine_bake(world,faces,vertex_normals,views,images,masks,positions,normals,texel_faces,front):
    import cv2
    from .texture_geometry import depth_buffer
    span=np.ptp(world,axis=0).max();centers=world[faces].mean(1)
    face_normals=np.cross(world[faces[:,1]]-world[faces[:,0]],world[faces[:,2]]-world[faces[:,0]])
    face_normals/=np.maximum(np.linalg.norm(face_normals,axis=1,keepdims=True),1e-8)
    import trimesh
    mesh=trimesh.Trimesh(world,faces,process=False)
    depths=[];distances=[];sharp=[];face_colors=[];face_scores=[]
    for view in views:
        id_=view['id'];im=images[id_];mask=masks[id_]
        gray=cv2.cvtColor(im,cv2.COLOR_RGB2GRAY)
        sharp.append(float(cv2.Laplacian(gray,cv2.CV_32F)[mask].var()))
        depths.append(depth_buffer(world,faces,view,1280))
        distances.append(cv2.distanceTransform(mask.astype('uint8'),cv2.DIST_L2,3))
    quality=np.clip(np.array(sharp)/max(np.median(sharp),1),.5,1.5)
    for i,view in enumerate(views):
        rgb,score=sample_observation(centers,face_normals,view,images[view['id']],distances[i],depths[i],span,quality[i])
        face_colors.append(rgb);face_scores.append(score)
        print('TEXTURE_VIEW',i+1,len(views)*2,flush=True)
    face_colors=np.array(face_colors);face_scores=np.array(face_scores)
    gain,exposure=exposure_gains(face_colors,face_scores,next(i for i,v in enumerate(views) if v['id']==front))
    face_colors=np.clip(face_colors*gain[:,None,None],0,1)
    print('TEXTURE_STAGE patches',flush=True)
    labels,label_report=coherent_labels(face_colors,face_scores,mesh.face_adjacency,face_normals)
    preferred=labels[texel_faces]
    # The coherent photo is used wherever it is individually visible. Otherwise the best visible photo fills it.
    rgbout=np.zeros((len(positions),3),np.float32);scoreout=np.zeros(len(positions),np.float32)
    source=np.full(len(positions),-1,np.int16)
    vcolors=np.zeros((len(world),3),np.float32);vscore=np.zeros(len(world),np.float32)
    used=[]
    for i,view in enumerate(views):
        count=0
        for begin in range(0,len(positions),150000):
            end=min(begin+150000,len(positions))
            color,score=sample_observation(positions[begin:end],normals[begin:end],view,images[view['id']],distances[i],depths[i],span,quality[i])
            valid=score>1e-6; preferred_here=preferred[begin:end]==i
            selection_score=np.where(valid,score+preferred_here*10,0)
            better=selection_score>scoreout[begin:end]
            rgbout[begin:end][better]=np.clip(color[better]*gain[i],0,1)
            scoreout[begin:end][better]=selection_score[better];source[begin:end][better]=i
            count+=int(better.sum())
        vc,vs=sample_observation(world,vertex_normals,view,images[view['id']],distances[i],depths[i],span,quality[i])
        better=vs>vscore;vscore[better]=vs[better];vcolors[better]=np.clip(vc[better]*gain[i],0,1)
        used.append(dict(id=view['id'],sharpness=sharp[i],exposure_gain=float(gain[i])))
        print('TEXTURE_VIEW',len(views)+i+1,len(views)*2,flush=True)
    known=scoreout>0
    print('TEXTURE_STAGE completion',flush=True)
    completed,vfilled=complete_vertex_colors(world,faces,vertex_normals,vcolors,vscore>1e-6)
    for i,item in enumerate(used):item['selected_texels']=int(np.sum(source==i))
    return rgbout,known,source,completed,(vscore>1e-6)|vfilled,dict(photo_patches=label_report,exposure=exposure,photos=used,
        inferred_vertices=int(vfilled.sum()),vertex_observed_count=int((vscore>1e-6).sum()),
        completion_method='normal-weighted harmonic color interpolation on mesh connectivity; no new geometric or photo evidence')
