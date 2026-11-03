from __future__ import annotations
import argparse
import atexit
import base64
import concurrent.futures
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs
from studio.common import ROOT, HIDDEN, config, read_json, save_json, inside, subprocess_environment
from studio.capture import CaptureService
from studio.hardware import detect
from studio.jobs import JobManager
from studio.capture_import import CaptureImporter


class Application:
    def __init__(self, cfg, offline_start=False):
        self.cfg = cfg
        self.token = secrets.token_urlsafe(32)
        self.receiver = CaptureService(cfg)
        self.jobs = JobManager(cfg)
        self.jobs.recover()
        self.capture_importer = CaptureImporter(cfg, self.receiver, self.jobs)
        self.capture_importer.start()
        self.check_lock = threading.Lock()
        self.check = dict(running=False, ready=False, checks=[])
        self.gpu = dict(ready=False, checking=True)
        if offline_start:
            self.check = dict(running=False, ready=False, checks=[dict(name='离线启动',ok=False,
                message='本次跳过硬件检测，未打开串口或接收端口。固件写入结束后可点击重新检测。')])
        else:
            self.check_connection()
        threading.Thread(target=self.check_runtime, daemon=True).start()

    def check_connection(self):
        if not self.check_lock.acquire(blocking=False):
            return
        self.check = dict(running=True, ready=False, checks=[])
        def work():
            try:
                self.check = dict(detect(self.cfg, self.receiver), running=False)
            except Exception as exc:
                self.check = dict(running=False, ready=False, error=str(exc), checks=[])
            finally:
                self.check_lock.release()
        threading.Thread(target=work, daemon=True).start()

    def check_runtime(self):
        try:
            for key in ('python', 'ai_python', 'segment_python', 'weights', 'segmentation_model'):
                if not Path(self.cfg[key]).is_file():
                    raise ValueError('缺少运行文件：' + key + '。请运行安装脚本或配置本机运行环境。')
            result = subprocess.run([self.cfg['ai_python'], '-c',
                "import torch,json;print(json.dumps({'cuda':torch.cuda.is_available(),'gpu':torch.cuda.get_device_name() if torch.cuda.is_available() else None}))"],
                capture_output=True, encoding='utf-8', timeout=45, creationflags=HIDDEN,
                env=subprocess_environment())
            if result.returncode:
                raise ValueError('AI Python 环境检查失败：' + result.stderr[-800:])
            info = json.loads(result.stdout.strip().splitlines()[-1])
            self.gpu = dict(ready=info['cuda'], checking=False, name=info['gpu'],
                            error=None if info['cuda'] else '未检测到可用的 NVIDIA CUDA GPU')
        except Exception as exc:
            self.gpu = dict(ready=False, checking=False, error=str(exc))

    def state(self):
        return dict(app='FPGA 建模工作台', connection=self.check, gpu=self.gpu,
                    capture_import=self.capture_importer.snapshot(), automatic_directions_available=bool(self.cfg.get('colmap')),
                    receiver=self.receiver.snapshot(), jobs=self.jobs.list(), active=self.jobs.active,
                    settings={k: self.cfg[k] for k in ('board_ip', 'board_target_ip', 'udp_port',
                              'serial_port', 'serial_baud', 'require_serial')},
                    has_previous=Path(self.cfg.get('previous_delivery', 'NONEXISTENT')).is_dir())

    def command(self, route, body):
        if route == '/api/check':
            self.check_connection()
            return {}
        if route == '/api/settings':
            if self.receiver.snapshot()['running'] or self.check_lock.locked():
                raise ValueError('请等检测完成并停止接收后再保存通信设置')
            new = {k: body[k] for k in ('board_ip', 'board_target_ip', 'udp_port', 'serial_port',
                                       'serial_baud', 'require_serial')}
            for key in ('board_ip', 'board_target_ip'):
                ipaddress.IPv4Address(new[key])
            if type(new['udp_port']) is not int or not 1024 <= new['udp_port'] <= 65535:
                raise ValueError('UDP 端口需在 1024～65535 之间')
            if new['serial_baud'] not in (9600, 57600, 115200):
                raise ValueError('不支持此串口波特率')
            if type(new['require_serial']) is not bool or not isinstance(new['serial_port'], str):
                raise ValueError('通信参数格式错误')
            self.cfg.update(new)
            local = ROOT / 'config/local.json'
            existing = read_json(local) if local.exists() else {}
            existing.update(new)
            save_json(local, existing)
            self.check_connection()
            return {}
        if route == '/api/receiver/start':
            if self.check.get('running') or not self.check.get('ready'):
                raise ValueError('连通检查未通过，请修复红色项目后重新检测')
            if time.time() - self.check.get('checked_at', 0) > 60:
                self.check_connection()
                raise ValueError('连接状态已过期，正在重新检测；检测通过后再开始接收')
            self.receiver.start()
            return {}
        if route == '/api/receiver/stop':
            self.receiver.stop()
            return {}
        if route == '/api/receiver/import':
            state = self.receiver.snapshot()
            if state.get('capture', {}).get('state') != 'complete':
                raise ValueError('本轮尚未完整接收，不允许作为完整采集导入')
            return dict(id=self.jobs.new_import(state['capture_path']))
        if route == '/api/import':
            return dict(id=self.jobs.new_import(body['path']))
        if route == '/api/browse':
            r = subprocess.run([self.cfg['python'], str(ROOT / 'tools/choose_folder.py')],
                capture_output=True, encoding='utf-8', timeout=180, creationflags=HIDDEN)
            if r.returncode:
                raise RuntimeError('文件夹选择窗口无法打开，可以直接粘贴照片路径')
            return dict(path=r.stdout.strip())
        if route == '/api/segment':
            self.jobs.segment(body['id'], body['views'], body.get('boxes', {}))
            return {}
        if route == '/api/exclude-region':
            self.jobs.exclude_region(body['id'], body['photo_id'], body.get('box'))
            return {}
        if route == '/api/mask':
            self.jobs.save_mask(body['id'], body['direction'], body['png'])
            return {}
        if route == '/api/generate':
            if not self.gpu.get('ready'):
                raise ValueError('GPU 运行环境不可用，请查看顶部错误信息')
            self.jobs.generate(body['id'], body.get('reviewed'), body.get('with_texture',True),body.get('prepare_color',True))
            return {}
        if route == '/api/pipeline':
            if not self.gpu.get('ready'):
                raise ValueError('GPU 环境尚未就绪')
            self.jobs.pipeline(body['id'], body.get('views'), body.get('boxes'), body.get('mode', 'auto'), body.get('with_texture',True),body.get('prepare_color',True))
            return {}
        if route == '/api/color':
            self.jobs.color_existing(body['id'])
            return {}
        if route == '/api/cancel':
            self.jobs.cancel()
            return {}
        if route == '/api/open':
            p = self.jobs.path(body['id'])
            state = self.jobs.state(body['id'])
            target = p / state['result'] if state.get('result') else p
            os.startfile(str(target))
            return {}
        raise ValueError('未知操作')


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_bytes(self, data, mime, code=200):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json(self, data, code=200):
            self.send_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', code)

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{app.cfg["port"]}', f'localhost:{app.cfg["port"]}')

        def do_GET(self):
            if not self.valid_host():
                self.json({'error': '非法主机名'}, 403)
                return
            route = unquote(urlparse(self.path).path)
            try:
                if route == '/api/health':
                    self.json(dict(app='FPGA 建模工作台', ready=True, pid=os.getpid(), root=str(ROOT),features=['independent_gray','parallel_color_calibration']))
                    return
                if route == '/api/state':
                    self.json(app.state())
                    return
                if route == '/api/job':
                    id_ = parse_qs(urlparse(self.path).query)['id'][0]
                    p = app.jobs.path(id_)
                    result = dict(state=app.jobs.state(id_))
                    for name in ('photos', 'selection'):
                        if (p / (name + '.json')).exists():
                            result[name] = read_json(p / (name + '.json'))
                    self.json(result)
                    return
                if route == '/':
                    page = (ROOT / 'web/index.html').read_text(encoding='utf-8').replace('__TOKEN__', app.token)
                    self.send_bytes(page.encode('utf-8'), 'text/html; charset=utf-8')
                    return
                if route.startswith('/job-file/'):
                    _, _, id_, relative = route.split('/', 3)
                    p = inside(app.jobs.path(id_), relative)
                elif route.startswith('/previous/'):
                    allowed = {'model_colored.glb', 'model_untextured.glb', 'model_lod24k.glb'}
                    name = route.rsplit('/', 1)[-1]
                    if name not in allowed or not app.cfg.get('previous_delivery'):
                        raise ValueError('文件不可用')
                    p = Path(app.cfg['previous_delivery']) / name
                elif route == '/vendor/model-viewer.min.js':
                    p = ROOT / 'vendor/viewer/model-viewer.min.js'
                elif route in ('/app.js', '/style.css'):
                    p = ROOT / 'web' / route[1:]
                else:
                    self.send_error(404)
                    return
                if not p.is_file() or p.stat().st_size > 100 * 1024 * 1024:
                    self.send_error(404)
                    return
                mime = {'.js': 'text/javascript', '.glb': 'model/gltf-binary'}.get(p.suffix, mimetypes.guess_type(p.name)[0])
                self.send_bytes(p.read_bytes(), mime or 'application/octet-stream')
            except (ValueError, KeyError, FileNotFoundError) as exc:
                self.json({'error': str(exc)}, 400)

        def do_POST(self):
            origin = self.headers.get('Origin')
            allowed = {f'http://127.0.0.1:{app.cfg["port"]}', f'http://localhost:{app.cfg["port"]}'}
            if (not self.valid_host() or self.headers.get('X-Studio-Token') != app.token or
                    (origin and origin not in allowed)):
                self.json({'error': '请求验证失败，请重新打开本机页面'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length', 0))
                if not 0 < n <= 48 * 1024 * 1024:
                    raise ValueError('请求大小不正确')
                body = json.loads(self.rfile.read(n))
                self.json(app.command(urlparse(self.path).path, body))
            except Exception as exc:
                self.json({'error': str(exc)}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline-start', action='store_true', help='Skip startup hardware probes during firmware maintenance')
    parser.add_argument('--port', type=int, help='Use a separate local verification instance')
    parser.add_argument('--data-dir', type=Path, help='Separate data folder for an isolated verification instance')
    args = parser.parse_args()
    cfg = config()
    if args.port is not None:
        if not 1024 <= args.port <= 65535: raise ValueError('Invalid port')
        cfg['port'] = args.port
    if args.data_dir is not None:
        cfg['data_dir'] = str(args.data_dir.resolve())
    # Bind before creating state: a second launch must not "recover" live jobs.
    server = ThreadingHTTPServer(('127.0.0.1', cfg['port']), BaseHTTPRequestHandler)
    app = Application(cfg, offline_start=args.offline_start)
    server.RequestHandlerClass = handler(app)
    atexit.register(app.receiver.stop)
    save_json(Path(cfg['data_dir']) / 'server.json', dict(pid=os.getpid(), port=cfg['port'], script=str(Path(__file__).resolve())))
    print('STUDIO_READY', cfg['port'], flush=True)
    try:
        server.serve_forever()
    finally:
        app.capture_importer.stop_event.set()
        app.jobs.cancel()
        app.receiver.stop()
        server.server_close()


if __name__ == '__main__':
    main()
