#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AstroEQ UI development: OLED navigation + USB camera + optional real manual steps.

Requires the already tested files in the same directory:
  oled_ui_dev.py, ssd1315_encoder.py (python-evdev version),
  ssd1315_oled_modes_right.py, solar_center_oled_modes_right.py.

Mount motion disabled by default. Real short-step GoTo requires explicit --execute-mount.
Tracking LED on GPIO20 can optionally be physically illuminated for TEST ONLY.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import timedelta, datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageDraw, Image, ImageFont
from astroeq_camera_calibration_halfarcsec import run_calibration, CalibrationError
from astroeq_pulse_autoguide import GuideSettings, choose_guide_pulse
from astroeq_guide_socket import MIN_GUIDE_PULSE_MS
from astroeq_guide_quality import GuideQuality
from astroeq_startup_oled import render_status
from astroeq_sun_position import get_gps

from oled_ui_dev import UI, SCREEN_CENTER, SCREEN_RAW, SCREEN_MANUAL, SCREEN_CAL, SCREEN_GUIDE
from astroeq_tracking_relative_goto_1arcsec import AstroEQTrackingRelativeGoTo
from astroeq_remote_mount import RemoteGuideMount
from ssd1315_encoder import UIEncoder
from ssd1315_oled_modes_right import SolarOLED
from solar_center_oled_modes_right import detect_circle, save_overlay


class Camera:
    def __init__(self, args):
        self.args = args
        self.cap = None
        self.still = None
        self.frames = deque(maxlen=args.frame_average)
        self.centers = deque(maxlen=args.center_average)
        self.prev = None
        self.gray = None
        self.raw = None
        self.circle = None
        self.detection_method = "NONE"
        self.n = 0
        self.success = 0
        self.started = time.monotonic()
        if args.image:
            self.still = cv2.imread(args.image, cv2.IMREAD_GRAYSCALE)
            if self.still is None:
                raise RuntimeError(f'静止画像を開けません: {args.image}')
            print(f'入力画像: {args.image} / {self.still.shape[1]}x{self.still.shape[0]}', flush=True)
        else:
            self.cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
            if not self.cap.isOpened():
                self.cap.release()
                raise RuntimeError(f'カメラを開けません: {args.device}')
            if args.fourcc:
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
            self.cap.set(cv2.CAP_PROP_FPS, args.fps)
            print(f'カメラ: {args.device} / 要求={args.width}x{args.height}@{args.fps:g}fps', flush=True)
            print('取得設定=%.0fx%.0f@%.1ffps' % (
                self.cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                self.cap.get(cv2.CAP_PROP_FPS)), flush=True)

    def update(self):
        if self.still is not None:
            gray = self.still
        else:
            ok, frame = self.cap.read()
            if not ok:
                raise RuntimeError('カメラフレームを取得できません')
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        self.raw = gray
        self.n += 1
        self.frames.append(gray.copy())
        if len(self.frames) < self.args.frame_average:
            return False
        avg = np.mean(np.stack(tuple(self.frames)), axis=0).astype(np.uint8)
        self.gray = avg
        result, self.detection_method = detect_circle(
            avg, self.args.canny1, self.args.canny2, self.prev, return_method=True)
        if result is None:
            self.centers.clear()
            self.circle = None
        else:
            self.prev = result
            self.centers.append(result)
            x = sum(c[0] for c in self.centers) / len(self.centers)
            y = sum(c[1] for c in self.centers) / len(self.centers)
            self.circle = (x, y, result[2])
            self.success += 1
        return True

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None



class CameraSettings:
    """CAMERA設定: DEFAULTは既存カメラ設定を保護、MANUALは機種別に記憶する。

    MANUALでは項目を短押しで確定するたびに実機と検出処理へ即時反映。
    末尾の >SET は現在反映済みの設定を機種別JSONへ保存して終了する。
    長押しでは保存せず終了するが、既に反映したカメラ値は元に戻さない。
    """
    V4L2_ITEMS = (
        ('EXP', 'exposure_time_absolute'),
        ('GAIN', 'gain'),
        ('BRIGHT', 'brightness'),
        ('CONTRAST', 'contrast'),
        ('SATURATE', 'saturation'),
    )
    PROFILE_PATH = Path.home() / '.config' / 'astroeq-solar-goto' / 'camera_manual_profiles.json'
    GUIDE_ITEMS = (('RA MAX PULSE', 'ra_max_ms', 'ms'),
                   ('DEC MAX PULSE', 'dec_max_ms', 'ms'),
                   ('GUIDE PERIOD', 'period_s', 's'))
    MENU_LABELS = ('CAM DEFAULT', 'CAM MANUAL') + tuple(item[0] for item in GUIDE_ITEMS) + ('EXIT',)

    def __init__(self, camera, guide_settings):
        self.camera = camera
        self.guide_settings = guide_settings
        self.guide_value = None
        self.stage = 'choice'
        self.choice = 0
        self.index = 0
        self.editing = False
        self.items = []
        self.controls = {}
        self.applied_values = {}  # 実機または検出処理に反映済みの値のみ保存対象
        self.preview_dirty = False  # 回転値は次のフレーム取得前に一度だけ適用
        self.last_live_luma = None
        self.last_preview_key = None
        self.last_preview_value = None
        self.message = 'ROTATE / PRESS'
        self.camera_key = None
        self.camera_name = ''
        self.profiles = self._load_profiles()
        self._discover()
        # 最初のCAMERA表示時、MANUAL設定前のカメラ実測値をセッション中保持。
        # V4L2のdefault値が不正なカメラもあるため、default欄は使用しない。
        if not hasattr(camera, 'camera_initial_controls'):
            camera.camera_initial_controls = self._snapshot_controls()
            print('CAMERA 初期状態を記録: ' + str(camera.camera_initial_controls), flush=True)

    def _v4l2(self, *args):
        return subprocess.run(['v4l2-ctl', '-d', self.camera.args.device, *args],
                              capture_output=True, text=True, timeout=3, check=False)

    def _load_profiles(self):
        try:
            with self.PROFILE_PATH.open('r', encoding='utf-8') as fp:
                obj = json.load(fp)
            if isinstance(obj, dict) and obj.get('version') == 1 and isinstance(obj.get('cameras'), dict):
                return obj['cameras']
            print('CAMERA: 保存形式が異なるため、保存済み設定は使用しません', flush=True)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            print(f'CAMERA: 保存済み設定を読み込めません: {exc}', flush=True)
        return {}

    def _identity(self):
        """USB番号が変わっても同じ型番になるようVID:PIDと製品名を取得する。"""
        dev = self.camera.args.device
        props = {}
        try:
            r = subprocess.run(['udevadm', 'info', '--query=property', '--name', dev],
                               capture_output=True, text=True, timeout=3, check=False)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if '=' in line:
                        k, v = line.split('=', 1)
                        props[k] = v
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'CAMERA: udev情報取得不可: {exc}', flush=True)
        card = ''
        try:
            r = self._v4l2('--info')
            if r.returncode == 0:
                m = re.search(r'^\s*Card type\s*:\s*(.+)$', r.stdout, re.MULTILINE)
                if m:
                    card = m.group(1).strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        vid = props.get('ID_VENDOR_ID', '').lower()
        pid = props.get('ID_MODEL_ID', '').lower()
        model = (props.get('ID_V4L_PRODUCT') or card or props.get('ID_MODEL') or '').strip()
        if not model:
            model = 'UNKNOWN-CAMERA'
        if model == 'UNKNOWN-CAMERA' and not (vid and pid):
            # 型番が特定できない機器間で設定が混ざるのを防ぐ。
            self.camera_key, self.camera_name = None, model
            print('CAMERA機種識別不可: MANUAL値の保存・復元をスキップ', flush=True)
            return
        if vid and pid:
            key = f'usb:{vid}:{pid}:{model}'
        else:
            # USB識別の取れないカメラも同じモデル名なら設定を再利用。
            key = f'model:{model}'
        self.camera_key, self.camera_name = key, model
        print(f'CAMERA機種識別: {key}', flush=True)

    def _discover(self):
        if self.camera.cap is None:
            self.message = 'NO USB CAMERA'
        else:
            self._identity()
            try:
                r = self._v4l2('--list-ctrls-menus')
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        m = re.match(r'^\s*(\w+)\s+0x[0-9a-fA-F]+\s+\((\w+)\)\s*:\s*(.*)$', line)
                        if not m:
                            continue
                        name, kind, props = m.groups()
                        vals = dict((k, int(v)) for k, v in re.findall(
                            r'\b(min|max|step|value|default)=(-?\d+)', props))
                        vals['kind'], vals['flags'] = kind, props
                        self.controls[name] = vals
                else:
                    print(f'CAMERA設定: V4L2列挙失敗: {r.stderr.strip()}', flush=True)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f'CAMERA設定: V4L2列挙不可: {exc}', flush=True)

        for label, key in self.V4L2_ITEMS:
            ctl = self.controls.get(key)
            if ctl is None or ctl['kind'] != 'int' or 'read-only' in ctl['flags']:
                print(f'CAMERA設定: {label} 非対応のためスキップ', flush=True)
                continue
            if 'inactive' in ctl['flags'] and key != 'exposure_time_absolute':
                print(f'CAMERA設定: {label} 無効のためスキップ', flush=True)
                continue
            if key == 'exposure_time_absolute' and 'inactive' in ctl['flags']:
                auto = self.controls.get('auto_exposure', {})
                if auto.get('kind') != 'menu' or 'read-only' in auto.get('flags', ''):
                    print('CAMERA設定: EXPはAUTO解除できないためスキップ', flush=True)
                    continue
            lo, hi = ctl.get('min', 0), ctl.get('max', 255)
            if hi < lo:
                continue
            self.items.append({'label':label, 'key':key, 'lo':lo,
                               'hi':hi, 'step':max(1, ctl.get('step', 1)),
                               'value':ctl.get('value', lo)})
        for label, key, lo, hi in (('CANNY TH1', 'canny1', 0, 255),
                                    ('CANNY TH2', 'canny2', 0, 255),
                                    ('CENTER AVG', 'center_average', 1, 20)):
            self.items.append({'label':label, 'key':key, 'lo':lo, 'hi':hi,
                               'step':1, 'value':getattr(self.camera.args, key)})

    @staticmethod
    def _safe_value(item, value):
        try:
            number = int(value)
        except (ValueError, TypeError):
            return None
        lo, hi, step = item['lo'], item['hi'], item['step']
        if not lo <= number <= hi:
            return None
        # カメラが通知するstepに合わせ、保存値が不正な場合はその項目をスキップ。
        if (number - lo) % step:
            return None
        return number

    def _restore_manual(self):
        """前回のMANUAL設定を読み込み、選択直後にカメラへ反映する。"""
        if not self.camera_key:
            print('CAMERA MANUAL: 機種不明のため現在値から開始', flush=True)
            return
        entry = self.profiles.get(self.camera_key, {})
        values = entry.get('values', {}) if isinstance(entry, dict) else {}
        if not isinstance(values, dict):
            values = {}
        restored = []
        for item in self.items:
            key = item['key']
            if key not in values:
                continue
            value = self._safe_value(item, values[key])
            if value is None:
                print(f'CAMERA MANUAL: 保存値が現在の範囲外 {key}', flush=True)
                continue
            # 画面の候補値を書き換えるだけでなく、実機に直ちに設定する。
            item['value'] = value
            if self._apply_item(item):
                restored.append(f'{key}={value}')
        print(f'CAMERA MANUAL: {self.camera_name} 読込・反映: '
              + (', '.join(restored) if restored else '有効な保存値なし'), flush=True)

    def _reset_detection_state(self):
        # Cannyと中心平均を変更すると過去の中心点を引きずらないようにする。
        self.camera.centers = deque(maxlen=self.camera.args.center_average)
        self.camera.prev = None
        self.camera.circle = None
        self.camera.frames.clear()

    def _snapshot_controls(self):
        """MANUAL操作前の有効な実測値を保存。カメラが返す不正なdefault値を使わない。"""
        snapshot = {}
        names = ('auto_exposure',) + tuple(key for _label, key in self.V4L2_ITEMS)
        for key in names:
            ctl = self.controls.get(key)
            if not ctl or 'read-only' in ctl.get('flags', ''):
                continue
            value = ctl.get('value')
            if not isinstance(value, int):
                continue
            if key != 'auto_exposure':
                if value < ctl.get('min', value) or value > ctl.get('max', value):
                    continue
                if (value - ctl.get('min', value)) % max(1, ctl.get('step', 1)):
                    continue
            # 列挙結果を主とし、取れれば読み戻しで実測値を優先する。
            try:
                r = self._v4l2(f'--get-ctrl={key}')
                if r.returncode == 0:
                    m = re.search(r':\s*(-?\d+)', r.stdout)
                    if m:
                        observed = int(m.group(1))
                        if key == 'auto_exposure' or (ctl.get('min', observed) <= observed <= ctl.get('max', observed)):
                            value = observed
            except (OSError, subprocess.TimeoutExpired):
                pass
            snapshot[key] = value
        return snapshot

    def _write_control(self, key, value):
        """V4L2設定と読戻しを検証。失敗した項目はログを残して次へ。"""
        try:
            r = self._v4l2(f'--set-ctrl={key}={value}')
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or '設定拒否')
            check = self._v4l2(f'--get-ctrl={key}')
            m = re.search(r':\s*(-?\d+)', check.stdout) if check.returncode == 0 else None
            if m is None or int(m.group(1)) != value:
                raise RuntimeError('読み戻し値不一致')
            return True
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            print(f'CAMERA DEFAULT復元失敗: {key}={value} / {exc}', flush=True)
            return False

    def _restore_camera_initial(self):
        """USB抜き差し不要で、MANUAL開始前のライブ映像設定へ戻す。"""
        saved = getattr(self.camera, 'camera_initial_controls', {})
        if self.camera.cap is None or not saved:
            print('CAMERA DEFAULT: カメラ初期設定を復元できません（未取得）', flush=True)
            return False
        # AUTO露出だった場合、まずAUTOへ戻す。露出時間はinactiveになるので書かない。
        # 手動露出だった場合のみ手動に戻し、初期露出時間を復元する。
        initial_mode = saved.get('auto_exposure')
        success = True
        if initial_mode is not None:
            success = self._write_control('auto_exposure', initial_mode) and success
        for _label, key in self.V4L2_ITEMS:
            if key not in saved:
                continue
            if key == 'exposure_time_absolute' and initial_mode != 1:
                continue
            if not self._write_control(key, saved[key]):
                success = False
        # 以後のMANUAL画面で初期値ではなく実際の復元値を表示するために、
        # ここに保持している辞書は更新せず、画面を開く際に再取得する。
        print('CAMERA DEFAULT: 初期カメラ設定復元' + ('成功' if success else '一部失敗'), flush=True)
        return success

    def reset_detector(self):
        # カメラ本体はこのPythonプロセス内でMANUAL操作前に観測した値へ戻す。
        # Canny TH1は従来どおり保持、TH2=150、中心位置平均=5。
        ok = self._restore_camera_initial()
        a = self.camera.args
        a.canny2, a.center_average = 150, 5
        self._reset_detection_state()
        print(f'CAMERA DEFAULT: カメラ初期状態復元={ok} / Canny TH1={a.canny1}維持 TH2=150 / 中心平均=5', flush=True)

    def rotate(self, steps):
        if self.stage == 'choice':
            self.choice = (self.choice + steps) % len(self.MENU_LABELS)
            self.message = 'ROTATE / PRESS'
        elif self.stage == 'guide':
            _, key, _ = self.GUIDE_ITEMS[self.choice - 2]
            lo, hi, step = self.guide_settings.RANGES[key]
            # 編集中は仮値だけを更新し、短押し確定まで制御・保存値には触れない。
            self.guide_value = max(lo, min(hi, self.guide_value + steps * step))
        elif self.stage == 'manual' and self.items:
            if self.editing:
                item = self.items[self.index]
                val = item['value'] + steps * item['step']
                new_value = max(item['lo'], min(item['hi'], val))
                if new_value != item['value']:
                    item['value'] = new_value
                    # 編集中でもカメラに反映する。まとめて複数クリック処理された
                    # ときにV4L2コマンドを連発せず、最後の値のみ送信する。
                    self.preview_dirty = True
            else:
                self.index = (self.index + steps) % (len(self.items) + 1)

    def flush_preview(self):
        """回転後、次のフレーム取得前に現在の項目をV4L2へ反映。"""
        if self.stage != 'manual' or not self.editing or not self.preview_dirty:
            return
        self.preview_dirty = False
        item = self.items[self.index]
        self._apply_item(item)

    def report_live_frame(self):
        """カメラ変更時の輝度変化をログで見える化する。"""
        if self.last_preview_key is None or self.camera.raw is None:
            return
        mean = float(np.mean(self.camera.raw))
        old = self.last_live_luma
        print(f'CAMERA PREVIEW FRAME: {self.last_preview_key}={self.last_preview_value} '
              f'輝度平均={mean:.1f}'
              + (f' (変更前={old:.1f}, 差={mean-old:+.1f})' if old is not None else ''), flush=True)
        self.last_preview_key = None

    def _apply_item(self, item):
        """単一項目をその場で適用。実測で成功した値だけ記録する。"""
        key, value = item['key'], item['value']
        # 設定反映後のフレーム輝度を表示して、設定値が変わっただけなのか
        # 実際の映像にも変化があるのかを切り分ける。
        before_luma = float(np.mean(self.camera.raw)) if self.camera.raw is not None else None
        if key in ('canny1', 'canny2', 'center_average'):
            setattr(self.camera.args, key, value)
            self._reset_detection_state()
            self.applied_values[key] = value
            self.message = f'{item["label"]} SET'
            print(f'CAMERA LIVE: {key}={value}', flush=True)
            return True
        if self.camera.cap is None:
            self.message = 'NO USB CAMERA'
            return False
        try:
            if key == 'exposure_time_absolute':
                mode = self._v4l2('--set-ctrl=auto_exposure=1')
                if mode.returncode:
                    raise RuntimeError(mode.stderr.strip() or 'AUTO解除失敗')
            r = self._v4l2(f'--set-ctrl={key}={value}')
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or '設定拒否')
            readback = self._v4l2(f'--get-ctrl={key}')
            match = re.search(r':\s*(-?\d+)', readback.stdout) if readback.returncode == 0 else None
            if match is None or int(match.group(1)) != value:
                raise RuntimeError('設定値の読み戻しが一致しません')
            self.applied_values[key] = value
            self.message = f'{item["label"]} SET'
            self.last_live_luma = before_luma
            self.last_preview_key = key
            self.last_preview_value = value
            print(f'CAMERA LIVE: {key}={value} (V4L2読み戻し一致。次フレームの輝度を確認)', flush=True)
            return True
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            # 保存対象に含めず、UIの候補値を直近の実機反映値へ戻す。
            ctl = self.controls.get(key, {})
            fallback = self.applied_values.get(key, ctl.get('value'))
            if fallback is not None:
                item['value'] = fallback
            self.message = f'{item["label"]} ERROR'
            print(f'CAMERA LIVE 反映失敗: {key}={value}: {exc}', flush=True)
            return False

    def _save_manual(self):
        """SETでは適用を繰り返さず、現在反映済みの値のみを記録。"""
        if self.camera_key:
            current = self.profiles.get(self.camera_key, {})
            saved = current.get('values', {}) if isinstance(current, dict) else {}
            saved = dict(saved) if isinstance(saved, dict) else {}
            # 保存に成功した項目だけ更新する。非対応の項目には触れない。
            saved.update(self.applied_values)
            self.profiles[self.camera_key] = {'model': self.camera_name, 'values': saved}
            try:
                self.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp = self.PROFILE_PATH.with_suffix('.json.tmp')
                with tmp.open('w', encoding='utf-8') as fp:
                    json.dump({'version': 1, 'cameras': self.profiles}, fp,
                              indent=2, ensure_ascii=False)
                    fp.write('\n')
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.PROFILE_PATH)
                print(f'CAMERA MANUAL保存: {self.camera_name} -> {self.PROFILE_PATH}', flush=True)
            except OSError as exc:
                print(f'CAMERA MANUAL保存失敗: {exc}', flush=True)
        print(f'CAMERA SET完了: {len(self.applied_values)}項目の反映値を保存', flush=True)

    @property
    def exit_selected(self):
        return self.stage == 'choice' and self.choice == len(self.MENU_LABELS) - 1

    def short(self):
        if self.stage == 'choice':
            if self.exit_selected:
                return False  # 画面遷移は呼び出し元のEXIT専用経路で処理する。
            if self.choice == 0:
                self.reset_detector()
                return True
            if self.choice >= 2:
                _, key, _ = self.GUIDE_ITEMS[self.choice - 2]
                self.guide_value = getattr(self.guide_settings, key)
                self.message = 'PRESS: SAVE'
                self.stage = 'guide'
                return False
            self.stage = 'manual'
            self.index = 0
            self.editing = False
            self._restore_manual()
            return False
        if self.stage == 'guide':
            label, key, unit = self.GUIDE_ITEMS[self.choice - 2]
            try:
                self.guide_settings.commit(self.guide_settings.PATH, key, self.guide_value)
            except OSError as exc:
                self.message = 'SAVE FAILED'
                print(f'GUIDE設定の保存失敗: {exc}', flush=True)
                return False
            print(f'GUIDE設定保存: {label}={self.guide_value}{unit}', flush=True)
            self.stage = 'choice'
            return False
        if self.index == len(self.items):
            self._save_manual()
            return True
        if not self.editing:
            self.editing = True
            self.preview_dirty = False
            return False
        # 短押し確定時にも再送信し、操作中の一時プレビュー値を確定。
        self.editing = False
        self.preview_dirty = False
        self._apply_item(self.items[self.index])
        return False

    def render(self):
        im = Image.new('1', (128, 64), 0)
        d = ImageDraw.Draw(im)
        font = ImageFont.load_default()
        d.text((1, 0), 'SETTINGS', font=font, fill=1)
        d.line((0, 12, 127, 12), fill=1)
        if self.stage == 'choice':
            # 64pxのメニュー領域内で6項目をスクロール表示。EXITはGUIDE PERIODの直下。
            top = min(max(0, self.choice - 1), len(self.MENU_LABELS) - 3)
            for row, idx in enumerate(range(top, top + 3)):
                y = 15 + 16 * row
                selected = self.choice == idx
                d.rectangle((1, y, 126, y + 14), outline=1, fill=int(selected))
                d.text((4, y + 1), self.MENU_LABELS[idx], font=font, fill=0 if selected else 1)
        elif self.stage == 'guide':
            label, _, unit = self.GUIDE_ITEMS[self.choice - 2]
            d.text((1, 16), label, font=font, fill=1)
            d.rectangle((1, 31, 126, 48), fill=1)
            d.text((5, 33), f'{self.guide_value} {unit}', font=font, fill=0)
            d.text((1, 53), self.message, font=font, fill=1)
        else:
            if self.index == len(self.items):
                d.text((3, 21), '>SET', font=font, fill=1)
                d.text((3, 43), 'PRESS: SAVE', font=font, fill=1)
            else:
                item = self.items[self.index]
                d.text((1, 16), f'{self.index+1}/{len(self.items)} {item["label"]}', font=font, fill=1)
                d.rectangle((1, 31, 126, 48), outline=1, fill=1 if self.editing else 0)
                d.text((5, 33), f'{item["value"]} [{item["lo"]}:{item["hi"]}]', font=font,
                       fill=0 if self.editing else 1)
                d.text((1, 53), 'PRESS: LIVE' if self.editing else 'TURN ITEM', font=font, fill=1)
        return im

def stamp_back(canvas):
    # 128x64 OLED: visible cue for the existing short-press BACK action.
    d = ImageDraw.Draw(canvas)
    d.rectangle((96, 51, 127, 63), fill=0, outline=1)
    d.text((98, 52), 'BACK', fill=1)
    return canvas


def render_guide(ui, calibration, history, guiding):
    im = Image.new('1', (128, 64), 0)
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    d.text((0, 0), 'GUIDE ON' if guiding else ('CAL OK / GUIDE OFF' if calibration else 'AUTO GUIDE: OFF'), font=font, fill=1)
    d.rectangle((1, 12, 126, 41), outline=1)
    if calibration and history:
        # RA: 実線、DEC: 2点おきの点線。両軸の推定誤差を+-120角秒で縮尺固定。
        values = list(history)[-60:]
        d.line((2, 27, 125, 27), fill=1)
        for axis in (0, 1):
            coords=[]
            for i,v in enumerate(values):
                x = 4 + int(i*119/max(1,len(values)-1))
                y = 27 - int(max(-120,min(120,v[axis])) * 13 / 120)
                coords.append((x,y))
            if axis==0 and len(coords)>1:
                d.line(coords, fill=1)
            elif axis==1:
                for i,pt in enumerate(coords):
                    if i%3 == 0: d.point(pt,fill=1)
        last=values[-1]
        # 画像上のズレから推定した軸秒角。実軸角の実測値ではない。
        d.text((3, 13), f'R{last[0]:+.0f} D{last[1]:+.0f} arcsec', font=font, fill=1)
    else:
        d.text((10, 20), 'RA/DEC: NO DATA', font=font,fill=1)
    # 表示名のみ変更。選択番号0/1/2とUI.short()の遷移先の対応は維持する。
    for i,(x0,x1,name) in enumerate(((0,34,'SET'),(36,84,'CENTER'),(86,127,'CALIB'))):
        chosen=i==ui.menu
        d.rectangle((x0,49,x1,63), fill=1 if chosen else 0, outline=1)
        d.text((x0+2,51), name, font=font,fill=0 if chosen else 1)
    return im


def render_calibration(message):
    im = Image.new('1', (128, 64), 0)
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    # 日本語ログは描かない。進捗の2要素を別行に置き、ASCIIだけを既存フォントで描く。
    # 測定が異常に大きい場合は指数表記にして、角度の文字幅を一定範囲に抑える。
    if isinstance(message, tuple):
        axis, direction, index, angle, target = message
        heading = f'{axis}{"+" if direction > 0 else "-"} {index}'
        angle_text = f'{angle:.1f}' if angle < 10000 else f'{angle:.1e}'
        distance = f'{angle_text} / {target:.0f}'
    else:
        heading, distance = message, ''
    for y, line in ((0, 'CALIBRATION'), (16, heading), (32, distance), (48, 'PRESS: BACK')):
        d.text((1, y), line, font=font, fill=1)
    return im


def render_for_screen(ui, camera, display, utc_now, mount_enabled=False,
                      calibration=None, history=None, guiding=False, cal_message='', camera_settings=None):
    if ui.screen == SCREEN_GUIDE:
        return render_guide(ui, calibration, history or (), guiding)
    if ui.screen == SCREEN_CAL:
        return render_calibration(cal_message)
    if ui.screen == SCREEN_RAW:
        # RAW全画面は廃止。下半分は従来どおり常時ライブ映像。
        return camera_settings.render() if camera_settings else stamp_back(ui.render(utc_now))
    if ui.screen == SCREEN_CENTER:
        if camera.gray is None:
            return stamp_back(ui.render(utc_now))
        h, w = camera.gray.shape
        canvas = stamp_back(display.render_center(camera.circle, w, h))
        # CENTER上半分の右上に、今回の検出に実際に使用した方式を表示する。
        # CANNY優先→検出できなければOTSU、双方失敗ならNONE。
        # 下半分のカメラ常時プレビュー、および既存の中心数値は維持。
        method = camera.detection_method
        d = ImageDraw.Draw(canvas)
        bbox = d.textbbox((0, 0), method)
        text_width = bbox[2] - bbox[0]
        # 上半分CENTER表示の右上へ検出方式を表示。
        x = max(0, 127 - text_width)
        d.rectangle((x - 1, 0, 127, 12), fill=0)
        d.text((x, 0), method, fill=1)
        return canvas
    # The guide graph intentionally remains 'NO DATA' until calibrated RA/DEC
    # error values are available. Pixel X/Y must not be mislabeled RA/DEC.
    canvas = ui.render(utc_now)
    # MOUNT MANUALはHI/LOWの4ボタンのみ。旧REL GoTo/TRACKは表示しない。
    return canvas


def parse_args():
    p = argparse.ArgumentParser(description='OLED+camera+encoder / optional RA tracking + relative GoTo nudge test')
    p.add_argument('--execute-mount', action='store_true', help='明示的な実機駆動許可。停止状態からRA太陽時追尾を開始し微動')
    p.add_argument('--astroeq-port', default=None, help='AstroEQ USBシリアルポート')
    p.add_argument('--arcsec-per-click', type=float, default=20.0, help='手動LOW微動の軸角: 初期値20秒角/クリック')
    p.add_argument('--ra-positive-dir', type=int, choices=[0, 1], default=None)
    p.add_argument('--dec-positive-dir', type=int, choices=[0, 1], default=None)
    p.add_argument('--batch-quiet-ms', type=int, default=200, help='回転が止まってから一括GoToするまでの待ち時間（ms）')
    p.add_argument('--batch-max-wait-ms', type=int, default=450, help='連続回転時の最大集約時間（ms）')
    p.add_argument('--batch-max-clicks', type=int, default=32, help='一括GoToの最大クリック数（1～32）')
    p.add_argument('--cal-clicks', type=int, default=16, help='旧オプション（互換用、CALのパルス数は画像変位で決まる）')
    p.add_argument('--cal-arcsec-per-step', type=float, default=0.5,
                   help='旧オプション（互換用、CALパルス制御には使用しない）')
    p.add_argument('--cal-counts-multiplier', type=int, default=16,
                   help='旧オプション（互換用、CALパルス制御には使用しない）')
    p.add_argument('--cal-pulse-ms', type=int, default=1000, help='CALのパルス時間（1000ms固定）')
    p.add_argument('--cal-drift', choices=('on', 'off'), default='on',
                   help='CALの自然ドリフト補正（OFFでは30秒の定点観測を省略）')
    p.add_argument('--solar-goto-remote', action='store_true', help='太陽GoToがシリアルを占有する連携モード')
    p.add_argument('--enable-guide-after-cal', action='store_true',
                   help='注意: 実機CAL成功後に自動パルス補正を許可。省略時はCAL・グラフのみ。固定円板では追尾中のRAドリフトを相殺できない場合あり')
    p.add_argument('--guide-min-interval', type=float, default=5.0, help='保存設定がない場合の補正周期（整数秒、1～30）')
    p.add_argument('--guide-max-clicks', type=int, default=2, help='旧オプション（互換用・パルスガイドには不使用）')
    p.add_argument('--guide-max-pulse-ms', type=int, default=500, help='保存設定がない場合の両軸の上限（100～2000ms、100ms刻み）')
    p.add_argument('--guide-max-pulses', type=int, default=0, help='1回のCAL後のGUIDE補正総回数上限（0=無制限、初期値0）')
    p.add_argument('--guide-deadband-px', type=float, default=2.0, help='軸方向の画像誤差がこの画素数以下なら無補正')
    p.add_argument('--guide-max-error-px', type=float, default=35.0, help='画像誤差がこの値を超えたらGUIDEを自動停止')
    p.add_argument('--guide-gain', type=float, default=0.2, help='1回の補正割合0.1～0.8（初期値0.2）')
    p.add_argument('--move-timeout', type=float, default=12.0)  # 旧スクリプト互換用（未使用）
    p.add_argument('--guide-fraction',type=float,default=0.95,help='恒星時速度に対するガイド速度倍率（CALには0.95が必要）')
    p.add_argument('--guide-min-pulse-ms', type=int, choices=(MIN_GUIDE_PULSE_MS,),
                   default=MIN_GUIDE_PULSE_MS, help='GUIDE最小パルス幅（100ms固定）')
    p.add_argument('--guide-log', default='guide_events_detailed.csv', help='補正前後の詳細ガイドCSV')
    p.add_argument('--test-tracking-led', action='store_true', help='TEST ONLY GPIO20 LED ON')
    p.add_argument('--no-encoder', action='store_true')
    p.add_argument('--seconds', type=float, default=0)
    p.add_argument('--device', default='/dev/video0')
    p.add_argument('--image', help='静止画像で表示確認する場合の入力ファイル')
    p.add_argument('--width', type=int, default=800)
    p.add_argument('--height', type=int, default=600)
    p.add_argument('--fps', type=float, default=15.0)
    p.add_argument('--fourcc', choices=['YUYV', 'MJPG'], default='YUYV')
    p.add_argument('--frame-average', type=int, default=5)
    p.add_argument('--center-average', type=int, default=5)
    p.add_argument('--canny1', type=int, default=50)
    p.add_argument('--canny2', type=int, default=150)
    p.add_argument('--oled-interval', type=float, default=0.5)
    p.add_argument('--csv', default='solar_center_ui_log.csv')
    p.add_argument('--output', default='solar_center_ui_preview.png')
    return p.parse_args()


def main():
    a = parse_args()
    if not (1 <= a.frame_average <= 20 and 1 <= a.center_average <= 20):
        raise SystemExit('平均枚数は1～20で指定してください')
    if not (0 <= a.canny1 <= 255 and 0 <= a.canny2 <= 255):
        raise SystemExit('Canny閾値は0～255で指定してください')
    if a.fps <= 0 or a.oled_interval <= 0 or a.seconds < 0:
        raise SystemExit('fps / OLED更新間隔 / seconds が不正です')
    # In integrated mode, the proven Solar GoTo owns GPS and serial exclusively.
    # Reusing its valid fix avoids a second blocking gpsd iteration while tracking.
    if a.solar_goto_remote:
        from astroeq_guide_socket import request, STATUS_TIMEOUT
        try:
            state = request({'cmd': 'STATUS'}, timeout=STATUS_TIMEOUT)
            if state.get('state') != 'TRACKING' or state.get('f1') != '111':
                raise RuntimeError('太陽GoToの追尾が確認できません')
            if not all(k in state for k in ('gps_utc', 'gps_lat', 'gps_lon')):
                raise RuntimeError('太陽GoToからGPS情報が届きません。統合版を全ファイル更新してください')
            base = datetime.fromisoformat(state['gps_utc'])
            if base.tzinfo is None:
                raise RuntimeError('GPS UTCにタイムゾーンがありません')
            gps_lat, gps_lon = float(state['gps_lat']), float(state['gps_lon'])
            if not (-90 <= gps_lat <= 90 and -180 <= gps_lon <= 180):
                raise RuntimeError('GPS座標が不正です')
        except (OSError, ValueError, RuntimeError) as exc:
            raise SystemExit(f'連携先GPS/追尾情報取得失敗: {exc}')
        print('GPS情報取得: 太陽GoTo側の測位値を再利用（gpsdの二重待機なし）', flush=True)
    else:
        print('GPS測位待ち: gpsdから有効UTC・緯度・経度を取得します', flush=True)
        try:
            base, gps_lat, gps_lon = get_gps()
        except RuntimeError as exc:
            raise SystemExit(str(exc))
    clock_start = time.monotonic()
    print(f'GPS測位確定: 緯度={gps_lat:.6f} 経度={gps_lon:.6f} UTC={base.isoformat()}', flush=True)
    if not 0.5 <= a.arcsec_per_click <= 30.0 or not 3 <= a.move_timeout <= 30:
        raise SystemExit('arcsec-per-click=0.5～30.0 / move-timeout=3～30秒')
    if not (50 <= a.batch_quiet_ms <= 1000 and 100 <= a.batch_max_wait_ms <= 2000
            and 1 <= a.batch_max_clicks <= 32):
        raise SystemExit('batch-quiet-ms=50～1000 / batch-max-wait-ms=100～2000 / batch-max-clicks=1～32')
    if not 1 <= a.cal_clicks <= 16 or not 1 <= a.cal_counts_multiplier <= 16 or not 0 < a.cal_arcsec_per_step <= 2 or not 1 <= a.guide_max_clicks <= 3:
        raise SystemExit('cal-clicks=1～16 / cal-counts-multiplier=1～16 / cal-arcsec-per-step=0超～2 / guide-max-clicks=1～3')
    if not 500 <= a.cal_pulse_ms <= 2000 or not 0.25 <= a.guide_fraction <= 0.95:
        raise SystemExit('cal-pulse-ms=500～2000 / guide-fraction=0.25～0.95')
    if not (0 <= a.guide_max_pulses <= 1000000
            and 0.5 <= a.guide_deadband_px <= 10.0 and 5.0 <= a.guide_max_error_px <= 100.0
            and 0.1 <= a.guide_gain <= 0.8):
        raise SystemExit('GUIDE制限値が範囲外です')
    if not a.guide_min_interval.is_integer():
        raise SystemExit('guide-min-interval は1～30の整数秒です')
    try:
        first_run = GuideSettings(a.guide_max_pulse_ms, a.guide_max_pulse_ms, int(a.guide_min_interval))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    guide_settings = GuideSettings.load(GuideSettings.PATH, first_run)
    if a.enable_guide_after_cal and not a.execute_mount:
        raise SystemExit('--enable-guide-after-cal は --execute-mount が必要です')
    if a.enable_guide_after_cal and a.image:
        raise SystemExit('静止画像ファイルでは実GUIDE駆動を許可しません')
    if a.execute_mount and (a.ra_positive_dir is None or a.dec_positive_dir is None):
        raise SystemExit('--execute-mount には両軸の --ra-positive-dir と --dec-positive-dir が必要です')
    print('Tracking状態: 実機時はRA太陽時追尾(111)を開始 / 模擬時は追尾なし', flush=True)
    print(f'手動 LOW=20秒角/click HI=180秒角/click（相対GoTo） / '
          f'CAL=各軸3分角、1000ms、0.95x恒星時、自然ドリフト補正={a.cal_drift.upper()}', flush=True)
    print('AstroEQ実機微動:', 'RA一時停止→相対GoTo→追尾再開 / DEC相対GoTo' if a.execute_mount else '無効（模擬のみ）', flush=True)
    camera = display = encoder = mount = guide_quality = None
    gpio = led = None
    ui = UI()
    calibration = None
    camera_settings = None
    guide_history = deque(maxlen=60)
    guiding = False
    cal_message = ''
    last_guide_sample = -1e10
    guide_interval_anchor = -1e10
    guide_pulse_count = 0
    guide_last_seen = -1e10
    guide_pending_error = None
    guide_nonimproving = 0
    cal_pending = False
    # ガイド基準点はガイド開始時に一度だけ採用し、そのガイド期間中は固定する。
    # CALの行列（RA/DEC→画像変位）は維持し、基準点のみ再設定可能にする。
    guide_reference_pending = False
    guide_reference_points = deque(maxlen=8)
    guide_reference_ready_at = 0.0
    guide_epoch = 0
    startup_pending = a.solar_goto_remote
    try:
        guide_quality = GuideQuality(a.guide_log, base, clock_start)
        # Camera first so resources are released in case initialization fails.
        camera = Camera(a)
        camera_settings = CameraSettings(camera, guide_settings)
        display = SolarOLED(interval=a.oled_interval)
        if startup_pending:
            # 親の案内を引き継ぎ、最初のカメラ画像つきMANUALフレームまで表示する。
            display.device.persist = True
            display.device.display(render_status('START_CAMERA'))
        if not a.no_encoder:
            encoder = UIEncoder()
            print('エンコーダー: Linux rotary-encoder + python-evdev A=GPIO5 B=GPIO6 / SW=GPIO13', flush=True)
        if a.execute_mount:
            if a.test_tracking_led:
                raise RuntimeError('実機モードでは --test-tracking-led を使用しないでください')
            mount_class = RemoteGuideMount if a.solar_goto_remote else AstroEQTrackingRelativeGoTo
            mount = mount_class(a.astroeq_port, a.arcsec_per_click,
                                       a.ra_positive_dir, a.dec_positive_dir, a.guide_fraction,
                                       cal_arcsec_per_step=a.cal_arcsec_per_step,
                                       cal_counts_multiplier=a.cal_counts_multiplier)
        if a.test_tracking_led:
            import lgpio
            gpio = lgpio
            led = gpio.gpiochip_open(0)
            gpio.gpio_claim_output(led, 20, 1)
            print('GPIO20 LED: 試験点灯 (実追尾ではありません)', flush=True)
        # 連携モードの手動微動は太陽GoToソケットへ送る（別シリアル接続なし）。
        # GPIOの回転イベントはバックグラウンドスレッドで蓄積される。
        # 旧consume()は1回に1クリックしか返さないため、メニュー以外の実機微動では
        # キューを一括取得して、短い回転バーストを1回の相対GoToにまとめる。
        pending_axis = None
        pending_clicks = 0
        pending_arcsec = 20  # 軸選択時に確定したHI/LOWの1クリック角度
        pending_first = 0.0
        pending_last = 0.0
        quiet_s = a.batch_quiet_ms / 1000.0
        max_wait_s = a.batch_max_wait_ms / 1000.0

        def flush_pending():
            nonlocal pending_axis, pending_clicks, pending_first, pending_last, pending_arcsec
            axis, clicks = pending_axis, pending_clicks
            pending_axis, pending_clicks = None, 0
            pending_first = pending_last = 0.0
            if not clicks:
                return
            arcsec = pending_arcsec
            print(f'微動一括処理: {axis} {clicks:+d}click × {arcsec}秒角 = '
                  f'{clicks * arcsec:+d}秒角 (相殺後)', flush=True)
            if mount is not None:
                # ソケット往復や赤道儀の相対GoToは同期呼び出しで数秒かかるため、
                # 別スレッドでOLEDタイトルだけを点滅させる。主スレッドの送信処理は変えない。
                # 画面は1か所から描画し、送信中は主スレッドが他の描画に入らない。
                blink_stop = threading.Event()
                ui.manual_moving = True
                ui.manual_blink_visible = True

                def redraw_manual_title():
                    try:
                        current_utc = base + timedelta(seconds=time.monotonic() - clock_start)
                        display._send_canvas(
                            render_for_screen(ui, camera, display, current_utc,
                                              a.execute_mount, calibration, guide_history,
                                              guiding, cal_message, camera_settings),
                            camera.raw, detection_method=camera.detection_method)
                    except Exception as exc:
                        print(f'微動表示更新エラー: {exc}', flush=True)

                # 開始直後にMOVING...を表示（点滅の最初のON）。
                redraw_manual_title()

                def blink_while_moving():
                    # 描画更新の最低間隔はOLED設定と同じか、それ以上にする。
                    blink_s = max(0.35, float(a.oled_interval))
                    while not blink_stop.wait(blink_s):
                        ui.manual_blink_visible = not ui.manual_blink_visible
                        redraw_manual_title()

                blink_thread = threading.Thread(target=blink_while_moving,
                                                name='astroeq-manual-moving-oled', daemon=True)
                blink_thread.start()
                try:
                    while clicks:
                        # HI / LOW のどちらも連続クリックを合算して送信する。
                        # リモート制御は1指令最大8クリック。超過分は順次送信する。
                        # ロータリー入力はevdevキューに保持され、送信完了後に再回収する。
                        chunk_limit = (min(a.batch_max_clicks, 8)
                                       if a.solar_goto_remote else a.batch_max_clicks)
                        chunk = max(-chunk_limit, min(chunk_limit, clicks))
                        if a.solar_goto_remote:
                            mount.move(axis, chunk, arcsec=arcsec)
                        elif arcsec == a.arcsec_per_click:
                            mount.move(axis, chunk)
                        else:
                            # スタンドアロン実機: 既に読み出した軸CPRから角度指定のcountsを算出。
                            cpr = mount.cpr[axis]
                            count = (cpr * arcsec * abs(chunk) + 648000) // 1296000
                            mount.move_counts(axis, count if chunk > 0 else -count,
                                              label=f'{chunk:+d}click×{arcsec}arcsec')
                        clicks -= chunk
                finally:
                    blink_stop.set()
                    blink_thread.join(timeout=max(2.0, float(a.oled_interval) + 1.0))
                    ui.manual_moving = False
                    ui.manual_blink_visible = True
                    # 指令が成功・失敗どちらで返っても通常タイトルへ戻す。
                    redraw_manual_title()
            else:
                print('実機モード無効：送信なし', flush=True)

        def request_new_guide_reference(reason):
            """新しいガイド期間を準備。手動の移動が完全に終わってから撮像して中心を固定。"""
            nonlocal guiding, guide_reference_pending, guide_reference_ready_at
            nonlocal guide_pulse_count, guide_pending_error, guide_nonimproving
            nonlocal last_guide_sample, guide_interval_anchor, guide_last_seen
            # フラッシュ完了後に呼ばれる。過去フレームの中心を次の基準点にしない。
            guiding = False
            guide_reference_pending = True
            guide_reference_ready_at = time.monotonic() + 0.7
            guide_reference_points.clear()
            guide_history.clear()
            guide_pulse_count = 0
            guide_pending_error = None
            guide_nonimproving = 0
            last_guide_sample = -1e10
            guide_interval_anchor = time.monotonic()
            guide_last_seen = guide_interval_anchor
            guide_quality.last_dec_direction = None
            guide_quality.pending_dec_direction = None
            guide_quality.pending_dec_count = 0
            guide_quality.pending = None
            camera.frames.clear()
            camera.centers.clear()
            camera.circle = None
            camera.prev = None
            print(f'GUIDE基準点再取得待ち: {reason} / CAL行列保持 / ずれチャート消去', flush=True)

        def get_encoder_events():
            # この試験版で採用済みのpython-evdev UIEncoderのキューをロックして一括回収。
            # 既存のssd1315_encoder.pyには一切変更を加えない。
            with encoder.lock:
                steps = list(encoder.rotations)
                encoder.rotations.clear()
                shorts, longs = encoder.shorts, encoder.longs
                encoder.shorts = encoder.longs = 0
            return steps, shorts, longs

        last_screen = None
        last_oled = -1e10
        last_report = -1e10
        deadline = clock_start + a.seconds if a.seconds else None
        with open(a.csv, 'w', encoding='utf-8', newline='') as fp:
            writer = csv.writer(fp)
            writer.writerow(['frame', 'time_s', 'detected', 'x_px', 'y_px', 'radius_px', 'dx_px', 'dy_px'])
            next_remote_check = -1e10
            while deadline is None or time.monotonic() < deadline:
                if a.solar_goto_remote and time.monotonic() >= next_remote_check:
                    try:
                        mount.check_tracking()
                    except (RuntimeError, OSError, ConnectionError) as exc:
                        print(f'太陽GoTo追尾終了/HOME移行: {exc}。GUIDE/CALを終了', flush=True)
                        guiding = False
                        break
                    next_remote_check = time.monotonic() + 1.0
                if encoder is not None:
                    # 画像処理やGoTo中に蓄積したイベントも一度に引き取る。
                    event_steps, short_clicks, long_clicks = get_encoder_events()
                    try:
                        # 軸切替・画面切替よりも前に、旧軸の保留要求を確定する。
                        if (short_clicks or long_clicks) and pending_clicks:
                            flush_pending()
                        for _ in range(long_clicks):
                            before_long = ui.screen
                            if before_long == SCREEN_RAW:
                                # CAMERA設定からは長押しで安全にGUIDEへ戻る。
                                camera_settings.editing = False
                                ui.screen = SCREEN_GUIDE
                                if calibration is not None and a.enable_guide_after_cal and mount is not None:
                                    request_new_guide_reference('CAMERA→GUIDE')
                                continue
                            ui.long()
                            if before_long == SCREEN_GUIDE and ui.screen == SCREEN_MANUAL:
                                # 長押しで手動微動へ: 自動補正を直ちに停止。
                                # 校正行列は消さない。手動微動後に再CALは不要。
                                guiding = False
                                guide_reference_pending = False
                                guide_reference_points.clear()
                                print('GUIDE一時停止: 微動モード / CAL行列は維持', flush=True)
                            elif before_long == SCREEN_MANUAL and ui.screen == SCREEN_GUIDE:
                                # 長押しでガイドへ戻る際、保留微動の実機移動完了後に新中心を採用。
                                if pending_clicks:
                                    flush_pending()
                                if calibration is not None and a.enable_guide_after_cal and mount is not None:
                                    request_new_guide_reference('MANUAL→GUIDE')
                                else:
                                    guiding = False
                                    guide_reference_pending = False
                                    print('GUIDE OFF: CAL未完了または自動ガイド未許可', flush=True)
                        for _ in range(short_clicks):
                            prev_screen = ui.screen
                            if prev_screen == SCREEN_RAW:
                                if camera_settings.exit_selected:
                                    # EXITは画面だけ戻す。カメラ設定完了時のGUIDE再開処理を通さず、
                                    # 同じ入力バッチの残りの短押しで別項目が実行されるのも防ぐ。
                                    ui.screen = SCREEN_GUIDE
                                    break
                                if camera_settings.short():
                                    ui.screen = SCREEN_GUIDE
                                    if calibration is not None and a.enable_guide_after_cal and mount is not None:
                                        request_new_guide_reference('CAMERA→GUIDE')
                                continue
                            ui.short()
                            if prev_screen == SCREEN_GUIDE and ui.screen == SCREEN_RAW:
                                # CAMの選択位置だけを初期化し、確定済みのガイド設定は共有する。
                                camera_settings = CameraSettings(camera, guide_settings)
                                # カメラ調整中の画像変化による誤補正を防止。
                                guiding = False
                                guide_reference_pending = False
                                guide_reference_points.clear()
                                print('CAMERA設定開始: GUIDE補正一時停止 / CAL行列維持',flush=True)
                            if prev_screen != SCREEN_CAL and ui.screen == SCREEN_CAL:
                                cal_pending = True
                            if prev_screen == SCREEN_CAL and ui.screen != SCREEN_CAL:
                                cal_pending = False

                        if ui.screen == SCREEN_MANUAL and mount is not None:
                            axis = 'RA' if ui.manual_selection < 2 else 'DEC'
                            arcsec = 180 if ui.manual_selection in (0, 2) else 20
                            now = time.monotonic()
                            for step in event_steps:
                                # 軸または速度モードの変更前の操作を混ぜない。
                                if pending_axis is not None and (pending_axis != axis or
                                                                 pending_arcsec != arcsec):
                                    flush_pending()
                                if pending_axis is None:
                                    pending_axis = axis
                                    pending_arcsec = arcsec
                                    pending_first = now
                                pending_clicks += step
                                pending_last = now
                                if abs(pending_clicks) >= a.batch_max_clicks:
                                    flush_pending()
                            # 複数操作は無条件に合算。+Nと-Nが打ち消し合えばGoTo不要。
                            # 連続回転時もmax_wait_sを超えたら適度な間隔で実行する。
                            if pending_axis is not None and (
                                abs(pending_clicks) >= a.batch_max_clicks or
                                now - pending_last >= quiet_s or
                                now - pending_first >= max_wait_s
                            ):
                                flush_pending()
                        else:
                            if pending_clicks:
                                flush_pending()
                            # CAMERA画面ではカメラ設定用の回転として処理する。
                            for step in event_steps:
                                if ui.screen == SCREEN_RAW:
                                    camera_settings.rotate(step)
                                else:
                                    ui.rotate(step)
                            if ui.screen == SCREEN_RAW and camera_settings is not None:
                                camera_settings.flush_preview()
                    except Exception as e:
                        print(f'!!! 実機微動エラー: {e}。入力を停止します', flush=True)
                        raise
                # CAL STARTが押された時にのみ実機移動を伴う校正を実行。
                # 操作を受け付けない時間を短くするためOLEDへ進捗を出す。
                if cal_pending:
                    cal_pending = False
                    calibration = None
                    guiding = False
                    guide_reference_pending = False
                    guide_reference_points.clear()
                    guide_pulse_count = 0
                    guide_pending_error = None
                    guide_nonimproving = 0
                    guide_history.clear()
                    guide_quality.last_dec_direction = None
                    guide_quality.pending_dec_direction = None
                    guide_quality.pending_dec_count = 0
                    guide_quality.pending = None
                    cal_message = 'MEASURING...'
                    display._send_canvas(render_for_screen(ui,camera,display,base,
                                                   bool(mount), calibration,guide_history,False,cal_message),
                                         camera.raw, detection_method=camera.detection_method)
                    def update_cal_display(message):
                        nonlocal cal_message
                        cal_message = message
                        display._send_canvas(render_for_screen(ui,camera,display,base,
                                            bool(mount),calibration,guide_history,False,cal_message),
                                             camera.raw, detection_method=camera.detection_method)

                    def report_cal(msg):
                        print(msg, flush=True)

                    def show_cal_progress(axis, direction, index, arcsec, target):
                        update_cal_display((axis, direction, index, arcsec, target))

                    if mount is None:
                        report_cal('CAL: 実機モード無効。モーター移動は行いません')
                        update_cal_display('NEEDS MOUNT')
                        ui.calibration_message = 'NEEDS MOUNT'
                        ui.screen = SCREEN_CAL
                    else:
                        try:
                            calibration = run_calibration(mount, camera, report=report_cal,
                                                          drift_enabled=a.cal_drift == 'on',
                                                          progress=show_cal_progress)
                            guiding = False  # CAL直後の旧測定位置ではガイドを開始しない
                            guide_pulse_count = 0
                            guide_pending_error = None
                            guide_nonimproving = 0
                            guide_quality.last_dec_direction = None
                            guide_quality.pending = None
                            guide_interval_anchor = time.monotonic()
                            guide_last_seen = guide_interval_anchor
                            ui.screen = SCREEN_GUIDE
                            if a.enable_guide_after_cal:
                                request_new_guide_reference('CAL完了後の初回GUIDE')
                            print('CAL成功 / GUIDE:', '基準点取得待ち' if guide_reference_pending else 'OFF (明示許可なし)', flush=True)
                        except Exception as exc:
                            # CAL本体だけでなく、成功後の基準点要求などの失敗でも無効化する。
                            calibration = None
                            guiding = False
                            guide_reference_pending = False
                            guide_reference_points.clear()
                            print(f'!!! CAL失敗: {exc}。ガイド補正は無効',flush=True)
                            cal_message = 'CAL FAILED'
                            ui.screen = SCREEN_CAL
                now_mono = time.monotonic()
                now_utc = base + timedelta(seconds=now_mono - clock_start)
                if ui.screen != last_screen:
                    print('表示:', ui.screen, 'UTC:', now_utc.isoformat(timespec='seconds'), flush=True)
                    last_screen = ui.screen
                    last_oled = -1e10  # first frame for new screen is immediate
                camera.update()
                if ui.screen == SCREEN_RAW and camera_settings is not None:
                    camera_settings.report_live_frame()
                if guide_reference_pending and calibration is not None:
                    if time.monotonic() >= guide_reference_ready_at and camera.circle is not None:
                        # 直近の新しいフレームだけで現在の太陽像中心を測定する。
                        guide_reference_points.append(tuple(camera.circle[:2]))
                        if len(guide_reference_points) == guide_reference_points.maxlen:
                            samples = np.asarray(guide_reference_points, dtype=float)
                            center = np.median(samples, axis=0)
                            spread = float(np.max(np.linalg.norm(samples - center, axis=1)))
                            if spread <= 5.0:
                                # guide pulse計算・円板誤差チャートが参照する点を揃える。
                                # 校正行列 / inverse / パルス換算値は変更しない。
                                calibration.reference = center.copy()
                                guide_reference_pending = False
                                guiding = True
                                guide_epoch += 1
                                guide_history.clear()
                                guide_pulse_count = 0
                                guide_pending_error = None
                                guide_nonimproving = 0
                                guide_quality.last_dec_direction = None
                                guide_quality.pending_dec_direction = None
                                guide_quality.pending_dec_count = 0
                                guide_quality.pending = None
                                last_guide_sample = -1e10
                                guide_interval_anchor = time.monotonic()
                                guide_last_seen = guide_interval_anchor
                                print(f'GUIDE開始 #{guide_epoch}: 固定基準 X={center[0]:.2f} Y={center[1]:.2f} '
                                      f'/ 安定性={spread:.2f}px / チャート0基準で再開', flush=True)
                                guide_quality.log('GUIDE_NEW_REFERENCE', point=tuple(center),
                                                  target=tuple(center),
                                                  note=f'epoch={guide_epoch};spread_px={spread:.2f}')
                            else:
                                # 画像乱れや移動中に基準点を誤取得しない。
                                guide_reference_points.clear()
                                print(f'GUIDE基準点保留: 太陽像中心が不安定 {spread:.2f}px', flush=True)
                if camera.gray is not None:
                    h, w = camera.gray.shape
                    if camera.circle:
                        x, y, r = camera.circle
                        writer.writerow([camera.n, f'{now_mono-clock_start:.3f}', 1,
                                         f'{x:.3f}', f'{y:.3f}', f'{r:.3f}',
                                         f'{x-w/2:.3f}', f'{y-h/2:.3f}'])
                    else:
                        writer.writerow([camera.n, f'{now_mono-clock_start:.3f}', 0, '', '', '', '', ''])
                now_mono = time.monotonic()
                if guiding and calibration is not None and camera.circle is not None and now_mono - last_guide_sample >= 1.0:
                    point = camera.circle[:2]
                    estimate = calibration.error_arcsec(point)
                    guide_history.append((float(estimate[0]), float(estimate[1])))
                    last_guide_sample = now_mono
                    # この段階では観測・グラフ記録のみ。手動相対GoToをGUIDE補正に流用しない。
                # ガイド補正は明示許可かつCAL成功後だけ。通常は測定・グラフのみ。
                # 画像上の基準はCAL後の最終中心。手動微動/再CALで失効させる。
                if guiding and calibration is not None:
                    if camera.circle is not None:
                        guide_last_seen = now_mono
                        guide_quality.observe(camera.circle[:2])
                        if guide_pending_error is not None:
                            present_error = float(np.linalg.norm(
                                np.asarray(camera.circle[:2]) - calibration.reference))
                            if present_error >= guide_pending_error - 0.5:
                                guide_nonimproving += 1
                            else:
                                guide_nonimproving = 0
                            guide_pending_error = None
                            if guide_nonimproving == 3:
                                # 非改善は警告・診断用。連続ガイドはここでは止めない。
                                print('!!! GUIDE警告: 補正後の画像誤差が3回連続で改善しません（ガイド継続）', flush=True)
                    if now_mono - guide_last_seen > 3.0:
                        guiding = False
                        print('!!! GUIDE停止: 円板検出が3秒以上途絶えました', flush=True)
                    elif camera.circle is not None and guide_settings.ready(now_mono, guide_interval_anchor):
                        try:
                            plan = choose_guide_pulse(
                                calibration, camera.circle[:2],
                                gain=a.guide_gain, deadband_px=a.guide_deadband_px,
                                max_error_px=a.guide_max_error_px,
                                max_pulse_ms=guide_settings.max_pulse_ms,
                                min_pulse_ms=a.guide_min_pulse_ms)
                            if plan is not None:
                                if a.guide_max_pulses > 0 and guide_pulse_count >= a.guide_max_pulses:
                                    guiding = False
                                    print('!!! GUIDE停止: 補正パルス回数上限', flush=True)
                                else:
                                    axis, direction, pulse_ms, error_px = plan
                                    if axis == 'DEC' and not guide_quality.allow_dec(direction):
                                        print(f'GUIDE DEC反転保留: {direction:+d}方向の要求を次回再確認します（追加駆動なし）', flush=True)
                                        guide_quality.log('DEC_REVERSAL_WAIT', point=camera.circle[:2],
                                            target=calibration.reference, axis=axis, direction=direction,
                                            note='2回連続要求が必要')
                                        guide_interval_anchor = time.monotonic()
                                    else:
                                        before = tuple(camera.circle[:2])
                                        target = tuple(calibration.reference)
                                        print(f'GUIDE実行 #{guide_pulse_count+1}: {axis} {direction:+d} '
                                              f'{pulse_ms}ms / 画像誤差={error_px:.2f}px', flush=True)
                                        # RAは追尾速度一時変更、DECは短時間連続回転。
                                        # 周期は送信開始→次の送信開始。同期応答の後に撮像履歴を
                                        # 捨てるため、周期満了でも新しい円盤中心を得るまで送信しない。
                                        begin = time.monotonic()
                                        guide_interval_anchor = begin
                                        mount.pulse_guide(axis, direction, pulse_ms)
                                        elapsed = time.monotonic() - begin
                                        guide_quality.sent(axis=axis, direction=direction,
                                            pulse_no=guide_pulse_count+1, pulse_ms=pulse_ms,
                                            point=before, target=target, duration_s=elapsed)
                                        guide_pending_error = error_px
                                        guide_pulse_count += 1
                                        # 次の観測まで過去フレームを使わない。
                                        camera.frames.clear()
                                        camera.centers.clear()
                                        camera.circle = None
                                        camera.prev = None
                        except Exception as exc:
                            guiding = False
                            print(f'!!! GUIDE停止: {exc}。追加駆動は行いません', flush=True)
                now_mono = time.monotonic()
                if now_mono - last_oled >= a.oled_interval:
                    display._send_canvas(render_for_screen(ui, camera, display, now_utc, a.execute_mount,
                                                          calibration, guide_history, guiding, cal_message, camera_settings),
                                         camera.raw, detection_method=camera.detection_method)
                    if startup_pending:
                        display.device.persist = False
                        startup_pending = False
                    last_oled = now_mono
                if now_mono - last_report >= 5.0:
                    if camera.gray is not None and camera.circle is not None:
                        h, w = camera.gray.shape
                        x, y, r = camera.circle
                        print(f'検出: X={x-w/2:+.2f} Y={y-h/2:+.2f} R={r:.2f} / 画面={ui.screen}', flush=True)
                    elif camera.gray is not None:
                        print(f'検出: NO DISK / 画面={ui.screen}', flush=True)
                    last_report = now_mono
            if pending_clicks:
                print(f'未処理の微動要求 {pending_axis} {pending_clicks:+d}click: 終了のため送信しません', flush=True)
    except KeyboardInterrupt:
        print('ユーザー操作で終了', flush=True)
    finally:
        if guide_quality is not None:
            guide_quality.close()
        if camera is not None:
            camera.close()
        if encoder is not None:
            encoder.close()
        if mount is not None:
            mount.close()
        if display is not None:
            display.close()
        if led is not None:
            try:
                gpio.gpio_write(led, 20, 0)
            finally:
                gpio.gpiochip_close(led)
        if camera is not None and camera.gray is not None:
            save_overlay(camera.gray, camera.circle, a.output,
                         f'detected={camera.success} / frame={camera.n}')
            print(f'プレビュー保存: {a.output}', flush=True)
        print(f'CSV保存: {a.csv}, 詳細ガイド: {a.guide_log} / 検出成功フレーム={camera.success if camera else 0}', flush=True)
        print('終了: K1/K2停止指令を試行。物理停止は別途確認してください。' if a.execute_mount else '終了: AstroEQ通信・モーター駆動なし。', flush=True)


if __name__ == '__main__':
    main()
