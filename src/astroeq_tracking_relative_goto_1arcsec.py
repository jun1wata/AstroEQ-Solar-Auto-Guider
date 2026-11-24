#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AstroEQ追尾・相対角度微動の試験版。

RA: 通常追尾をK1で停止→現在位置j1から相対GoTo(G12d/H1/J1)
    →停止を確認→通常追尾G110/I1/J1に復帰。
DEC: RA追尾は維持→現在位置j2から相対GoTo(G22d/H2/J2)→停止。

手動はCPRと指定秒角から1ステップ分を算出。CALは速度と時間を指定。
手動は1ステップ整数counts×クリック数。累積端数補正なし。
j1/j2はモーター実角度のセンサー値ではない。
従来の一括GoToは実機動作確認済み。時間指定CALは実機未検証。
"""
import subprocess
import math
import time
import serial
from astroeq_guide_socket import MIN_GUIDE_PULSE_MS, record_calibration_pulse
from astroeq_sun_tracking_generic_ch340 import (
    BAUD, find_astroeq_port, read_controller_params, read24, send,
    prepare_axis, hex24, status_stopped, signed24_delta,
)

FULL_ARCSEC = 360 * 3600
SIDEREAL_DAY = 86164.0905
SOLAR_DAY = 86400.0


def counts_per_step(cpr, arcsec):
    """指定角（秒角）に相当する1ステップのカウントを四捨五入（非負のみ）。"""
    if not isinstance(cpr, int) or cpr <= 0 or not math.isfinite(arcsec) or arcsec <= 0:
        raise ValueError('CPRと秒角には正の有限値が必要です')
    return math.floor(cpr * arcsec / FULL_ARCSEC + 0.5)


class AstroEQTrackingRelativeGoTo:
    def __init__(self, port=None, arcsec=1.0, ra_positive_dir=0,
                 dec_positive_dir=1, guide_fraction=0.5, cal_arcsec_per_step=0.5,
                 cal_counts_multiplier=1, cal_pulse_ms=1000):
        # guide_fractionはCALパルス速度に使用。従来の手動微動には影響しない。
        # 複数クリックを一括移動する場合も、AstroEQは1回のGoToを実行する。
        if not 0.5 <= arcsec <= 30.0:
            raise ValueError('軸角は0.5～30秒角/click')
        if ra_positive_dir != 0:
            raise ValueError('RA通常追尾の方向0に合わせ、--ra-positive-dir 0を指定してください')
        if dec_positive_dir not in (0, 1):
            raise ValueError('DEC方向は0または1')
        if subprocess.run(['systemctl', 'is-active', '--quiet', 'astroeq-sun.service'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0:
            raise RuntimeError('astroeq-sun.serviceが稼働中です')
        self.ser = None
        self.motion_attempted = False
        self.ra_tracking_attempted = False
        self.arcsec = arcsec
        if not 0 < cal_arcsec_per_step <= 2:
            raise ValueError('校正角は0超～2秒角/step')
        self.cal_arcsec_per_step = cal_arcsec_per_step
        if type(cal_counts_multiplier) is not int or not 1 <= cal_counts_multiplier <= 16:
            raise ValueError('CAL counts倍率は整数1～16')
        self.cal_counts_multiplier = cal_counts_multiplier
        if not math.isfinite(guide_fraction) or not 0.25 <= guide_fraction <= 0.95:
            raise ValueError('ガイド速度は恒星時の0.25～0.95倍')
        if type(cal_pulse_ms) is not int or not 500 <= cal_pulse_ms <= 2000:
            raise ValueError('CALパルス時間は500～2000ms')
        self.guide_fraction = guide_fraction
        self.cal_pulse_ms = cal_pulse_ms
        # 角度は名目値。画像上の実変位・方向はCALで測定する。
        self.cal_pulse_arcsec = FULL_ARCSEC / SIDEREAL_DAY * guide_fraction * cal_pulse_ms / 1000.0
        self.ra_positive_dir = ra_positive_dir
        self.dec_positive_dir = dec_positive_dir
        self.timeout = 8.0  # 異常時の無限待ちを防ぐ
        self._diag_started = time.monotonic()  # 診断専用の経過時刻基準
        self._dec_probe_last = None  # DEC各コマンド前後のRAカウンター差分
        # CPR取得後、各軸の1ステップ単位で四捨五入する（位相・残差蓄積なし）。
        self.manual_counts = {}
        self.cal_counts = {}
        try:
            port = find_astroeq_port(port)
            self.ser = serial.Serial(port, BAUD, timeout=2, write_timeout=2)
            time.sleep(2.0)  # Arduino Mega USBシリアルオープン時のリセット対策
            ra_cpr, dec_cpr = read_controller_params(self.ser)
            ra_timer = read24(self.ser, 'b1')
            dec_timer = read24(self.ser, 'b2')
            s1, s2 = send(self.ser, 'f1'), send(self.ser, 'f2')
            print(f'初期軸状態 RA={s1} DEC={s2}', flush=True)
            if not ((s1 == '100' or status_stopped(s1)) and
                    (s2 == '100' or status_stopped(s2))):
                raise RuntimeError('起動前に両軸停止が必要（他プログラムの追尾には接続しません）')
            self.cpr = {'RA': ra_cpr, 'DEC': dec_cpr}
            if any(not 0 < value <= 0xFFFFFF for value in self.cpr.values()):
                raise RuntimeError(f'CPRが不正です: {self.cpr}')
            self.manual_counts = {axis: counts_per_step(cpr, self.arcsec)
                                  for axis, cpr in self.cpr.items()}
            # 1ステップ整数化を先に行い、CAL専用の実験倍率を掛ける。
            # 手動微動のcountsとRA追尾周期には一切影響しない。
            self.cal_counts = {axis: counts_per_step(cpr, self.cal_arcsec_per_step)
                               * self.cal_counts_multiplier for axis, cpr in self.cpr.items()}
            if any(n <= 0 for n in (*self.manual_counts.values(), *self.cal_counts.values())):
                raise RuntimeError('指定秒角に対する1ステップのcountsが0です。移動指令は実行しません')
            self.step_counts = dict(self.manual_counts)  # 旧表示との互換
            self.normal_period = round(ra_timer * SOLAR_DAY / ra_cpr)
            self.ra_fast_period = round(self.normal_period / (1.0 + self.guide_fraction))
            # RA－でガイド速度が通常追尾以上なら逆回転が必要。
            # 既存のI1周期変更のみでは逆転できないため、その方向の実行を禁止。
            self.ra_slow_period = (round(self.normal_period / (1.0 - self.guide_fraction))
                                   if self.guide_fraction < 1.0 else None)
            self.dec_guide_period = round(dec_timer * SIDEREAL_DAY / (dec_cpr * self.guide_fraction))
            if not all(1 <= period <= 0xFFFFFF for period in (self.normal_period, self.ra_fast_period, self.dec_guide_period)):
                raise RuntimeError('RA追尾周期が不正です')
            print(f'CPR算出counts: CPR RA={ra_cpr} DEC={dec_cpr} / '
                  f'手動 {self.arcsec:g}秒角/click: RA={self.manual_counts["RA"]} DEC={self.manual_counts["DEC"]} / '
                  f'追尾周期={self.normal_period}', flush=True)
            # 実機試験A/Bで確認した順序: RA/DECを先行初期化してからRA追尾を開始する。
            # RA追尾中に初めてF2を送るとRAカウンターが停止したため、ここで必ず済ませる。
            prepare_axis(self.ser, 1)
            prepare_axis(self.ser, 2)
            print('両軸先行初期化完了: RA=', send(self.ser, 'f1'),
                  'DEC=', send(self.ser, 'f2'), flush=True)
            self._start_tracking()
            print('RA追尾開始（111）/ 相対GoTo時だけRA追尾を一時停止', flush=True)
        except BaseException:
            self.close()
            raise

    def _diag_axes(self, stage, positions=False):
        """状態の読み取り専用診断。診断の失敗で通常の移動制御を変更しない。"""
        elapsed = time.monotonic() - self._diag_started
        try:
            ra = send(self.ser, 'f1')
            dec = send(self.ser, 'f2')
            extra = ''
            if positions:
                p1 = read24(self.ser, 'j1')
                p2 = read24(self.ser, 'j2')
                extra = f' j1={p1} j2={p2}'
            print(f'[状態診断] +{elapsed:.3f}s {stage}: RA(f1)={ra} DEC(f2)={dec}{extra}',
                  flush=True)
        except Exception as exc:
            print(f'[状態診断] +{elapsed:.3f}s {stage}: 取得失敗 {exc}', flush=True)

    def _probe_dec(self, stage, reset=False):
        """DECコマンド境界の読み取り専用診断（モーター指令は出さない）。

        各時点のf1/f2・j1/j2と、直前の観測からのRA位置変化を記録する。
        状態111のままj1が停止する箇所を切り分けるための試験専用。
        """
        try:
            now = time.monotonic()
            f1 = send(self.ser, 'f1')
            f2 = send(self.ser, 'f2')
            j1 = read24(self.ser, 'j1')
            j2 = read24(self.ser, 'j2')
            prev = None if reset else self._dec_probe_last
            if prev is None:
                difference = '初回'
            else:
                old_t, old_j1 = prev
                dt = now - old_t
                dj = signed24_delta(j1, old_j1)
                difference = f'前回比 dt={dt:.3f}s Δj1={dj:+d}counts'
            self._dec_probe_last = (now, j1)
            print(f'[DECコマンド診断] +{now-self._diag_started:.3f}s {stage}: '
                  f'RA(f1)={f1} DEC(f2)={f2} j1={j1} j2={j2} / {difference}',
                  flush=True)
        except Exception as exc:
            print(f'[DECコマンド診断] {stage}: 取得失敗 {exc}', flush=True)

    def _wait_stopped(self, axis):
        deadline = time.monotonic() + self.timeout
        hits = 0
        while time.monotonic() < deadline:
            s = send(self.ser, f'f{axis}')
            hits = hits + 1 if status_stopped(s) else 0
            if hits >= 2:
                return
            time.sleep(0.12)
        raise TimeoutError(f'軸{axis}が{self.timeout:g}秒以内に停止しません')

    def _require_tracking(self):
        status = send(self.ser, 'f1')
        if status != '111':
            raise RuntimeError(f'RA追尾状態111ではありません: {status}')

    def _start_tracking(self):
        prepare_axis(self.ser, 1)
        send(self.ser, 'G110')
        send(self.ser, 'I1' + hex24(self.normal_period))
        self.ra_tracking_attempted = True
        send(self.ser, 'J1')
        time.sleep(0.25)
        self._require_tracking()
        self._diag_axes('RA追尾開始/再開後', positions=True)

    def _stop_axis(self, axis):
        """停止を確認する。追尾RAのK1応答だけで停止済みとは判断しない。

        K1後にRAが111を維持した実機事象の切り分けのため、
        未停止の場合に限り最大3回Kを送る。8秒で失敗として上位へ返す。
        """
        self._diag_axes(f'K{axis}送信直前', positions=True)
        stop_t0 = time.monotonic()
        deadline = stop_t0 + self.timeout
        attempts = 0
        next_retry = 0.0
        last_report = 0.0
        consecutive_stopped = 0
        last_status = None
        while time.monotonic() < deadline:
            now = time.monotonic()
            if attempts == 0 or (attempts < 3 and now >= next_retry):
                attempts += 1
                send(self.ser, f'K{axis}')
                next_retry = time.monotonic() + 1.5
                print(f'軸{axis}停止指令: K{axis} #{attempts} '                      f'停止開始から{time.monotonic()-stop_t0:.3f}s', flush=True)
            status = send(self.ser, f'f{axis}')
            if status_stopped(status):
                consecutive_stopped += 1
                if consecutive_stopped >= 2:
                    print(f'軸{axis}停止確認: status={status} K回数={attempts} '                          f'経過={time.monotonic()-stop_t0:.3f}s', flush=True)
                    self._diag_axes(f'K{axis}後/停止確認', positions=True)
                    return
            else:
                consecutive_stopped = 0
            now = time.monotonic()
            if status != last_status or now - last_report >= 1.0:
                print(f'軸{axis}停止待ち: status={status} K回数={attempts} '                      f'経過={now-stop_t0:.3f}s', flush=True)
                self._diag_axes(f'K{axis}後/停止待ち', positions=False)
                last_status = status
                last_report = now
            time.sleep(0.12)
        self._diag_axes(f'K{axis}停止タイムアウト', positions=True)
        raise TimeoutError(
            f'軸{axis}が{self.timeout:g}秒以内に停止しません '
            f'(最終状態={last_status}, K送信={attempts}回)'
        )

    def pulse_guide(self, axis, direction, duration_ms, *, calibration=False):
        """ガイドパルス: RAは追尾中I1変更、DECはG21d/I2/J2/K2。

        手動相対GoToには影響しない。実機コマンドの応答後に時刻を計るため、
        実際の光学的移動時間や移動角を保証しない。
        """
        if axis not in ('RA', 'DEC') or direction not in (-1, 1):
            raise ValueError('軸はRA/DEC、方向は+1/-1')
        if type(duration_ms) is not int or not MIN_GUIDE_PULSE_MS <= duration_ms <= 2000:
            raise ValueError('パルス時間は100～2000ms')
        self._require_tracking()
        t0 = None
        if axis == 'RA':
            if direction < 0 and self.ra_slow_period is None:
                raise RuntimeError('RA－補正はguide-fraction >= 1.0で逆回転が必要です。'
                                   '現在のI1周期変更方式では未対応のため送信しません')
            if calibration:
                # 太陽時周期に対する速度比へ変換し、恒星時0.95倍相当の差を加える。
                fraction = self.guide_fraction * SOLAR_DAY / SIDEREAL_DAY
                period = round(self.normal_period / (1 + direction * fraction))
            else:
                period = self.ra_fast_period if direction > 0 else self.ra_slow_period
            if not 1 <= period <= 0xFFFFFF:
                raise ValueError('RAガイド周期が範囲外です')
            changed = False
            try:
                # 追尾は停止しない。速度を一時的に1+fまたは1-f倍とする。
                changed = True
                send(self.ser, 'I1' + hex24(period))
                t0 = time.monotonic()
                time.sleep(duration_ms / 1000.0)
            finally:
                if changed:
                    try:
                        # 送信失敗でも追尾復旧できたとみなさない。
                        send(self.ser, 'I1' + hex24(self.normal_period))
                    except BaseException:
                        print('!!! RA通常追尾速度へ戻せません。直ちに実機安全確認', flush=True)
                        raise
            self._require_tracking()
        else:
            state = send(self.ser, 'f2')
            if not status_stopped(state):
                raise RuntimeError(f'DECは停止状態ではありません: {state}')
            # 起動時にF2済み。RA追尾中はF2で再初期化しない。
            bit = self.dec_positive_dir if direction > 0 else 1 - self.dec_positive_dir
            started = False
            try:
                send(self.ser, f'G21{bit}')
                send(self.ser, 'I2' + hex24(self.dec_guide_period))
                started = True
                send(self.ser, 'J2')
                t0 = time.monotonic()
                time.sleep(duration_ms / 1000.0)
            finally:
                if started:
                    # DECだけ停止。K1は送らずRA追尾を維持する。
                    try:
                        send(self.ser, 'K2')
                    except BaseException:
                        print('!!! DEC停止指令失敗。独立した電源遮断手段で安全確保', flush=True)
                        raise
                    self._wait_stopped(2)
            self._require_tracking()
        elapsed = time.monotonic() - t0 if t0 is not None else float('nan')
        print(f'GUIDE {axis} {direction:+d} {duration_ms}ms 速度={self.guide_fraction:g}x恒星時 '
              f'終了確認まで={elapsed:.3f}s', flush=True)

    def move_cal_pulse(self, axis, direction):
        """CAL専用。通常のパルス処理を挟んで両軸の位置を読む。"""
        return record_calibration_pulse(
            self.ser, read24, self.normal_period, axis,
            lambda: self.pulse_guide(axis, direction, self.cal_pulse_ms, calibration=True))

    def move(self, axis, step):
        """手動: CPRから求めた整数counts/クリックにクリック数を乗算。"""
        if axis not in ('RA', 'DEC') or type(step) is not int or not (1 <= abs(step) <= 32):
            raise ValueError('入力はRA/DEC、符号付き1～32クリックのみ')
        return self.move_counts(axis, step * self.manual_counts[axis], label=f'{step:+d}click')

    def move_cal_steps(self, axis, steps):
        """CAL: CPRから求めた整数counts/校正ステップにステップ数を乗算。"""
        if axis not in ('RA', 'DEC') or type(steps) is not int or not (1 <= abs(steps) <= 16):
            raise ValueError('校正入力はRA/DEC、符号付き1～16ステップのみ')
        return self.move_counts(axis, steps * self.cal_counts[axis], label=f'CAL {steps:+d}step')

    def move_counts(self, axis, requested, label=None):
        """現在位置を基準に指定された整数カウントのみ移動する。端数補正なし。"""
        if axis not in ('RA', 'DEC') or type(requested) is not int or requested == 0:
            raise ValueError('RA/DECと0以外の整数countsを指定してください')
        if abs(requested) > 0x7FFFFF:
            raise ValueError('相対移動要求が範囲外')
        label = label or f'{requested:+d}counts'
        self._require_tracking()
        self._diag_axes(f'{axis}微動開始直前', positions=True)
        n = 1 if axis == 'RA' else 2
        direction_positive = (self.ra_positive_dir if axis == 'RA'
                              else self.dec_positive_dir)
        direction = direction_positive if requested > 0 else 1 - direction_positive
        if abs(requested) > 0x7FFFFF:
            raise RuntimeError('相対移動要求が範囲外')
        tracking_paused = False
        go_started = False
        move_succeeded = False
        before = None
        try:
            if axis == 'RA':
                self._stop_axis(1)
                tracking_paused = True
                self.ra_tracking_attempted = False
            else:
                self._diag_axes('DEC移動直前', positions=True)
                self._probe_dec('DEC準備前', reset=True)
                s2 = send(self.ser, 'f2')
                if s2 == '100':
                    # RA追尾中のF2でRA位置カウント停止が再現したため、絶対にF2を送らない。
                    raise RuntimeError('DECが未初期化(100)です。RA追尾中のF2は禁止。'
                                       '全軸停止と安全を確認してからプログラムを再起動してください')
                elif not status_stopped(s2):
                    raise RuntimeError(f'DEC停止状態ではありません: {s2}')
            before = read24(self.ser, f'j{n}')
            print(f'相対GoTo準備: {axis} 入力={label} 現在位置={before} '
                  f'要求={requested:+d}counts 方向bit={direction}', flush=True)
            send(self.ser, f'G{n}2{direction}')
            if axis == 'DEC':
                self._probe_dec('G2直後')
            send(self.ser, f'H{n}' + hex24(abs(requested)))
            if axis == 'DEC':
                self._probe_dec('H2直後')
            self.motion_attempted = True
            go_started = True
            send(self.ser, f'J{n}')
            if axis == 'DEC':
                self._probe_dec('J2直後')
            self._wait_stopped(n)
            after = read24(self.ser, f'j{n}')
            delta = signed24_delta(after, before)
            # 実カウントと符号の食い違いが大きい場合は停止し、異常を知らせる。
            expected_sign = 1 if direction == 0 else -1
            if delta * expected_sign < -2 or abs(delta) > abs(requested) + 25:
                raise RuntimeError(f'{axis}位置変化異常: 要求={requested:+d} 実測={delta:+d}')
            if axis == 'DEC':
                self._probe_dec('K2直前')
            self._stop_axis(n)
            if axis == 'DEC':
                self._probe_dec('K2直後')
            go_started = False
            self.motion_attempted = False
            move_succeeded = True
            if axis == 'DEC':
                self._diag_axes('DEC移動完了直後', positions=True)
            print(f'相対GoTo完了: {axis} {label} / 要求={requested:+d}counts '
                  f'位置カウント変化={delta:+d}counts '
                  '(実回転角の測定値ではありません)', flush=True)
        except BaseException:
            # 失敗時は自動で追尾復帰しない。必ず停止してエラーを表へ伝える。
            for k in (2, 1):
                try:
                    send(self.ser, f'K{k}')
                except Exception as exc:
                    print(f'!!! 軸{k}停止指令失敗: {exc}', flush=True)
            self.ra_tracking_attempted = False
            raise
        finally:
            if axis == 'RA' and tracking_paused and move_succeeded:
                # 成功後だけ通常追尾へ復帰。動作中なら再始動しない。
                try:
                    if not self.motion_attempted:
                        self._start_tracking()
                        print('RA追尾復帰: 状態111を確認', flush=True)
                except Exception as exc:
                    print(f'!!! RA追尾復帰失敗: {exc}。モーター停止・再確認が必要', flush=True)
                    raise

    def close(self):
        if self.ser is None:
            return
        for axis in (2, 1):
            try:
                send(self.ser, f'K{axis}')
            except Exception as exc:
                print(f'!!! 軸{axis}停止指令失敗: {exc}', flush=True)
        try:
            print('終了時状態 RA=', send(self.ser, 'f1'),
                  'DEC=', send(self.ser, 'f2'), flush=True)
        except Exception as exc:
            print(f'!!! 状態取得失敗: {exc}', flush=True)
        self.ser.close()
        self.ser = None
