"""Conservative capture-local ROI for the explicitly enabled fixed turntable setup.

Only a wide, low support visible in several views is eligible. No color thresholds
or anatomy-specific rules. Ambiguous scenes retain the manual subject-box option.
"""
import numpy as np


def support_proposal(alpha):
    import cv2
    h,w=alpha.shape
    count,labels,stats,_=cv2.connectedComponentsWithStats((alpha>0).astype('uint8'))
    if count<2:
        return None
    mask=labels==(1+int(np.argmax(stats[1:,4])))
    yy,xx=np.where(mask)
    top,bottom=int(yy.min()),int(yy.max())
    profile=np.convolve(mask.sum(1),np.ones(9)/9,mode='same')
    base_width=float(np.quantile(profile[top:bottom+1],.95))
    hits=np.flatnonzero((profile>.75*base_width)&(np.arange(h)>top+.5*(bottom-top)))
    if not len(hits) or base_width<.18*w:
        return None
    start=int(hits[0]);cut=round(start-.010*h)
    if cut-top<.20*h or not .08<(bottom-start)/base_width<.42:
        return None
    if np.quantile(profile[top:cut],.95)>.68*base_width:
        return None
    stable=profile[start:bottom]
    if (stable>.8*base_width).mean()<.55:
        return None
    sy,sx=np.where(mask[:cut])
    box=[max(0,(sx.min()-.018*w)/w), max(0,(sy.min()-.018*h)/h),
         min(1,(sx.max()+1+.018*w)/w), min(1,(cut+2)/h)]
    return dict(box=list(map(float,box)),support_width=base_width/w,
                support_top=start/h,support_bottom=bottom/h)


def consensus_subject_box(proposals):
    good=[p for p in proposals if p is not None]
    if len(good)<3 or len(good)<.75*len(proposals):
        return None
    if np.ptp([p['support_top'] for p in good])>.025 or np.ptp([p['support_width'] for p in good])>.07:
        return None
    boxes=np.asarray([p['box'] for p in good])
    return [float(boxes[:,0].min()),float(boxes[:,1].min()),
            float(boxes[:,2].max()),float(boxes[:,3].max())]
