"""Publish a gray mesh while an isolated CPU color-calibration worker runs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.common import ROOT, HIDDEN, read_json, save_json, sha256, inside, subprocess_environment

PRINT_LOCK=threading.Lock()


def emit(prefix, value):
    with PRINT_LOCK:
        print(prefix, value, flush=True)


def stream(argv, prefix):
    proc=subprocess.Popen(list(map(str,argv)),cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          encoding='utf-8',errors='replace',creationflags=HIDDEN,env=subprocess_environment())
    try:
        for line in proc.stdout:emit(prefix,line.strip())
    finally:
        proc.stdout.close()
    code=proc.wait()
    if code:raise RuntimeError(f'{Path(argv[1]).name} 退出码 {code}')


def calibrate(job, run, cfg):
    started=time.monotonic();inputs=run.parent/'inputs'
    if (inputs/'calibration/result.json').is_file():
        from studio.foreground_boundary import FOREGROUND_VERSION
        if read_json(inputs/'calibration/result.json').get('foreground_version') == FOREGROUND_VERSION:
            return dict(status='ready',detail='使用本次灰模保存的相机配准',seconds=0)
        # Only archive the new run's copied snapshot; keep prior runs unchanged.
        prior = inside(inputs, 'calibration')
        archived = inside(inputs, 'calibration_before_foreground_update')
        if archived.exists():
            raise ValueError('旧配准备份已存在，请开始新的运行')
        prior.rename(archived)
    work=run.parent/'registration'
    try:
        if not cfg.get('colmap') or not Path(cfg['colmap']).is_file():
            raise ValueError('缺少 COLMAP；灰模不受影响，照片贴图需配置配准组件')
        selection=read_json(inputs/'views.json')['original_selection']
        photos=read_json(inputs/'photos.json')
        work.mkdir()
        save_json(work/'photos.json',photos)
        save_json(work/'selection.json',selection)
        save_json(work/'background_regions.json',read_json(inputs/'background_regions.json'))
        for row in photos['photos']:
            source=inside(job,row['file']);target=inside(work,row['file'])
            if sha256(source)!=row['sha256']:raise ValueError('原片校验失败：'+row['id'])
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        argv=[cfg['segment_python'],ROOT/'tools/select_directions.py','--job',work,
              '--model',cfg['segmentation_model'],'--colmap',cfg['colmap'],'--preserve-selection','--cpu','--texture-only']
        if cfg.get('turntable_calibration',False):
            argv+=['--turntable','--order-direction',cfg.get('capture_order_direction','left')]
        if cfg.get('matching_model'):argv+=['--matching-model',cfg['matching_model']]
        stream(argv,'CAL_LOG')
        # Publish the result marker only after all immutable camera/mask files exist.
        for name in ('sparse','masks','result.json'):
            source=work/'direction'/name;target=inputs/'calibration'/name
            target.parent.mkdir(parents=True,exist_ok=True)
            if source.is_dir():shutil.copytree(source,target)
            else:shutil.copy2(source,target)
        for name in ('pose_audit.json','sfm_audit.json','matching_preview.png'):
            if (work/'direction'/name).exists():shutil.copy2(work/'direction'/name,inputs/'calibration'/name)
        direction=read_json(inputs/'calibration/result.json');audit=direction.get('pose_audit')
        return dict(status='ready',detail=(f"{audit['registered_images']}/{audit['attempted_images']} 张通过检查，仅使用可靠视角上色" if audit else '照片贴图相机配准通过'),
                    audit=audit,audit_file=(inputs/'calibration/pose_audit.json').relative_to(job).as_posix() if inputs.is_relative_to(job) and (inputs/'calibration/pose_audit.json').exists() else None,
                    preview_file=(inputs/'calibration/matching_preview.png').relative_to(job).as_posix() if inputs.is_relative_to(job) and (inputs/'calibration/matching_preview.png').exists() else None,
                    seconds=round(time.monotonic()-started,2))
    except Exception as exc:
        error=read_json(work/'direction/error.json')['error'] if (work/'direction/error.json').exists() else str(exc)
        audit=read_json(work/'direction/pose_audit.json') if (work/'direction/pose_audit.json').exists() else None
        if audit is None and (work/'direction/sfm_audit.json').exists():
            models=read_json(work/'direction/sfm_audit.json')['models']
            best=max(models,key=lambda m:m['registered_images'],default={})
            rows=read_json(inputs/'photos.json')['photos']
            registered=set(best.get('registered_ids',[]))
            audit=dict(method='failed SfM model evidence',registered_images=0,attempted_images=len(rows),
                registered_before_validation=len(registered),full_ring=False,
                views=[dict(id=r['id'],accepted=False,reason='已配准但几何检查未通过' if r['id'] in registered else '未配准') for r in rows],
                validation_note=f"求解器最多配上 {len(registered)} 张，但未通过几何检查，未用于上色。")
        return dict(status='failed',detail=error,audit=audit,seconds=round(time.monotonic()-started,2))


def shape(job,run,cfg):
    started=time.monotonic()
    stream([cfg['ai_python'],ROOT/'tools/generate_shape.py','--weights',cfg['weights'],
            '--inputs',run.parent/'inputs','--output',run,'--preview-dir',job/'processing'],'SHAPE_LOG')
    stream([cfg['ai_python'],ROOT/'tools/export_model.py','--run',run],'SHAPE_LOG')
    report=read_json(run/'delivery/model_report.json')
    for asset in report['artifacts'].values():
        if sha256(run/'delivery'/asset['file'])!=asset['sha256']:raise ValueError('灰模输出校验失败')
    return dict(status='ready',seconds=round(time.monotonic()-started,2))


def run_workers(shape_fn, calibration_fn, on_result):
    """Calibration errors are results, never a gate on successful shape output."""
    results={}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(calibration_fn):'calibration'}
        if shape_fn:futures[pool.submit(shape_fn)]='shape'
        for future in as_completed(futures):
            key=futures[future]
            try:result=future.result()
            except Exception as exc:result=dict(status='failed',detail=str(exc))
            results[key]=result;on_result(key,result)
    return results


def main(args):
    import json
    cfg=read_json(args.config);args.run.parent.mkdir(parents=True,exist_ok=True)
    if not args.skip_calibration:
        emit('CAL_STATE',json.dumps(dict(status='running',detail='使用 CPU 计算照片位姿；不阻塞灰模发布'),ensure_ascii=False))
    def result(key,value):
        if key=='calibration':
            save_json(args.run.parent/'calibration_status.json',value)
            emit('CAL_STATE',json.dumps(value,ensure_ascii=False))
        elif value['status']=='ready':emit('GRAY_READY',json.dumps(value))
        else:emit('GRAY_ERROR',value['detail'])
    results=run_workers(None if args.calibration_only else lambda:shape(args.job,args.run,cfg),
                        (lambda:dict(status='skipped',detail='本次未计算照片配准；上色时再计算',seconds=0)) if args.skip_calibration else lambda:calibrate(args.job,args.run,cfg),result)
    save_json(args.run.parent/'parallel_report.json',results)
    if results.get('shape',{}).get('status')=='failed':raise RuntimeError(results['shape']['detail'])


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True,type=Path)
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--calibration-only',action='store_true')
    parser.add_argument('--skip-calibration',action='store_true')
    main(parser.parse_args())
