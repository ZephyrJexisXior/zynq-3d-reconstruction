"""Receive existing FPGA UDP traffic; never transmit motor/capture commands."""
from __future__ import annotations
import argparse
from collections import Counter,OrderedDict
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time
import uuid
import zlib
import binascii
import cv2
import numpy as np
# Modified 2026-11-01: package-relative import for the desktop application.
from .protocol import Assembler,quality_event

ROOT=Path(__file__).resolve().parent


def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    # Windows readers and indexers may briefly deny FILE_SHARE_DELETE.
    for attempt in range(10):
        try:tmp.replace(path);return
        except PermissionError:
            if attempt==9:raise
            time.sleep(.01*(attempt+1))


class Receiver:
    def __init__(self,output,board):
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True);self.board=board
        self.session=self.output/('session_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
        self.session.mkdir();self.assembler=Assembler();self.stop=threading.Event();self.lock=threading.Lock()
        self.quality=OrderedDict();self.samples=OrderedDict();self.seen=OrderedDict();self.records={}
        self.round=None;self.round_serial=0;self.last_jpeg=None;self.last_metadata=None
        self.sender_generation=0
        self.pending_json={}
        self.status={'state':'listening','board':board,'port':5002,'pid':os.getpid(),
          'session':str(self.session),'packets':0,'board_packets':0,'frames':0,'snapshots':0,
          'bad_packets':0,'expired_frames':0,'packet_types':{},'last_frame':None,
          'capture_started_by_receiver':False,'motor_commands_sent':False,
          'note':'等待板端发送；接收器不会触发拍照或转动。'}
        self.counts=Counter();self.flush()

    def persist_json(self,path,value):
        try:
            save_json(path,value);self.pending_json.pop(path,None)
        except OSError as exc:
            # Preserve records in memory and retry. A locked manifest must not
            # kill the UDP listener and lose the remaining photographs.
            self.pending_json[path]=value
            self.status['last_storage_error']=str(exc)
            print('JSON_WRITE_DEFERRED',path,str(exc),flush=True)

    def flush(self):
        for path,value in list(self.pending_json.items()):self.persist_json(path,value)
        with self.lock:
            self.status.update(bad_packets=self.assembler.bad_packets,expired_frames=self.assembler.expired_frames,
                               packet_types=dict(self.counts),pending_json_writes=len(self.pending_json))
            snapshot=dict(self.status)
        self.persist_json(ROOT/'status.json',snapshot)

    @staticmethod
    def cache(mapping,key,value,limit=2048):
        mapping[key]=value;mapping.move_to_end(key)
        while len(mapping)>limit:mapping.popitem(last=False)

    def round_manifest(self):
        if self.round:
            m=self.round['manifest'];frames=m['frames'];capture=m['capture']
            capture['saved_images']=len(frames)
            expected=capture.get('board_sent_images')
            plan=capture.get('plan',{})
            requested=plan.get('positions')
            indexes=[r.get('position_index') for r in frames]
            cameras=plan.get('camera_ids')
            dual=isinstance(cameras,list) and len(cameras)==2 and all(isinstance(c,str) for c in cameras) and len(set(cameras))==2
            missing=sorted(set(range(requested))-set(p for p in indexes if type(p)is int)) if type(requested)is int else None
            pairs=[(r.get('camera_id'),r.get('position_index')) for r in frames]
            required={(c,i) for c in cameras for i in range(requested)} if dual and type(requested)is int else set()
            missing_pairs=sorted(required-set(pairs)) if required else []
            capture['camera_counts']={c:sum(r.get('camera_id')==c for r in frames) for c in (cameras if dual else ['front_ov5640'])}
            if dual:capture['missing_camera_positions']=[{'camera_id':c,'position_index':i} for c,i in missing_pairs]
            capture['missing_positions']=missing
            valid=all(r['valid'] and r.get('fpga_quality',{}).get('frame_complete') is True and
                      r.get('fpga_quality',{}).get('rows')==r['image_size'][1] and
                      r.get('fpga_quality',{}).get('frame_pixels')==r['image_size'][0]*r['image_size'][1]
                      for r in frames)
            if capture.get('end_event_received'):
                success=(not capture.get('abort_event_received') and expected is not None and
                         len(frames)==expected and (requested is None or expected==requested*(2 if dual else 1)) and
                         missing==[] and valid and (not dual or (not missing_pairs and len(set(pairs))==len(frames))))
                capture['state']='complete' if success else 'incomplete'
            self.persist_json(self.round['path']/'manifest.json',m)

    def event(self,packet):
        magic=packet[:4]
        # UDP may deliver image metadata after the end marker. Keep accepting
        # it while a round is incomplete, but freeze a verified complete round.
        mutable_round=self.round and self.round['manifest']['capture']['state']!='complete'
        if magic==b'FPGQ':
            seq,q=quality_event(packet);self.cache(self.quality,seq,q)
            entry=self.records.get((self.round_serial,seq)) if mutable_round else None
            if entry:entry['fpga_quality']=q;self.round_manifest()
        elif magic==b'FPGP':
            if len(packet)>1400:raise ValueError('oversized capture metadata')
            data=json.loads(packet[4:]);kind=data.get('kind')
            if kind=='sample':
                seq=data.get('sequence')
                if type(seq)is not int:raise ValueError('invalid sample sequence')
                if type(data.get('position_index'))is not int or not 0<=data['position_index']<10000:
                    raise ValueError('invalid sample position')
                if 'camera_id' in data and not isinstance(data['camera_id'],str):
                    raise ValueError('invalid camera id')
                self.cache(self.samples,seq,data)
                entry=self.records.get((self.round_serial,seq)) if mutable_round else None
                if entry:
                    entry.update({k:v for k,v in data.items() if k not in ('file','sha256','valid','camera_id','camera_role')})
                    self.apply_camera(entry,data)
                    entry.pop('missing_sample_metadata',None);self.round_manifest()
            elif kind=='plan' and mutable_round:
                plan=data.get('plan',{})
                if not isinstance(plan,dict):raise ValueError('invalid capture plan')
                if 'positions' in plan and (type(plan['positions'])is not int or not 1<=plan['positions']<=10000):
                    raise ValueError('invalid capture size')
                if 'camera_ids' in plan:
                    cameras=plan['camera_ids']
                    if (not isinstance(cameras,list) or len(cameras)!=2 or
                        not all(isinstance(c,str) and 0<len(c)<=64 for c in cameras) or len(set(cameras))!=2):
                        raise ValueError('dual plan requires two distinct camera ids')
                self.round['manifest']['capture']['plan']=plan
                for entry in self.round['manifest']['frames']:
                    sample=self.samples.get(entry.get('sequence'))
                    if sample:self.apply_camera(entry,sample)
                self.round_manifest()
            self.last_metadata=data
        elif magic in (b'FPGK',b'FPGD',b'FPGX'):
            if len(packet)!=16:raise ValueError('invalid capture event size')
            _,trigger,duration,detail=struct.unpack('<4sIII',packet)
            if magic==b'FPGK':
                identity=(self.sender_generation,trigger,duration,detail)
                if self.round and self.round.get('identity')==identity:return
                if self.round and self.round['manifest']['capture']['state']=='capturing':
                    self.round['manifest']['capture']['state']='incomplete';self.round_manifest()
                self.round_serial+=1;self.samples.clear();self.quality.clear();self.last_metadata=None
                self.assembler.pending.clear()
                path=self.session/('capture_%03d_trigger_%d'%(self.round_serial,trigger));(path/'images').mkdir(parents=True)
                manifest={'schema':'fpga-reconstruction/v2','camera_id':'front_ov5640','frames':[],
                  'receiver':{'protocols':['FPG3','FPG4'],'source_host':self.board,'sender_generation':self.sender_generation},
                  'capture':{'state':'capturing','trigger':trigger,'planned_duration_ms':duration,
                             'start_frame':detail,'full_turn_verified':False,'angle_calibrated':False}}
                self.round={'trigger':trigger,'identity':identity,'path':path,'manifest':manifest};self.round_manifest()
                print('CAPTURE_START',path,flush=True)
            elif self.round and self.round['trigger']==trigger:
                capture=self.round['manifest']['capture'];capture.update(end_event_received=True,
                    board_sent_images=detail,abort_event_received=magic==b'FPGX')
                self.round_manifest();print('CAPTURE_END',capture['state'],'saved',capture['saved_images'],'sent',detail,flush=True)
        elif magic in (b'FPGM',b'FPGR'):
            if magic==b'FPGR':
                if len(packet)!=16:raise ValueError('invalid ROI rectangle packet')
                _,w,h,x0,y0,x1,y1=struct.unpack('<4sHHHHHH',packet)
                if not 0<=x0<x1<=w<=8192 or not 0<=y0<y1<=h<=8192 or w*h>24_000_000:raise ValueError('invalid ROI bounds')
                description={'width':w,'height':h,'roi_xyxy':[x0,y0,x1,y1],
                             'kind':'FPGA rectangular ROI; not a fine object segmentation mask'}
            else:
                if len(packet)<14:raise ValueError('invalid compressed mask')
                _,w,h,n=struct.unpack_from('<4sHHI',packet)
                if w*h>24_000_000 or len(packet)!=12+n+2:raise ValueError('mask bounds mismatch')
                payload=packet[12:-2]
                if binascii.crc_hqx(payload,0xffff)!=struct.unpack('<H',packet[-2:])[0]:raise ValueError('mask CRC mismatch')
                obj=zlib.decompressobj();decoded=obj.decompress(payload,w*h+1)
                if len(decoded)!=w*h or not obj.eof:raise ValueError('mask decoded size mismatch')
                mask=np.frombuffer(decoded,np.uint8).reshape((h,w))
                ok,encoded=cv2.imencode('.png',mask)
                if not ok:raise ValueError('cannot save ROI mask')
                (self.session/'fpga_roi.png').write_bytes(encoded.tobytes())
                description={'width':w,'height':h,'kind':'FPGA ROI mask; not a fine object segmentation mask'}
            self.persist_json(self.session/'fpga_roi_report.json',description)

    def apply_camera(self,entry,data):
        cameras=self.round['manifest']['capture'].get('plan',{}).get('camera_ids',[]) if self.round else []
        if isinstance(cameras,list) and len(cameras)==2 and data.get('camera_id') in cameras:
            entry['camera_id']=data['camera_id']
            entry['camera_role']='primary' if data['camera_id']==cameras[0] else 'upper'

    def frame(self,frame):
        digest=hashlib.sha256(frame.payload).hexdigest();key=(self.round_serial,frame.sequence)
        if self.seen.get(key)==digest:return
        if key in self.seen:
            self.assembler.bad_packets+=1;return
        self.cache(self.seen,key,digest)
        is_sample=(self.round is not None and frame.sequence!=0xffffffff)
        directory=self.round['path'] if is_sample else self.session/'snapshots'
        (directory/'images').mkdir(parents=True,exist_ok=True)
        extension='.jpg' if frame.encoding=='jpeg' else '.png'
        filename=('frame_%010d'%frame.sequence)+extension
        target=directory/'images'/filename
        if frame.encoding=='jpeg':data=frame.payload
        else:
            ok,encoded=cv2.imencode('.png',frame.image)
            if not ok:raise ValueError('PNG encoding failed')
            data=encoded.tobytes()
        target.write_bytes(data)
        control=frame.telemetry['control']
        original_color=not bool(control&((1<<1)|(1<<2)|(1<<5)|(1<<9)))
        entry={'file':'images/'+filename,'camera_id':'front_ov5640','source_sequence':frame.sequence,
           'received_at':datetime.now().astimezone().isoformat(),'sha256':hashlib.sha256(data).hexdigest(),
           'transport_sha256':digest,'image_size':[frame.width,frame.height],
           'encoding':frame.encoding,'valid':original_color,'color_input':original_color,
           'transport_complete':True,**frame.telemetry}
        if frame.sequence in self.quality:entry['fpga_quality']=self.quality[frame.sequence]
        if frame.sequence in self.samples:
            entry.update({k:v for k,v in self.samples[frame.sequence].items() if k not in ('file','sha256','valid','camera_id','camera_role')})
            self.apply_camera(entry,self.samples[frame.sequence])
        elif is_sample:entry['missing_sample_metadata']=True
        if self.last_metadata and self.last_metadata.get('kind')=='transport_test':
            entry['transport_test']=self.last_metadata
            expected=self.last_metadata.get('expected_transport_sha256')
            entry['expected_transport_hash_matches']=expected==digest
        if is_sample:
            self.round['manifest']['frames'].append(entry);self.records[key]=entry;self.round_manifest()
        else:self.persist_json(target.with_suffix(extension+'.json'),entry)
        # Save status last so a completed-frame status always references real files.
        ok,preview=cv2.imencode('.jpg',cv2.resize(frame.image,(960,720),interpolation=cv2.INTER_AREA))
        if ok:
            latest=preview.tobytes();tmp=ROOT/'latest.tmp'
            try:tmp.write_bytes(latest);tmp.replace(ROOT/'latest.jpg')
            except OSError as exc:print('DISK_PREVIEW_SKIPPED',str(exc),flush=True)
        else:latest=None
        with self.lock:
            self.status['frames']+=1
            self.status['snapshots']+=not is_sample
            self.status.update(state='receiving',note='已收到完整图片；接收器继续等待板端数据，不触发拍照或转动。',last_frame={'path':str(target),'width':frame.width,
                'height':frame.height,'sequence':frame.sequence,'transport_sha256':digest,
                'expected_transport_hash_matches':entry.get('expected_transport_hash_matches'),
                'fpga_quality':entry.get('fpga_quality'),'snapshot_only':not is_sample})
            if latest:self.last_jpeg=latest
        self.flush();print('SAVED',str(target),frame.width,frame.height,'sha256',digest,flush=True)

    def consume(self,packet,source):
        if source[0]!=self.board:return
        with self.lock:self.status['board_packets']+=1
        magic=packet[:4];self.counts[magic.decode('ascii',errors='replace')]+=1
        if magic in (b'FPG3',b'FPG4'):
            frame=self.assembler.feed(packet)
            if frame:self.frame(frame)
        elif magic==b'FPGS':
            if packet!=b'FPGS':self.assembler.bad_packets+=1;return
            if self.round and not self.round['manifest']['capture'].get('end_event_received'):
                self.round['manifest']['capture']['state']='incomplete'
                self.round['manifest']['capture']['sender_restarted_before_end']=True
                self.round_manifest()
            # Board services restart their trigger and sequence counters. Never
            # reuse a previous round or partial frame across a sender handshake.
            self.sender_generation+=1;self.round=None;self.round_serial+=1
            self.samples.clear();self.quality.clear();self.seen.clear();self.records.clear()
            self.assembler.pending.clear();self.last_metadata=None
            print('SENDER_RESTART',self.sender_generation,flush=True)
        else:
            try:self.event(packet)
            except (ValueError,struct.error,json.JSONDecodeError,zlib.error):self.assembler.bad_packets+=1


PAGE='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>FPGA 照片接收</title>
<style>body{font:17px system-ui;background:#101826;color:#e5edf8;max-width:1100px;margin:32px auto;padding:0 20px}h1{font-size:28px}#info{line-height:1.8;background:#1b293d;padding:20px;border-radius:14px}img{max-width:100%;border-radius:12px;margin-top:20px}small{color:#a9bdd6}pre{white-space:pre-wrap;word-break:break-all}</style>
<h1>FPGA 照片接收</h1><p>只接收数据，不发送拍照或舵机命令。</p><div id="info">正在连接本机接收器…</div><img id="image" hidden><p><small>单张连通测试与整轮建模数据分别保存。丢帧不会被标成采集完成。</small></p>
<script>let previous='';async function refresh(){try{let s=await(await fetch('/status',{cache:'no-store'})).json();document.querySelector('#info').textContent='状态：'+s.state+' ｜ 数据包：'+s.board_packets+' ｜ 完整图片：'+s.frames+' ｜ CRC/格式错误：'+s.bad_packets+' ｜ 超时帧：'+s.expired_frames+' ｜ 保存位置：'+s.session;let f=s.last_frame;if(f&&f.path!==previous){previous=f.path;let im=document.querySelector('#image');im.src='/latest.jpg?t='+Date.now();im.hidden=false;}}catch(e){document.querySelector('#info').textContent='接收器已停止或尚未启动';}}setInterval(refresh,1000);refresh();</script></html>'''


def http_handler(receiver):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path.split('?')[0]=='/status':
                with receiver.lock:data=json.dumps(receiver.status,ensure_ascii=False).encode('utf-8')
                mime='application/json; charset=utf-8'
            elif self.path.split('?')[0]=='/latest.jpg':
                with receiver.lock:data=receiver.last_jpeg
                mime='image/jpeg'
                if data is None:self.send_error(404);return
            elif self.path=='/':data=PAGE.encode('utf-8');mime='text/html; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    return Handler


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bind',default='0.0.0.0')
    p.add_argument('--port',type=int,default=5002);p.add_argument('--board',default='192.168.2.99')
    p.add_argument('--web-port',type=int,default=8766);p.add_argument('--duration',type=float,default=0)
    p.add_argument('--output',type=Path,default=ROOT/'datasets');args=p.parse_args()
    receiver=Receiver(args.output,args.board)
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,16*1024*1024)
    try:sock.bind((args.bind,args.port))
    except Exception:
        receiver.status.update(state='failed_to_bind');receiver.flush();sock.close();raise
    sock.settimeout(.25);web=ThreadingHTTPServer(('127.0.0.1',args.web_port),http_handler(receiver))
    thread=threading.Thread(target=web.serve_forever,daemon=True);thread.start()
    save_json(ROOT/'receiver_process.json',{'pid':os.getpid(),'script':str(Path(__file__).resolve()),'web_port':args.web_port})
    print('RECEIVER_READY',args.bind,args.port,'http://127.0.0.1:'+str(args.web_port),flush=True)
    start=time.monotonic();last_flush=0
    try:
        while not receiver.stop.is_set() and (not args.duration or time.monotonic()-start<args.duration):
            try:packet,source=sock.recvfrom(65535)
            except socket.timeout:packet=None
            if packet is not None:
                with receiver.lock:receiver.status['packets']+=1
                receiver.consume(packet,source)
            receiver.assembler.expire()
            if time.monotonic()-last_flush>2:receiver.flush();last_flush=time.monotonic()
    except KeyboardInterrupt:pass
    finally:
        sock.close();web.shutdown();web.server_close()
        if receiver.round and receiver.round['manifest']['capture']['state']=='capturing':
            receiver.round['manifest']['capture']['state']='incomplete';receiver.round_manifest()
        with receiver.lock:receiver.status['state']='stopped'
        receiver.flush()


if __name__=='__main__':main()
