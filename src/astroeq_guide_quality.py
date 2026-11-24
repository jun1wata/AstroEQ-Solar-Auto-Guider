#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUIDE補正の診断・DEC反転の誤判定抑制。駆動命令は出さない。"""
import csv
import math
import time
from datetime import timedelta
import numpy as np


class GuideQuality:
    def __init__(self, path, start_utc, start_mono, *, dec_reverse_confirm=2):
        self.fp = open(path, 'w', encoding='utf-8', newline='')
        self.writer = csv.writer(self.fp)
        self.writer.writerow(['utc','elapsed_s','event','pulse_no','axis','direction','pulse_ms',
                              'x_px','y_px','target_x_px','target_y_px','dx_px','dy_px','error_px',
                              'duration_s','error_after_px','error_change_px','note'])
        self.start_utc = start_utc
        self.start_mono = start_mono
        self.last_dec_direction = None
        self.pending_dec_direction = None
        self.pending_dec_count = 0
        self.dec_reverse_confirm = dec_reverse_confirm
        self.pending = None

    def log(self, event, *, point=None, target=None, pulse_no='', axis='', direction='',
            pulse_ms='', duration_s='', error_after_px='', error_change_px='', note=''):
        now = time.monotonic()
        utc = self.start_utc + timedelta(seconds=now-self.start_mono)
        x=y=tx=ty=dx=dy=err=''
        if point is not None and target is not None:
            x,y = (float(v) for v in point)
            tx,ty = (float(v) for v in target)
            dx,dy=x-tx,y-ty
            err=math.hypot(dx,dy)
        self.writer.writerow([utc.isoformat(timespec='milliseconds'),f'{now-self.start_mono:.3f}',
                              event,pulse_no,axis,direction,pulse_ms,x,y,tx,ty,dx,dy,err,
                              duration_s,error_after_px,error_change_px,note])
        self.fp.flush()

    def allow_dec(self, direction):
        """DEC反転方向は2回連続して要求された場合だけ許可。追加駆動しない。"""
        if self.last_dec_direction is None or direction == self.last_dec_direction:
            self.pending_dec_direction = None
            self.pending_dec_count = 0
            return True
        if self.pending_dec_direction != direction:
            self.pending_dec_direction = direction
            self.pending_dec_count = 1
        else:
            self.pending_dec_count += 1
        if self.pending_dec_count < self.dec_reverse_confirm:
            return False
        self.pending_dec_direction = None
        self.pending_dec_count = 0
        return True

    def sent(self, *, axis, direction, pulse_no, pulse_ms, point, target, duration_s):
        if axis == 'DEC':
            self.last_dec_direction = direction
        self.pending = (pulse_no, axis, direction, pulse_ms,
                        np.asarray(point, dtype=float), np.asarray(target, dtype=float))
        self.log('PULSE', pulse_no=pulse_no, axis=axis, direction=direction,
                 pulse_ms=pulse_ms, point=point, target=target, duration_s=f'{duration_s:.3f}')

    def observe(self, point):
        if self.pending is None:
            return
        pulse_no,axis,direction,pulse_ms,before,target=self.pending
        after=np.asarray(point, dtype=float)
        old=float(np.linalg.norm(before-target))
        new=float(np.linalg.norm(after-target))
        self.log('POST', pulse_no=pulse_no, axis=axis, direction=direction,
                 pulse_ms=pulse_ms, point=after, target=target,
                 error_after_px=f'{new:.3f}',error_change_px=f'{new-old:+.3f}')
        print(f'GUIDE結果 #{pulse_no}: {axis} {direction:+d} {pulse_ms}ms / '
              f'前={old:.2f}px 後={new:.2f}px 差={new-old:+.2f}px / '
              f'X={after[0]-target[0]:+.2f} Y={after[1]-target[1]:+.2f}',flush=True)
        self.pending=None

    def close(self):
        self.fp.close()
