"""Read-only connection checks; no motor, capture, login or firmware commands."""
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from .common import HIDDEN


def evaluate_network(adapters, addresses, board, target):
    up = [a for a in adapters if a.get('Status') == 'Up' and a.get('HardwareInterface')
          and str(a.get('PhysicalMediaType')) in ('802.3', '14')]
    ips = [x for x in addresses if x.get('InterfaceIndex') in [a['ifIndex'] for a in up]]
    assigned = any(x.get('IPAddress') == target for x in ips)
    reachable_subnet = any(ipaddress.ip_address(board) in ipaddress.ip_network(
        f"{x['IPAddress']}/{x['PrefixLength']}", strict=False) for x in ips)
    if not up:
        return False, '有线网口未连接：检查开发板供电、网线和 USB 网卡。'
    if not assigned:
        return False, f'有线网卡未配置板端当前目标地址 {target}，无法接收发往该地址的数据。'
    if not reachable_subnet:
        return False, f'有线网卡与开发板 {board} 不在同一子网。'
    return True, f'有线网口已连接，接收地址 {target} 已配置。'


def detect(cfg, receiver):
    checks = []
    network_ok = serial_ok = board_ok = False
    try:
        ps = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
              "$a=@(Get-NetAdapter | Select-Object ifIndex,Name,Status,HardwareInterface,PhysicalMediaType); "
              "$i=@(Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceIndex,IPAddress,PrefixLength); "
              "@{adapters=$a;addresses=$i}|ConvertTo-Json -Depth 5 -Compress")
        run = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], capture_output=True,
                             timeout=25, creationflags=HIDDEN)
        if run.returncode:
            raise RuntimeError('Windows 网卡状态查询失败')
        info = json.loads(run.stdout.decode('utf-8-sig'))
        network_ok, message = evaluate_network(info['adapters'], info['addresses'],
                                                cfg['board_ip'], cfg['board_target_ip'])
        checks.append(dict(name='以太网', ok=network_ok, message=message))
        if network_ok:
            ping = subprocess.run(['ping', '-4', '-n', '1', '-w', '1200', cfg['board_ip']],
                                  capture_output=True, timeout=4, creationflags=HIDDEN)
            responded = ping.returncode == 0 and b'TTL=' in ping.stdout.upper()
            board_ok = responded
            checks.append(dict(name='开发板 IP', ok=responded,
                message='收到 ICMP 回复；图像协议仍需收到完整照片验证。' if responded else
                '未收到 ping 回复；可能未开机、地址不符或屏蔽 ICMP。'))
    except Exception as exc:
        checks.append(dict(name='以太网', ok=False, message=str(exc)))
    ports = []
    try:
        import serial
        from serial.tools import list_ports
        ports = [dict(port=p.device, description=p.description, usb=p.vid is not None)
                 for p in list_ports.comports()]
        chosen = cfg['serial_port']
        candidates = [p for p in ports if p['usb']]
        if chosen == 'auto':
            if len(candidates) != 1:
                raise ValueError('未发现唯一 USB 串口，请接入开发板 USB 串口并选择端口。')
            chosen = candidates[0]['port']
        if chosen not in [p['port'] for p in ports]:
            raise ValueError(f'串口 {chosen} 不存在')
        with serial.Serial(port=None, baudrate=cfg['serial_baud'], timeout=.25) as s:
            s.dtr = False
            s.rts = False
            s.port = chosen
            s.open()
            data = s.read(min(4096, s.in_waiting) or 1)
        serial_ok = True
        checks.append(dict(name='USB 串口', ok=True,
            message=f'{chosen} 可打开（{cfg["serial_baud"]} 波特）；只读检查，未验证板端命令应答。',
            received_bytes=len(data)))
    except Exception as exc:
        checks.append(dict(name='USB 串口', ok=False, message=str(exc)))
    state = receiver.snapshot()
    if state.get('running') and time.time() - (state.get('last_complete_at') or 0) < 30:
        board_ok = True  # A recent complete photo can independently prove the board replied.
    if state['running']:
        port_ok = True
        message = f'UDP {cfg["udp_port"]} 正在本程序监听。'
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('0.0.0.0', cfg['udp_port']))
            port_ok = True
            message = f'UDP {cfg["udp_port"]} 可绑定；尚未开启接收。'
        except OSError as exc:
            port_ok = False
            message = f'UDP {cfg["udp_port"]} 被占用或拒绝绑定：{exc}。不会自动停止其他进程。'
            try:
                port = int(cfg['udp_port'])
                query = (f"Get-NetUDPEndpoint -LocalPort {port} -ErrorAction Stop | "
                         "ForEach-Object { $p=Get-Process -Id $_.OwningProcess -ErrorAction Stop; "
                         "[PSCustomObject]@{pid=$p.Id;name=$p.ProcessName} } | ConvertTo-Json -Compress")
                owners = subprocess.run(['powershell.exe', '-NoProfile', '-Command', query],
                    capture_output=True, timeout=8, creationflags=HIDDEN)
                if owners.returncode == 0:
                    rows = json.loads(owners.stdout.decode('utf-8-sig'))
                    rows = rows if isinstance(rows, list) else [rows]
                    names = ', '.join(f"{p['name']}.exe (PID {p['pid']})" for p in rows)
                    message = f'UDP {port} 被 {names} 占用，请关闭占用程序或协调接收端口后重试。'
            except Exception:
                pass
        finally:
            sock.close()
    checks.append(dict(name='照片接收端口', ok=port_ok, message=message))
    return dict(checks=checks, ports=ports, checked_at=time.time(),
                ready=network_ok and board_ok and port_ok and (serial_ok or not cfg['require_serial']),
                message='仅当前网卡/串口检查；完整照片到达后才验证图像链路。')
