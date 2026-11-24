#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AstroEQ CAMERA CAL行列から短時間ガイドパルスを選ぶ純粋計算モジュール。

CAL行列の列はRA+/DEC+で得た「px / CALパルス」。
固定円板の模擬太陽では通常RA追尾によって画像が動くため、
RA-補正が太陽像を止められるとは限らない。無制限の補正は禁止。
このファイルはUSB・GPIOを開かず、実際の駆動は呼出元が明示許可した場合のみ。
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import numpy as np
from astroeq_guide_socket import MIN_GUIDE_PULSE_MS


@dataclass
class GuideSettings:
    ra_max_ms: int = 500
    dec_max_ms: int = 500
    period_s: int = 5

    PATH = Path.home() / '.config' / 'astroeq-solar-goto' / 'guide_settings.json'
    RANGES = {'ra_max_ms': (100, 2000, 100), 'dec_max_ms': (100, 2000, 100),
              'period_s': (1, 30, 1)}

    def __post_init__(self):
        for key, (lo, hi, step) in self.RANGES.items():
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi or (value - lo) % step:
                raise ValueError(f'GUIDE設定が範囲外です: {key}={value}')

    @classmethod
    def load(cls, path, first_run=None):
        try:
            values = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(values, dict) or values.keys() != cls.RANGES.keys():
                raise ValueError('GUIDE設定の項目が一致しません')
            return cls(**values)
        except FileNotFoundError:
            return first_run if first_run is not None else cls()
        except (OSError, ValueError, TypeError) as exc:
            print(f'GUIDE設定の読込失敗。初期値で起動: {exc}', flush=True)
            return cls()

    def commit(self, path, key, value):
        # JSONを同じディレクトリに書き、完成後に一度だけ置換する。
        # 保存に失敗した場合は、制御に公開している直前の確定値も維持する。
        updated = replace(self, **{key: value})
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(updated), stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        setattr(self, key, value)

    @property
    def max_pulse_ms(self):
        return {'RA': self.ra_max_ms, 'DEC': self.dec_max_ms}

    def ready(self, now, anchor):
        """前回の補正開始または安全上の待機起点から、現在の設定周期を測る。"""
        return now - anchor >= self.period_s


def choose_guide_pulse(calibration, point, *, gain=0.5, deadband_px=2.0,
                       max_error_px=35.0, max_pulse_ms=500, min_pulse_ms=MIN_GUIDE_PULSE_MS):
    """(axis, sign, milliseconds, absolute_error_px) またはNoneを返す。

    画像誤差をCALパルス座標系に変換し、2軸同時でなく誤差寄与の大きい軸から
    1パルスだけ補正する。方向はCALで実測した正負の符号に従う。
    画面誤差が大きすぎる場合は駆動せず例外で上位に知らせる。
    """
    limits = max_pulse_ms if isinstance(max_pulse_ms, Mapping) else dict(RA=max_pulse_ms, DEC=max_pulse_ms)
    if not all(MIN_GUIDE_PULSE_MS <= min_pulse_ms <= limits[axis] for axis in ('RA', 'DEC')):
        raise ValueError(f'GUIDEパルス幅の下限は{MIN_GUIDE_PULSE_MS}msです')
    p = np.asarray(point, dtype=float)
    ref = np.asarray(calibration.reference, dtype=float)
    if p.shape != (2,) or ref.shape != (2,) or not np.all(np.isfinite(p)):
        raise ValueError('円板中心座標が不正です')
    err = ref - p
    err_px = float(np.linalg.norm(err))
    if not math.isfinite(err_px) or err_px > max_error_px:
        raise ValueError(f'画像誤差が許容範囲外です: {err_px:.2f}px')
    units = np.asarray(calibration.inverse @ err, dtype=float)
    vectors = np.asarray(calibration.matrix, dtype=float)
    if units.shape != (2,) or vectors.shape != (2, 2) or not np.all(np.isfinite(units)):
        raise ValueError('CAL行列が不正です')
    weights = np.array([abs(units[i]) * np.linalg.norm(vectors[:, i]) for i in range(2)])
    idx = int(np.argmax(weights))
    if weights[idx] <= deadband_px:
        return None
    direction = 1 if units[idx] > 0 else -1
    axis = 'RA' if idx == 0 else 'DEC'
    # 逆行列の単位は校正パルス数。1000msのCAL時間で換算し、CPR比は掛けない。
    duration_ms = int(round(abs(units[idx]) * calibration.steps_pulse_ms * gain))
    duration_ms = max(min_pulse_ms, min(limits[axis], duration_ms))
    return (axis, direction, duration_ms, err_px)
