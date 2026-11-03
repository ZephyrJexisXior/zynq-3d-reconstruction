"""Image-guided refinement of semantic foreground, independent of object anatomy.

Context is evidence, not a new output ROI. No white-pixel removal, hole filling,
convex hull, or minimum component size: real gaps and thin parts must survive.
"""
FOREGROUND_VERSION = 'context_native_edges_v1'


def refine_foreground(rgb, primary, context=None):
    import cv2
    import numpy as np
    base = primary >= .35
    score = primary.copy()
    if context is not None:
        # Extra context can restore material missed by the tight crop. Do not
        # introduce disconnected objects detected only in the expanded crop.
        candidates = (base | (context >= .35)).astype('uint8')
        _, labels = cv2.connectedComponents(candidates)
        touched = np.unique(labels[base])
        supported = np.isin(labels, touched[touched != 0])
        score[supported] = np.maximum(primary, context)[supported]
    initial = score >= .35
    if not initial.any():
        raise ValueError('前景为空，请检查主体框')
    radius = max(2, min(12, round(min(rgb.shape[:2])*.007)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
    possible = cv2.dilate(initial.astype('uint8'), kernel).astype(bool)
    # Graph-cut only selects original pixels in a narrow semantic uncertainty
    # band; dilation creates candidates, never the final silhouette itself.
    core = cv2.erode((score >= .9).astype('uint8'), np.ones((3,3), 'uint8')).astype(bool)
    result = initial
    status = 'semantic_only'
    if core.any() and (~possible).any():
        yy, xx = np.where(possible)
        y0, y1 = max(0, int(yy.min())-2), min(len(rgb), int(yy.max())+3)
        x0, x1 = max(0, int(xx.min())-2), min(rgb.shape[1], int(xx.max())+3)
        region = np.s_[y0:y1, x0:x1]
        cut = np.where(initial[region], cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype('uint8')
        cut[~possible[region]] = cv2.GC_BGD
        cut[core[region]] = cv2.GC_FGD
        cv2.setRNGSeed(0)
        cv2.grabCut(np.ascontiguousarray(rgb[region]), cut, None,
                    np.zeros((1,65), np.float64), np.zeros((1,65), np.float64),
                    3, cv2.GC_INIT_WITH_MASK)
        result = np.zeros_like(initial)
        result[region] = (cut == cv2.GC_FGD) | (cut == cv2.GC_PR_FGD)
        status = 'native_rgb_graph_cut'
    return result.astype('uint8')*255, dict(
        foreground_version=FOREGROUND_VERSION, boundary_method=status,
        context_added_pixels=int((initial & ~base).sum()),
        restored_pixels=int((result & ~base).sum()),
        removed_boundary_pixels=int((base & ~result).sum()),
        uncertain_pixels=int((possible & ~core).sum()))
