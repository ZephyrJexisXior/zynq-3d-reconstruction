from __future__ import annotations
import base64
import copy
from datetime import datetime
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from PIL import Image, ImageOps
from .common import ROOT, HIDDEN, DIRECTIONS, save_json, read_json, sha256, subprocess_environment
from .ingest import import_folder, validate_views
from .process_tree import ProcessTree
from .processing import save_image_preview, write_stages


class JobManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = Path(cfg['data_dir']) / 'jobs'
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = threading.RLock()
        self.active = None
        self.proc = None
        self.process_tree = None
        self.cancel_event = threading.Event()

    def path(self, id_):
        if not isinstance(id_, str) or not re.fullmatch(r'job_[0-9_]+_[a-f0-9]{8}', id_):
            raise ValueError('无效任务编号')
        p = self.root / id_
        if not p.is_dir():
            raise ValueError('任务不存在')
        return p

    def state(self, id_):
        with self.guard:
            return read_json(self.path(id_) / 'state.json')

    def update(self, id_, **kw):
        with self.guard:
            path = self.path(id_) / 'state.json'
            s = read_json(path)
            s.update(kw, updated=time.time())
            save_json(path, s)

    def list(self):
        with self.guard:
            return [read_json(p / 'state.json') for p in sorted(self.root.glob('job_*'), reverse=True)
                    if (p / 'state.json').is_file()][:100]

    def recover(self):
        for s in self.list():
            if s['status'] == 'running':
                self.update(s['id'], status='interrupted', error='程序上次被关闭；该任务没有完成，请重新运行。')

    def start_task(self, id_, kind, action):
        with self.guard:
            if self.active:
                raise ValueError('已有任务正在处理，请等待或先取消')
            self.active = id_
            self.cancel_event.clear()
            self.update(id_, status='running', phase=kind, detail=kind, progress=None, error=None, started=time.time(),
                        elapsed=None, log=[], can_generate=False)
        def run():
            started = time.monotonic()
            try:
                action()
                if self.cancel_event.is_set():
                    raise InterruptedError('任务已取消')
            except InterruptedError as exc:
                self.update(id_, status='cancelled', phase='已取消', error=str(exc), progress=None)
            except Exception as exc:
                self.update(id_, status='failed', phase='处理失败', error=str(exc), progress=None)
                self.log(id_, repr(exc))
            finally:
                self.update(id_, elapsed=round(time.monotonic() - started, 2))
                with self.guard:
                    self.proc = None
                    self.active = None
        threading.Thread(target=run, daemon=True).start()

    def log(self, id_, line):
        line = line.strip()[-1500:]
        if not line:
            return
        with self.guard:
            p = self.path(id_)
            with (p / 'run.log').open('a', encoding='utf-8') as f:
                f.write(line + '\n')
            s = self.state(id_)
            self.update(id_, log=(s.get('log', []) + [line])[-25:])

    @staticmethod
    def inference_step(line):
        # tqdm can prepend its progress display on the same console line.
        match = re.search(r'\bDIFFUSION_STEP\s+(\d+)\b', line)
        return min(int(match.group(1)), 8) if match else None

    def process(self, id_, argv, parser):
        if self.cancel_event.is_set():
            raise InterruptedError('任务已取消')
        self.log(id_, '启动：' + Path(argv[1]).name)
        with self.guard:
            self.proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', env=subprocess_environment(), creationflags=HIDDEN)
            proc = self.proc
            try:
                self.process_tree = ProcessTree(proc)
            except Exception:
                proc.terminate()
                proc.wait()
                proc.stdout.close()
                raise
        try:
            for line in proc.stdout:
                self.log(id_, line)
                if not self.cancel_event.is_set():
                    parser(line.strip())
        except BaseException:
            with self.guard:
                if self.process_tree:
                    self.process_tree.close()
                    self.process_tree = None
            if proc.poll() is None:
                proc.terminate()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
            code = proc.wait()
            with self.guard:
                if self.process_tree:
                    self.process_tree.close()
                    self.process_tree = None
                self.proc = None
        if self.cancel_event.is_set():
            raise InterruptedError('任务已取消；已保留原片和日志')
        if code:
            raise RuntimeError(f'处理进程退出码 {code}，请查看下方日志')

    def cancel(self):
        with self.guard:
            self.cancel_event.set()
            if self.process_tree:
                self.process_tree.close()
                self.process_tree = None
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()  # Only this application's currently owned worker.

    def new_import(self, source, capture_key=None):
        with self.guard:
            if self.active:
                raise ValueError('已有任务运行中')
            id_ = datetime.now().strftime('job_%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
            p = self.root / id_
            p.mkdir()
            save_json(p / 'state.json', dict(id=id_, title=Path(source).name, status='new', created=time.time(),
                                           source=str(Path(source).resolve()), capture_key=capture_key))
            def action():
                report = import_folder(source, p,
                    lambda phase, value, detail: self.update(id_, phase=phase, progress=value, detail=detail),
                    self.cancel_event.is_set)
                sample = next(r for r in report['photos'] if r['id']==report['views']['front'])
                with Image.open(p/sample['file']) as image:
                    save_image_preview(image, p/'processing/received.png')
                self.update(id_, status='photos_ready', phase='照片已就绪，可以一键建模', progress=1,
                            photo_count=len(report['photos']), camera_counts=report['camera_counts'],
                            stage_images=[dict(key='received', label='接收原片', file='processing/received.png',
                                detail='正面参考 '+sample['id']+'；FPGA 采集默认使用主摄倒数第二张，可手动调整。')])
            self.start_task(id_, '导入照片', action)
            return id_

    def refresh_stages(self, id_):
        p = self.path(id_)
        path = p/'processing/stages.json'
        if path.exists():
            self.update(id_, stage_images=read_json(path)['stages'], preview_version=time.time())

    def segment_action(self, id_):
        p = self.path(id_)
        def parse(line):
            if line.startswith('SEGMENT_LOADING'):
                self.update(id_, phase='处理图片：提取主体', progress=None)
            if line.startswith('SEGMENT_DONE '):
                count = int(line.split()[1])
                self.update(id_, phase='处理图片：去除背景', progress=count/4, detail=f'{count}/4 个建模视角')
            if line == 'PREVIEW_READY':
                self.refresh_stages(id_)
            if line.startswith('SUBJECT_PREFLIGHT '):
                _,done,total=line.split()
                self.update(id_,phase='分离主体与转台',progress=int(done)/int(total),detail='仅处理前景，不计算相机位姿')
        argv=[self.cfg['segment_python'], str(ROOT/'tools/segment_views.py'),
              '--job', str(p), '--model', self.cfg['segmentation_model']]
        if self.cfg.get('exclude_turntable'):argv+=['--exclude-turntable']
        self.process(id_, argv, parse)
        self.refresh_stages(id_)

    def segment(self, id_, views, boxes):
        p = self.path(id_)
        with self.guard:
            if self.active:
                raise ValueError('已有任务运行中')
            validate_views(p, views)
            previous = read_json(p/'selection.json') if (p/'selection.json').exists() else {}
            reference_box = (boxes or {}).get('front')
            if not reference_box and previous.get('views',{}).get('front') == views.get('front'):
                reference_box = previous.get('reference_box')
            save_json(p/'selection.json', dict(views=views, boxes=boxes, reference_box=reference_box,
                      reviewed=False, direction_source='user_selected'))
            def action():
                self.segment_action(id_)
                self.update(id_, status='masks_ready', phase='前景已准备好，可直接建模或修正', progress=1, can_generate=True)
            self.start_task(id_, '生成前景预览', action)

    def save_mask(self, id_, direction, data):
        with self.guard:
            if self.active:
                raise ValueError('任务运行中不能修改输入')
            if direction not in DIRECTIONS:
                raise ValueError('无效方向')
            p = self.path(id_)
            selected = validate_views(p, read_json(p / 'selection.json')['views'])
            original = ImageOps.exif_transpose(Image.open(p / selected[direction]['file'])).convert('RGBA')
            payload = base64.b64decode(data.split(',', 1)[1], validate=True)
            with Image.open(io.BytesIO(payload)) as mask:
                mask.load()
                if mask.size != original.size:
                    raise ValueError('掩码与原照片尺寸不一致')
                alpha = mask.convert('RGBA').getchannel('A')
                if not alpha.getbbox():
                    raise ValueError('前景不能为空')
                original.putalpha(alpha)
                original.save(p / 'masks' / f'{direction}.png')
            s = read_json(p / 'selection.json')
            s['reviewed'] = False
            s['mask_user_edited'] = True
            save_json(p / 'selection.json', s)
            self.update(id_, status='masks_ready', can_generate=True)

    def calibration_ready(self, id_):
        from .foreground_boundary import FOREGROUND_VERSION
        p = self.path(id_)
        try:
            selection = read_json(p/'selection.json')
            result = read_json(p/'direction/result.json')
            if result.get('foreground_version') != FOREGROUND_VERSION:
                return False
            exclusions = read_json(p/'background_regions.json') if (p/'background_regions.json').exists() else {}
            if result.get('background_regions', {}) != exclusions:
                return False
            if result.get('selection_signature') != {k:selection.get(k) for k in ('views','boxes','reference_box')}:
                return False
            for name, digest in result['camera_files_sha256'].items():
                if sha256(p/'direction/sparse'/name) != digest:
                    return False
            photos = {r['id']:r for r in read_json(p/'photos.json')['photos']}
            for pid, digest in result['source_photo_sha256'].items():
                if sha256(p/photos[pid]['file']) != digest or not (p/'direction/masks'/(pid+'.png')).exists():
                    return False
            return set(selection['views'].values()).issubset(result['source_photo_sha256'])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def exclude_region(self, id_, photo_id, box):
        with self.guard:
            if self.active:
                raise ValueError('任务运行中不能修改背景范围')
            p = self.path(id_)
            if photo_id not in {r['id'] for r in read_json(p/'photos.json')['photos']}:
                raise ValueError('照片不属于本轮任务')
            if box is not None and (not isinstance(box,list) or len(box)!=4 or
                    not all(type(v) in (int,float) for v in box) or
                    not (0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1)):
                raise ValueError('无效的背景排除框')
            regions = read_json(p/'background_regions.json') if (p/'background_regions.json').exists() else {}
            if box is None:
                regions.pop(photo_id,None)
            else:
                regions.setdefault(photo_id,[]).append(box)
            save_json(p/'background_regions.json', regions)
            self.update(id_, can_generate=False, phase='背景范围已更新，请重新处理图片')

    def direction_action(self, id_, reference_box=None, reference_photo=None, preserve_selection=False):
        p = self.path(id_)
        photos = read_json(p/'photos.json')
        if not preserve_selection and photos.get('views') and set(photos['views']) == set(DIRECTIONS):
            validate_views(p, photos['views'])
            save_json(p/'selection.json', dict(views=photos['views'], boxes={}, reviewed=False,
                direction_source='input_views_manifest', first_is_front=True))
            preserve_selection = True
        colmap = self.cfg.get('colmap', '')
        if not colmap or not Path(colmap).is_file():
            self.update(id_, status='needs_directions', phase='缺少相机配准组件，暂不能生成照片贴图',
                        progress=None, error='请配置 COLMAP；手动选四方向也需要相机配准才能上色。')
            return False
        def parse(line):
            if line == 'DIRECTION_PREVIEW_READY':
                self.refresh_stages(id_)
            elif line.startswith('DIRECTION_PREFLIGHT '):
                _,done,total=line.split()
                self.update(id_,phase='检查主体与转台范围',progress=int(done)/int(total),
                            detail=f'检查 {done}/{total} 个视角；主体框仅用于本轮主摄')
            elif line.startswith('DIRECTION_SUBJECT_ROI '):
                self.update(id_,detail='已根据本轮多个视角分离宽底座；请在前景预览核对物体边缘')
            elif line.startswith('DIRECTION_MASK '):
                _, done, total = line.split()
                self.update(id_, phase='自动识别方向：提取本轮主体特征', progress=int(done)/int(total),
                            detail=f'已准备 {done}/{total} 张候选照片；编号不作为角度')
            elif line.startswith('DIRECTION_MATCH '):
                _, done, total = line.split()
                self.update(id_, phase='自动识别方向：预训练模型匹配照片', progress=int(done)/int(total),
                            detail=f'已匹配 {done}/{total} 对候选照片；寻找对应点，不进行模型训练')
            elif line.startswith('DIRECTION_STAGE '):
                label = line.split()[1]
                names = {'features':'提取特征', 'matching':'匹配照片', 'poses':'计算相机方向'}
                self.update(id_, phase='自动识别方向：'+names.get(label, '检查配准结果'), progress=None)
        try:
            argv = [self.cfg['segment_python'], str(ROOT/'tools/select_directions.py'),
                '--job', str(p), '--model', self.cfg['segmentation_model'], '--colmap', colmap]
            if self.cfg.get('matching_model'):
                argv += ['--matching-model', self.cfg['matching_model']]
            if reference_box:
                argv += ['--reference-box', json.dumps(reference_box)]
            if reference_photo:
                argv += ['--reference-photo', reference_photo]
            if preserve_selection:
                argv += ['--preserve-selection']
            if self.cfg.get('exclude_turntable', False):
                argv += ['--exclude-turntable']
            self.process(id_, argv, parse)
        except InterruptedError:
            raise
        except Exception as exc:
            reason = str(exc)
            if (p/'direction/error.json').exists():
                reason = read_json(p/'direction/error.json')['error']
            self.update(id_, status='needs_directions', phase='相机配准未通过，尚未开始 GPU 建模',
                        progress=None, error=reason, detail=reason)
            return False
        report = read_json(p/'direction/result.json')
        self.update(id_, direction_note=report['reference_note'], direction_report=report,
                    direction_version=time.time())
        return True

    def pipeline(self, id_, views=None, boxes=None, mode='auto', with_texture=True, prepare_color=True):
        from .foreground_boundary import FOREGROUND_VERSION
        p = self.path(id_)
        with self.guard:
            if self.active:
                raise ValueError('已有任务运行中')
            if mode not in ('auto', 'manual', 'calibrated'):
                raise ValueError('无效方向模式')
            if type(with_texture) is not bool:
                raise ValueError('无效的颜色生成选项')
            if type(prepare_color) is not bool:
                raise ValueError('无效的配准选项')
            if not (p/'photos.json').exists():
                raise ValueError('照片尚未完成导入')
            if mode == 'manual':
                validate_views(p, views or {})
            existing = read_json(p/'selection.json') if (p/'selection.json').exists() else {}
            reusable = (mode == 'manual' and existing.get('views') == views and existing.get('boxes', {}) == (boxes or {})
                        and (existing.get('foreground_version') == FOREGROUND_VERSION or existing.get('mask_user_edited'))
                        and self.state(id_).get('can_generate') and all((p/'masks'/(d+'.png')).exists() for d in DIRECTIONS))
            self.update(id_, result=None, stage_images=[], direction_note=None, direction_report=None, texture_generated=False,
                        texture_warning=None, calibration=None, gray_elapsed=None)
            def action():
                if mode == 'calibrated':
                    from .ingest import default_front
                    front = (views or {}).get('front') or default_front(read_json(p/'photos.json'))['id']
                    reference_box = (boxes or {}).get('front')
                    if not reference_box and existing.get('views',{}).get('front') == front:
                        reference_box = existing.get('reference_box')
                    if not self.direction_action(id_, reference_box, reference_photo=front):
                        return
                else:
                    chosen=views
                    if mode=='auto':
                        from .quick_views import suggest_views
                        photos=read_json(p/'photos.json')
                        try:
                            chosen=photos['views'] if set(photos.get('views',{}))==set(DIRECTIONS) else suggest_views(
                                photos,(views or {}).get('front'),self.cfg.get('capture_order_direction','left'))
                        except ValueError as exc:
                            self.update(id_,status='needs_directions',phase='请确认四方向后生成灰模',error=str(exc),progress=None)
                            return
                    validate_views(p,chosen)
                    if not reusable:
                        save_json(p/'selection.json',dict(views=chosen,boxes=boxes or {},reviewed=False,
                            reference_box=(boxes or {}).get('front') or (existing.get('reference_box') if existing.get('views',{}).get('front')==chosen.get('front') else None),
                            direction_source='user_selected' if mode=='manual' else 'capture_order_estimate',
                            angles_verified=False,first_is_front=False))
                    self.update(id_,direction_version=time.time(),direction_note=(
                        '使用手选四方向先生成灰模；相机配准与形状生成分开进行。' if mode=='manual' else
                        '按主摄采集顺序预选四方向：只是假定一圈均匀转动，实际角度尚未验证；请核对卡片，可手动改选。'))
                if not reusable:
                    self.segment_action(id_)
                else:
                    selected = validate_views(p, views)
                    row = selected['front']
                    with Image.open(p/row['file']) as original, Image.open(p/'masks/front.png') as foreground:
                        write_stages(p, original, foreground, {'method':'使用本轮已检查或画笔修正的前景'}, row)
                    self.refresh_stages(id_)
                if with_texture:
                    self.generate_action(id_, reviewed=False)
                else:
                    self.generate_action(id_, reviewed=False, with_texture=False, prepare_color=prepare_color)
            self.start_task(id_, '一键建模：准备本轮照片', action)

    def generate_action(self, id_, reviewed, with_texture=True, prepare_color=True):
        p = self.path(id_)
        selected = read_json(p/'selection.json')
        validate_views(p, selected['views'])
        masks = [p/'masks'/(d+'.png') for d in DIRECTIONS]
        if any(not q.is_file() for q in masks):
            raise ValueError('四个方向的前景尚未准备完成')
        if self.cancel_event.is_set():
            raise InterruptedError('任务已取消')
        run_id = datetime.now().strftime('run_%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:6]
        inputs = p/run_id/'inputs'
        inputs.mkdir(parents=True)
        if self.calibration_ready(id_):
            calibration=inputs/'calibration'
            calibration.mkdir()
            shutil.copy2(p/'direction/result.json',calibration/'result.json')
            for name in ('sparse','masks'):
                shutil.copytree(p/'direction'/name,calibration/name)
        shutil.copy2(p/'photos.json',inputs/'photos.json')
        save_json(inputs/'background_regions.json',read_json(p/'background_regions.json') if (p/'background_regions.json').exists() else {})
        for q in masks:
            shutil.copy2(q, inputs/q.name)
        save_json(inputs/'views.json', dict(views={d:d+'.png' for d in DIRECTIONS},
            directions_selected_in_interface=selected.get('direction_source') == 'user_selected',
            first_is_front_by_user_capture_convention=bool(selected.get('first_is_front')), masks_reviewed=bool(reviewed), original_selection=selected,
            mask_sha256={q.name:sha256(q) for q in masks}))
        result = p/run_id/'shape'
        self.update(id_, result=None, run_id=run_id, calibration=None, gray_elapsed=None)
        def parse(line):
            if line.startswith('CAL_STATE '):
                self.update(id_,calibration=json.loads(line[len('CAL_STATE '):]))
                return
            if line.startswith('CAL_LOG '):
                progress=None;detail=None
                match=re.search(r'DIRECTION_(MASK|MATCH) (\d+) (\d+)',line)
                if match:
                    progress=int(match[2])/int(match[3]);detail=('提取前景' if match[1]=='MASK' else '匹配照片')+f' {match[2]}/{match[3]}'
                elif 'DIRECTION_STAGE ' in line:detail='计算照片对应点与相机位置'
                if detail:self.update(id_,calibration=dict(status='running',detail=detail,progress=progress))
                return
            if line.startswith('GRAY_READY '):
                self.update(id_,result=(result/'delivery').relative_to(p).as_posix(),texture_generated=False,
                    gray_elapsed=round(time.time()-self.state(id_)['started'],2),phase='灰模已可查看，正在结束照片贴图配准',
                    progress=1,detail='下方灰模可旋转和下载；配准失败也会保留灰模')
                return
            if line.startswith('SHAPE_LOG '):line=line[len('SHAPE_LOG '):]
            if line == 'VERIFY_WEIGHTS':
                self.update(id_, phase='GPU 建模：校验 AI 权重', progress=None)
            elif line == 'LOAD_VERIFIED_MODEL':
                self.update(id_, phase='GPU 建模：加载模型', progress=None)
            elif line == 'AI_INPUT_READY':
                self.refresh_stages(id_)
            elif self.inference_step(line) is not None:
                step = self.inference_step(line)
                self.update(id_, phase='GPU 建模：生成形状', progress=step/8, detail=f'已完成 {step}/8 步推理')
                if step == 8:
                    self.update(id_, phase='GPU 建模：提取表面网格', progress=None, detail='等待网格解码完成')
            elif line == 'MESH_EXTRACTED':
                self.update(id_, phase='简化、导出和重新读取灰模', progress=None)
        save_json(p/run_id/'execution.json',self.cfg)
        argv=[self.cfg['python'],str(ROOT/'tools/build_gray.py'),'--job',str(p),
              '--run',str(result),'--config',str(p/run_id/'execution.json')]
        if not (prepare_color or with_texture):argv+=['--skip-calibration']
        self.process(id_,argv,parse)
        calibration_state=read_json(p/run_id/'calibration_status.json')
        if calibration_state['status']=='failed':
            self.update(id_,texture_warning='灰模已保留；照片贴图配准未通过：'+calibration_state['detail'])
        if with_texture and calibration_state['status']=='ready':
            self.texture_action(id_, result)
        report = read_json(result/'delivery/model_report.json')
        for asset in report['artifacts'].values():
            if sha256(result/'delivery'/asset['file']) != asset['sha256']:
                raise ValueError('输出模型校验失败')
        colored = report.get('texture_generated', False)
        gray_phase={'ready':'灰模完成，可以生成彩色模','failed':'灰模完成；照片贴图配准未通过','skipped':'灰模完成；上色时再计算照片配准'}[calibration_state['status']]
        self.update(id_, status='complete', phase='彩色建模完成，请检查结构和贴图' if colored else gray_phase, progress=1,
                    detail='灰模和彩色模均已保存，可切换对照与下载' if colored else (self.state(id_).get('texture_warning') or '先检查灰模结构，再点击“生成彩色模”，无需重新推理形状'),
                    texture_generated=colored, texture_completed=report.get('texture_completed', False),
                    result=(result/'delivery').relative_to(p).as_posix(),
                    can_generate=True)

    def texture_action(self, id_, result):
        p = self.path(id_)
        self.update(id_, texture_generated=False, texture_warning=None,detail='使用通过检查的照片投影颜色')
        def parse(line):
            if line.startswith('TEXTURE_STAGE '):
                stage = line.split()[1]
                self.update(id_, phase={'align':'贴图：全局对齐照片和模型', 'uv':'贴图：展开 UV',
                    'patches':'贴图：选择连续照片区域', 'completion':'贴图：延续缺色区域的邻近颜色'}.get(stage, '生成贴图'), progress=None)
            elif line.startswith('TEXTURE_VIEW '):
                _, done, total = line.split()
                self.update(id_, phase='贴图：检查遮挡并投影照片颜色', progress=int(done)/int(total), detail=f'已处理 {done}/{total} 个视角步骤')
            elif line in ('TEXTURE_COMPLETE', 'TEXTURE_PREVIEW_READY'):
                self.refresh_stages(id_)
        try:
            self.process(id_, [self.cfg['ai_python'], str(ROOT/'tools/texture_photos.py'), '--job', str(p), '--run', str(result)], parse)
            self.refresh_stages(id_)
        except InterruptedError:
            raise
        except Exception as exc:
            detail = read_json(result/'photo_texture/error.json').get('error') if (result/'photo_texture/error.json').exists() else str(exc)
            warning = '灰模已保留；自动贴图失败：'+detail
            self.log(id_, warning)
            self.update(id_, texture_warning=warning)
            report = read_json(result/'delivery/model_report.json')
            report.update(texture_generated=False, texture_error=warning)
            save_json(result/'delivery/model_report.json', report)

    def color_existing(self, id_):
        """Bake a new color result from an immutable gray run; preserve the source run."""
        with self.guard:
            if self.active:
                raise ValueError('已有任务运行中')
            p=self.path(id_);state=self.state(id_)
            if not state.get('result'):
                raise ValueError('请先生成灰模')
            source=(p/state['result']).resolve()
            if not source.is_relative_to(p.resolve()) or not (source/'model_untextured.glb').is_file():
                raise ValueError('没有可用的灰模')
            source_run=source.parent.parent
            if not (source_run/'inputs/views.json').exists():
                raise ValueError('灰模缺少原始输入记录，请重新生成')
            if not (source_run/'inputs/calibration/result.json').exists() and not (source_run/'inputs/photos.json').exists():
                raise ValueError('旧灰模缺少原片快照，无法独立重试照片配准，请重新生成灰模')
            report=read_json(source/'model_report.json')
            asset=report['artifacts']['model_untextured']
            if sha256(source/asset['file'])!=asset['sha256']:
                raise ValueError('灰模校验失败')
            source_result=state['result']
            def action():
                run_id=datetime.now().strftime('run_%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:6]
                destination=p/run_id
                shutil.copytree(source_run/'inputs',destination/'inputs')
                result=destination/'shape';delivery=result/'delivery';delivery.mkdir(parents=True)
                for name in ('model_untextured.glb','model_lod24k.glb','model.obj','model.ply'):
                    if (source/name).is_file():shutil.copy2(source/name,delivery/name)
                clean=copy.deepcopy(report)
                for key in ('texture_report','texture_error','default_preview'):
                    clean.pop(key,None)
                clean.update(texture_generated=False,texture_completed=False,shape_source_run=source_run.name)
                clean['artifacts']={k:v for k,v in clean['artifacts'].items() if k in ('model_untextured','model_lod24k')}
                save_json(delivery/'model_report.json',clean)
                self.log(id_,'复用已校验灰模，仅生成颜色贴图；不重新执行 AI 形状推理')
                if not (destination/'inputs/calibration/result.json').exists():
                    self.update(id_,phase='保留灰模，重新计算照片贴图配准',progress=None)
                    save_json(destination/'execution.json',self.cfg)
                    self.process(id_,[self.cfg['python'],str(ROOT/'tools/build_gray.py'),'--job',str(p),
                        '--run',str(result),'--config',str(destination/'execution.json'),'--calibration-only'],
                        lambda line:self.update(id_,calibration=json.loads(line[len('CAL_STATE '):])) if line.startswith('CAL_STATE ') else None)
                    calibration_state=read_json(destination/'calibration_status.json')
                    if calibration_state['status']!='ready':
                        self.update(id_,texture_warning='灰模已保留；照片配准未通过：'+calibration_state['detail'])
                if (destination/'inputs/calibration/result.json').exists():
                    self.texture_action(id_,result)
                colored=read_json(delivery/'model_report.json').get('texture_generated',False)
                if sha256(delivery/'model_untextured.glb')!=asset['sha256']:
                    raise ValueError('上色意外改变了灰模文件')
                if colored:
                    self.update(id_,status='complete',phase='灰模和彩色模均已保存',progress=1,
                        run_id=run_id,result=delivery.relative_to(p).as_posix(),shape_source_run=source_run.name,
                        previous_gray_result=source_result,texture_generated=True,texture_completed=True,
                        detail='颜色来自原片和缺色插值；灰模保持不变，可切换对照',can_generate=True)
                else:
                    self.update(id_,status='complete',phase='上色未通过，原模型已保留',progress=1,
                        result=source_result,texture_generated=report.get('texture_generated',False),
                        texture_completed=report.get('texture_completed',False),detail=self.state(id_).get('texture_warning'),can_generate=True)
            self.start_task(id_,'为当前灰模生成颜色贴图',action)

    def generate(self, id_, reviewed, with_texture=True, prepare_color=True):
        with self.guard:
            if self.active:
                raise ValueError('已有任务运行中')
            if reviewed is not True:
                raise ValueError('请先核对四个方向及前景完整性')
            if type(prepare_color) is not bool or type(with_texture) is not bool:raise ValueError('无效生成选项')
            self.start_task(id_, '准备 GPU 建模', lambda:self.generate_action(id_, reviewed=True, with_texture=with_texture,prepare_color=prepare_color))
