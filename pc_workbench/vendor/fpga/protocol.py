"""Decode the FPG3/FPG4 datagrams used by the inspected board sender."""
from __future__ import annotations
import binascii
from dataclasses import dataclass
import struct
import time
import cv2
import numpy as np

HEADER=struct.Struct('<4sHHIIHHHIIIIII')
QUALITY_EXT=struct.Struct('<4sIIIIIIIIIIIII')
MAX_FRAME=32*1024*1024
MAX_CHUNK=1400-HEADER.size-2


@dataclass
class Frame:
    sequence:int
    width:int
    height:int
    encoding:str
    payload:bytes
    image:np.ndarray
    telemetry:dict


class Assembler:
    def __init__(self,timeout=12.,max_pending=4):
        self.timeout=timeout;self.max_pending=max_pending;self.pending={}
        self.bad_packets=0;self.expired_frames=0

    def expire(self,now=None):
        now=time.monotonic() if now is None else now
        for key,value in list(self.pending.items()):
            if now-value['created']>self.timeout:
                del self.pending[key];self.expired_frames+=1

    def feed(self,packet,now=None):
        now=time.monotonic() if now is None else now;self.expire(now)
        try:
            if len(packet)<HEADER.size+2:raise ValueError('short frame packet')
            magic,w,h,seq,total,index,count,size,*values=HEADER.unpack_from(packet)
            if (magic not in (b'FPG3',b'FPG4') or not 1<=w<=8192 or not 1<=h<=8192 or
                w*h>24_000_000 or not 0<total<=MAX_FRAME or not 0<size<=MAX_CHUNK or
                count!=(total+MAX_CHUNK-1)//MAX_CHUNK or not 0<=index<count or
                size!=min(MAX_CHUNK,total-index*MAX_CHUNK) or len(packet)!=HEADER.size+size+2):
                raise ValueError('frame header bounds/length mismatch')
            if magic==b'FPG3' and total!=w*h*3:raise ValueError('raw frame byte count mismatch')
            payload=packet[HEADER.size:-2]
            if struct.unpack_from('<H',packet,len(packet)-2)[0]!=binascii.crc_hqx(payload,0xffff):
                raise ValueError('chunk CRC mismatch')
            key=(seq,magic);identity=(w,h,total,count,tuple(values))
            if key not in self.pending:
                if len(self.pending)>=self.max_pending:
                    oldest=min(self.pending,key=lambda k:self.pending[k]['created'])
                    del self.pending[oldest];self.expired_frames+=1
                self.pending[key]={'created':now,'identity':identity,'chunks':{}}
            frame=self.pending[key]
            if frame['identity']!=identity:
                del self.pending[key];raise ValueError('inconsistent frame chunks')
            previous=frame['chunks'].get(index)
            if previous is not None and previous!=payload:
                del self.pending[key];raise ValueError('conflicting duplicate chunk')
            frame['chunks'][index]=payload
            if len(frame['chunks'])!=count:return None
            data=b''.join(frame['chunks'][i] for i in range(count));del self.pending[key]
            if len(data)!=total:raise ValueError('incomplete frame')
            if magic==b'FPG4':
                image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR);encoding='jpeg'
                if image is None or image.shape!=(h,w,3):raise ValueError('JPEG decode/dimension mismatch')
            else:
                image=np.frombuffer(data,np.uint8).reshape((h,w,3)).copy();encoding='bgr24'
            names=('frame_count','target_x','target_y','target_area','servo_pulse_us','control')
            return Frame(seq,w,h,encoding,data,image,dict(zip(names,values)))
        except (ValueError,struct.error,cv2.error):
            self.bad_packets+=1;return None


def quality_event(packet):
    if len(packet)==QUALITY_EXT.size:
        _,seq,frame,edges,clipped,pixels,luma,dark,saturated,noise,rows,frame_pixels,flags,motor=QUALITY_EXT.unpack(packet)
        # pixels is the ISP statistic/ROI population, NOT necessarily full-frame.
        return seq,dict(frame=frame,edges=edges,clipped=clipped,pixels=pixels,
            luma_sum=luma,dark=dark,saturated=saturated,noise=noise,rows=rows,
            frame_pixels=frame_pixels,shape_flags=flags,motor_steps=motor,
            mean_luma=luma/pixels if pixels else None,dark_ratio=dark/pixels if pixels else None,
            saturated_ratio=saturated/pixels if pixels else None,
            frame_complete=(flags&0x3f)==0x37,frame_bad=bool(flags&8),
            statistics_scope='sender-reported pixels; may cover ROI rather than full frame')
    if len(packet) in (24,28):
        values=struct.unpack('<4s'+'I'*((len(packet)-4)//4),packet)
        _,seq,frame,edges,clipped,pixels,*motor=values
        q=dict(frame=frame,edges=edges,clipped=clipped,pixels=pixels,frame_complete=False,
               limitation='legacy quality lacks full-frame shape proof')
        if motor:q['motor_steps']=motor[0]
        return seq,q
    raise ValueError('unsupported FPGQ packet size')
