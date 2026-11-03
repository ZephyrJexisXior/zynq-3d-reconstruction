"""Nominal turntable suggestions for shape only; never calibrated camera poses."""
import math
from .ingest import primary_sequence, default_front


def suggest_views(report, front_id=None, increasing='left'):
    if not report.get('capture_verified'):
        raise ValueError('普通文件夹没有已核验的采集顺序，请手选四方向，或选择先配准计算方向')
    rows = primary_sequence(report['photos'])
    positions = [r.get('position_index') for r in rows]
    if len(rows) < 8 or any(type(i) is not int for i in positions) or positions != list(range(len(rows))):
        raise ValueError('主摄位置不完整，无法按采集顺序预选；请手选四方向')
    front_id = front_id or default_front(report)['id']
    if front_id not in {r['id'] for r in rows}:
        raise ValueError('正面参考必须来自本轮主摄照片')
    if increasing not in ('left', 'right'):
        raise ValueError('请配置采集顺序朝向 left 或 right')
    anchor = next(i for i,r in enumerate(rows) if r['id'] == front_id)
    sign = 1 if increasing == 'left' else -1
    return {label:rows[(anchor+sign*math.floor(f*len(rows)+.5))%len(rows)]['id']
            for label,f in [('front',0),('left',.25),('back',.5),('right',.75)]}
