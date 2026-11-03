"""Copy a reproducible, angle-balanced subset; prior poses select only, never reconstruct."""
import argparse
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.common import read_json, save_json, sha256


def select(candidates, count):
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    if count < 8 or count > len(candidates):
        raise ValueError('Insufficient registered candidates')
    targets = np.arange(count)*360/count
    angles = np.array([r['relative_angle_deg'] for r in candidates])
    costs = abs((targets[:, None]-angles[None, :]+180)%360-180)
    a, b = linear_sum_assignment(costs)
    return [dict(**candidates[j], target_angle_deg=float(targets[i]), deviation_deg=float(costs[i,j])) for i,j in zip(a,b)]


def main(a):
    report = read_json(a.job/'direction/result.json')
    photos = {r['id']:r for r in read_json(a.job/'photos.json')['photos']}
    rows = select(report['candidates'], a.count)
    if a.output.exists():
        raise ValueError('Use a fresh folder')
    a.output.mkdir(parents=True)
    for i,row in enumerate(rows):
        photo = photos[row['id']]
        row['file'] = f'{i:02d}_{row["id"]}'+Path(photo['file']).suffix
        shutil.copy2(a.job/photo['file'], a.output/row['file'])
        row['sha256'] = sha256(a.output/row['file'])
    save_json(a.output/'subset_selection.json', dict(source_job=str(a.job), photos=rows,
        selection_uses_previous_pose_estimates=True, reconstruction_must_recompute_from_subset=True,
        upper_photos=0, requested_count=a.count))
    print(str(a.output), flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--job',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--count',type=int,default=18)
    main(p.parse_args())
