"""Real preprocessing outputs for both automatic and reviewed modeling flows."""
from pathlib import Path
import json
from .common import save_json, read_json, sha256, ROOT
from .foreground_boundary import FOREGROUND_VERSION


class Foreground:
    def __init__(self, model):
        self.model = Path(model)
        self.session = None

    def segment(self, rgba, box=None, refine=True):
        import cv2
        import numpy as np
        height, width = rgba.shape[:2]
        box = box or [0, 0, 1, 1]
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError('无效的主体框')
        x0, y0, x1, y1 = [round(v*d) for v, d in zip(box, [width, height, width, height])]
        if x1-x0 < 16 or y1-y0 < 16:
            raise ValueError('主体框太小')
        details = dict(foreground_version=FOREGROUND_VERSION)
        if rgba[..., 3].min() < 255:
            alpha = np.zeros((height, width), np.uint8)
            alpha[y0:y1, x0:x1] = rgba[y0:y1, x0:x1, 3]
            method = '保留原片透明前景'
        else:
            if self.session is None:
                import onnxruntime as ort
                reference = read_json(ROOT / 'config/isnet_source.json')
                if sha256(self.model) != reference['sha256']:
                    raise ValueError('分割模型校验失败')
                options = ort.SessionOptions()
                options.intra_op_num_threads = 6
                options.inter_op_num_threads = 1
                self.session = ort.InferenceSession(str(self.model), options, providers=['CPUExecutionProvider'])
            crop = rgba[y0:y1, x0:x1, :3]
            def predict(pixels):
                tensor = (cv2.resize(pixels, (1024, 1024)).astype(np.float32)/255-.5).transpose(2, 0, 1)[None]
                raw = self.session.run(None, {'input_image': tensor})[0][0, 0]
                if not np.isfinite(raw).all() or np.ptp(raw) < 1e-6:
                    raise ValueError('分割没有找到有效主体，请框选主体后重试')
                return cv2.resize((raw-raw.min())/np.ptp(raw), (pixels.shape[1], pixels.shape[0]))
            score = predict(crop)
            alpha = np.zeros((height, width), np.uint8)
            if refine:
                context = None
                seen = {(x0,y0,x1,y1)}
                for fraction in (.15, .30):
                    padding = round(fraction*max(x1-x0, y1-y0))
                    cx0,cy0,cx1,cy1 = max(0,x0-padding),max(0,y0-padding),min(width,x1+padding),min(height,y1+padding)
                    if (cx0,cy0,cx1,cy1) in seen:
                        continue
                    seen.add((cx0,cy0,cx1,cy1))
                    expanded = predict(rgba[cy0:cy1,cx0:cx1,:3])
                    proposal = expanded[y0-cy0:y1-cy0,x0-cx0:x1-cx0]
                    context = proposal if context is None else np.maximum(context, proposal)
                from .foreground_boundary import refine_foreground
                alpha[y0:y1,x0:x1], details = refine_foreground(crop, score, context)
                details['context_passes'] = len(seen)
                method = 'IS-Net 主体与周边多范围识别＋原尺寸图像边缘校正；保留原片颜色'
            else:
                alpha[y0:y1, x0:x1] = (score >= .35).astype(np.uint8)*255
                method = 'IS-Net 转台范围预检查'
        if np.count_nonzero(alpha) < 100:
            raise ValueError('前景为空或过小，请检查图片')
        result = rgba.copy()
        result[..., 3] = alpha
        return result, dict(method=method, foreground_pixels=int(np.count_nonzero(alpha)), box=box, **details)


def subject_box(selection, row, photos):
    """Capture-local ROI, shared only by the reference camera; per-view overrides win."""
    views = selection.get('views', {})
    boxes = selection.get('boxes', {})
    for label, pid in views.items():
        if pid == row['id'] and boxes.get(label):
            return boxes[label]
    reference = next((p for p in photos if p['id'] == views.get('front')), None)
    if reference and reference['camera'] == row['camera']:
        return boxes.get('front') or selection.get('reference_box')
    return None


def exclude_background(rgba, rectangles):
    """Apply explicit capture-local background annotations, never a color threshold."""
    height, width = rgba.shape[:2]
    result = rgba.copy()
    for box in rectangles:
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError('无效的背景排除框')
        x0,y0,x1,y1 = [round(v*d) for v,d in zip(box,[width,height,width,height])]
        result[y0:y1,x0:x1,3] = 0
    if not result[...,3].any():
        raise ValueError('背景排除框移除了全部主体，请重新检查')
    return result


def save_image_preview(image, path):
    from PIL import ImageOps
    preview = ImageOps.exif_transpose(image).copy()
    preview.thumbnail((900, 900))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path)


def write_stages(job, original, foreground, mask_info, selected):
    """One actual representative view per stage; no invented denoise/sharpen stages."""
    from PIL import Image
    stage = Path(job) / 'processing'
    stage.mkdir(exist_ok=True)
    save_image_preview(original, stage/'original.png')
    save_image_preview(foreground.getchannel('A'), stage/'mask.png')
    save_image_preview(foreground, stage/'foreground.png')
    rows = [
        dict(key='original', label='原始照片', file='processing/original.png',
             detail='本轮选中参考视角的原片；只缩小展示，原始文件保留。'),
        dict(key='mask', label='主体分割', file='processing/mask.png',
             detail='白色保留、黑色背景；这是主体掩码，不是按物体颜色分类。'),
        dict(key='foreground', label='去背景结果', file='processing/foreground.png',
             detail=mask_info['method']),
    ]
    save_json(stage/'stages.json', dict(sample=selected, stages=rows))
