#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""太陽像の実測変位から RA/DEC のガイド応答を校正する。"""
from datetime import datetime, timedelta, timezone
import json
import time
import numpy as np
from astroeq_guide_socket import counter_delta


class CalibrationError(RuntimeError):
    pass


class CameraAxisCalibration:
    def __init__(self, axis_vectors, reference, samples, steps, cal_arcsec_per_step=0.5, *, cpr=None, manual_counts=None, cal_counts=None):
        self.matrix = np.column_stack(axis_vectors).astype(float)  # px / 校正パルス、RA・DEC の順
        self.inverse = np.linalg.inv(self.matrix)
        self.reference = np.asarray(reference, dtype=float)
        self.samples = samples
        self.steps = steps
        self.steps_pulse_ms = 1000  # CAL完了時にrun_calibrationで実値を代入
        self.cal_arcsec_per_step = cal_arcsec_per_step
        # 分解能に応じた整数化により、RA/DECで実際の単位比が変わる。
        # 校正ステップから手動クリックへの変換は秒角比ではなくcounts比を使う。
        self.cpr = cpr
        self.manual_counts = manual_counts
        self.cal_counts = cal_counts

    def error_arcsec(self, point):
        # ガイド対象が基準位置からどちらの軸へ何秒角ずれたかを推定
        v = self.inverse @ (np.asarray(point, dtype=float) - self.reference)
        if self.cpr is not None and self.cal_counts is not None:
            return v * np.array([self.cal_counts[axis] * 360 * 3600 / self.cpr[axis]
                                 for axis in ('RA', 'DEC')])
        if hasattr(self, 'pixel_scale_arcsec'):
            # RA/DEC成分は「それぞれの校正パルス数」単位のため、軸別に秒角換算する。
            return v * np.asarray([self.cal_pulse_arcsec['RA'], self.cal_pulse_arcsec['DEC']])
        return v * self.cal_arcsec_per_step

    def corrective_clicks(self, point, limit, manual_arcsec_per_click=1.0):
        correction = self.inverse @ (self.reference - np.asarray(point, dtype=float))
        # 校正の単位(0.5秒角/step)から手動駆動の単位(1秒角/click)へ変換。
        if self.manual_counts is not None and self.cal_counts is not None:
            ratio = np.array([self.cal_counts[axis] / self.manual_counts[axis]
                              for axis in ('RA', 'DEC')])
            rounded = np.rint(correction * ratio).astype(int)
        else:
            rounded = np.rint(correction * self.cal_arcsec_per_step / manual_arcsec_per_click).astype(int)
        return np.clip(rounded, -limit, limit).astype(int)


def stable_center(camera, *, frames=10, settle=0.65, max_spread=5.0, with_radius=False):
    """複数実フレームから中心を収集。検出不足・広がりすぎを拒否。"""
    time.sleep(settle)
    # 移動前のフレーム平均・中心座標履歴を破棄し、移動後の新しい観測だけ使う。
    # 前後の移動測定に旧フレームが混じると、±8秒角の微小変位が薄まる。
    if hasattr(camera, 'frames'):
        camera.frames.clear()
    if hasattr(camera, 'centers'):
        camera.centers.clear()
    if hasattr(camera, 'circle'):
        camera.circle = None
    if hasattr(camera, 'prev'):
        camera.prev = None
    coords = []
    radii = []
    for _ in range(frames + max(0, getattr(getattr(camera, 'args', None), 'frame_average', 1) - 1)):
        camera.update()
        if camera.circle is not None and getattr(camera, 'gray', None) is not None:
            coords.append(camera.circle[:2])
            radii.append(float(camera.circle[2]))
    if len(coords) < max(6, int(frames * .75)):
        raise CalibrationError(f'円盤中心の検出不足: {len(coords)}/{frames}')
    points = np.asarray(coords, dtype=float)
    spread = np.max(np.linalg.norm(points - np.median(points, axis=0), axis=1))
    if spread > max_spread:
        raise CalibrationError(f'中心検出が不安定: 最大偏差={spread:.1f}px')
    center = np.median(points, axis=0)
    if with_radius:
        radius = float(np.median(radii))
        if not 20.0 <= radius <= 2000.0:
            raise CalibrationError(f"太陽半径が不正: {radius:.2f}px")
        return center, radius
    return center



def _axis_metrics(vectors, pixel_scale, pulse_s):
    matrix = np.column_stack((vectors['RA'], vectors['DEC']))
    lengths = np.linalg.norm(matrix, axis=0)
    # atan2(外積の大きさ, 内積)で、正方向どうしの0～180°を求める。
    ra, dec = matrix.T
    area = abs(ra[0] * dec[1] - ra[1] * dec[0])
    angle = float(np.degrees(np.arctan2(area, np.dot(ra, dec)))) if min(lengths) > 0 else float('nan')
    return dict(angle=angle, condition=float(np.linalg.cond(matrix)),
                directions=dict(zip(('RA', 'DEC'), np.degrees(np.arctan2(matrix[1], matrix[0])))),
                rates=dict(zip(('RA', 'DEC'), lengths * pixel_scale / pulse_s)))


def _axis_regression(points):
    """正方向の観測をパルス番号に対してX・Y独立に最小二乗回帰する。"""
    xy = np.asarray(points)
    steps = np.arange(len(xy), dtype=float)

    def fit(indices):
        # 切片を自由にして、原点の測定誤差や初期遊びが全体を引っ張る影響を評価する。
        design = np.column_stack((steps[indices], np.ones(len(indices))))
        slope, intercept = np.linalg.lstsq(design, xy[indices], rcond=None)[0]
        residual = xy[indices] - (design @ np.stack((slope, intercept)))
        return slope, float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))

    indices = np.arange(len(xy))
    all_fit = fit(indices)
    # 外れ値候補は全点の最小二乗残差ではなく、ペア傾きの中央値を基準に検出する。
    # 外れ値自身が基準線を傾ける影響を抑え、中央値絶対偏差(MAD)で散らばりを測る。
    i, j = np.triu_indices(len(xy), k=1)
    robust_slope = np.median((xy[j] - xy[i]) / (steps[j] - steps[i])[:, None], axis=0)
    detrended = xy - steps[:, None] * robust_slope
    distances = np.linalg.norm(detrended - np.median(detrended, axis=0), axis=1)
    median = np.median(distances)
    limit = median + max(0.5, 3 * 1.4826 * np.median(np.abs(distances - median)))
    inliers = indices[distances <= limit]
    return dict(all=all_fit, drop_initial=fit(indices[4:]) if len(indices) >= 6 else None,
                drop_outliers=fit(inliers) if len(inliers) >= 2 else None,
                outliers=indices[distances > limit].tolist(), outlier_threshold_px=float(limit))


def run_calibration(mount, camera, *, settle=0.2, report=print, drift_enabled=True,
                    solar_radius_arcsec=960.0, progress=None):
    """追尾中の太陽像を使い、PHD2型の順序で校正する。

    RAドリフト測定 → RA正・逆 → DEC遊び取り → DECドリフト測定 → DEC正・逆。
    逆方向のパルス数は各軸の正方向と同数とし、残差は診断値に使う。
    progressには表示用の軸・方向・回数・画像変位・目標角を渡す。
    """
    pulse_ms = 1000
    guide_fraction = 0.95
    if mount.cal_pulse_ms != pulse_ms or mount.guide_fraction != guide_fraction:
        raise CalibrationError('CALには1000ms、ガイド速度0.95倍の設定が必要です')
    target_arcsec = 180.0
    max_forward_pulses = 60
    max_backlash_pulses = 24
    positions = {}
    pulse_counts = {}
    pulse_log = []
    axis_vectors = {}
    raw_axis_vectors = {}
    drift_by_axis = {}
    regression = {}
    return_errors = {}
    attempted = 0
    # 時刻差・補正量は単調時計で計算し、ログ照合用にCAL開始時のUTCへ対応づける。
    cal_started = time.monotonic()
    cal_utc = datetime.now(timezone.utc)

    def measure():
        # 安定化の待ち時間を除いたフレーム取得区間の中点を観測時刻とする。
        time.sleep(settle)
        started = time.monotonic()
        center, radius = stable_center(camera, settle=0, with_radius=True)
        return (started + time.monotonic()) / 2, center, radius

    def check_radius(radius):
        if abs(radius - initial_radius) > max(5.0, initial_radius * 0.08):
            raise CalibrationError(f'太陽半径が急変: 初期{initial_radius:.1f}px 現在{radius:.1f}px')

    def axis_origin(axis, first_observation):
        # 補正ON時は各軸の正式測定直前に約30秒観測し、軸ごとに独立した一定速度を推定する。
        # 正逆1往復の範囲だけ同じ速度を使い、DEC遊び取りにはRAの推定値を流用しない。
        observations = [first_observation]
        if drift_enabled:
            report(f'CAL {axis} 自然ドリフト観測中（約30秒、補正パルスなし）')
            start = observations[0][0]
            while observations[-1][0] - start < 30.0:
                time.sleep(2.0)
                observation = measure()
                check_radius(observation[2])
                observations.append(observation)
            slopes = [(later - earlier) / (t_later - t_earlier)
                      for j, (t_later, later, _) in enumerate(observations)
                      for t_earlier, earlier, _ in observations[:j]]
            drift = np.median(slopes, axis=0)
        else:
            drift = np.zeros(2)
        epoch, center, _ = observations[-1]
        drift_by_axis[axis] = drift
        report(f'CAL {axis} 自然ドリフト {"ON" if drift_enabled else "OFF"}: '
               f'{drift.tolist()} px/s（観測 {epoch - observations[0][0]:.1f}秒）')
        return epoch, center, drift

    def pulse(stage, axis, direction, index, origin):
        nonlocal attempted
        attempted += 1
        response = mount.move_cal_pulse(axis, direction)
        # 両軸の生の24bitカウンターを保存し、差分は折り返しを考慮する。
        # RA差分には通常追尾も含まれる。光学変位の判定は画像のみで行う。
        try:
            before = {key: response['before'][key] for key in ('RA', 'DEC')}
            after = {key: response['after'][key] for key in ('RA', 'DEC')}
            tracking = response['ra_tracking_counts']
            guide = response['ra_guide_counts']
            elapsed = response['ra_counter_elapsed_s']
        except (KeyError, TypeError) as exc:
            raise CalibrationError('パルス前後の位置カウンターを取得できません') from exc
        if any(type(value) is not int or not 0 <= value <= 0xFFFFFF
               for counters in (before, after) for value in counters.values()):
            raise CalibrationError('位置カウンターが24bit整数ではありません')
        delta = {key: counter_delta(before[key], after[key]) for key in ('RA', 'DEC')}
        entry = dict(stage=stage, axis=axis, direction=direction, index=index,
                     before=before, after=after, delta=delta, ra_counter_elapsed_s=elapsed,
                     ra_tracking_counts=tracking, ra_guide_counts=guide)
        pulse_log.append(entry)
        # 画像取得が失敗しても、先に採取したカウンターはログに残す。
        report(f'CAL {stage} {index}: 位置カウンター 前={before} 後={after} 差分={delta} / '
               f'RA通常追尾={tracking:+.2f} RAガイド={guide:+.2f} counts '
               f'（{elapsed:.3f}s、RAパルス時は推定）')
        try:
            timestamp, center, radius = measure()
        except CalibrationError as exc:
            report(f'CAL IMAGE {stage} {index}: 測定失敗 {exc}')
            raise
        epoch, raw_origin, drift = origin
        offset = timestamp - epoch
        raw_shift = center - raw_origin
        # 補正ベクトルは生の変位から差し引く量。逆方向も正方向と同じ原点から測る。
        drift_shift = drift * offset
        corrected_shift = raw_shift - drift_shift
        utc = (cal_utc + timedelta(seconds=timestamp - cal_started)).isoformat(timespec='milliseconds')
        entry.update(timestamp=timestamp, timestamp_utc=utc, elapsed_s=offset, center=center, radius=radius,
                     raw_delta_px=raw_shift, drift_delta_px=drift_shift,
                     corrected_delta_px=corrected_shift, corrected_center=raw_origin + corrected_shift)
        image_record = dict(stage=stage, axis=axis, direction=direction, pulse=index,
                            utc=utc, monotonic_s=timestamp, elapsed_s=offset,
                            raw_xy_px=center.tolist(), radius_px=radius,
                            raw_delta_px=raw_shift.tolist(), drift_delta_px=drift_shift.tolist(),
                            corrected_delta_px=corrected_shift.tolist())
        report('CAL IMAGE ' + json.dumps(image_record, ensure_ascii=False))
        check_radius(radius)
        # 日本語ログとは独立した数値を表示へ渡す。正逆とも測定原点からの距離で、
        # 逆方向では復帰残差を示す。この通知はパルス数や校正値を決めない。
        if progress is not None:
            measured_arcsec = float(np.linalg.norm(corrected_shift)) * pixel_scale
            progress(axis, direction, index, measured_arcsec, target_arcsec)
        return entry

    def calibrate_axis(axis, origin):
        raw_points = [np.zeros(2)]
        corrected_points = [np.zeros(2)]
        for count in range(1, max_forward_pulses + 1):
            entry = pulse(f'{axis}+', axis, 1, count, origin)
            raw_points.append(entry['raw_delta_px'])
            corrected_points.append(entry['corrected_delta_px'])
            distance_arcsec = float(np.linalg.norm(corrected_points[-1])) * pixel_scale
            report(f'CAL {axis}+ {count}: 補正後変位 {distance_arcsec:.2f}/{target_arcsec:.0f}秒角')
            # 画像座標の減算で生じる浮動小数点の丸め誤差だけを許容する。
            if distance_arcsec >= target_arcsec - 1e-9:
                break
        else:
            raise CalibrationError(f'{axis} 正方向が{max_forward_pulses}パルスで3分角に達しません')
        positions[f'{axis}+'] = entry['corrected_center']
        axis_vectors[axis] = corrected_points[-1] / count
        raw_axis_vectors[axis] = raw_points[-1] / count
        regression[axis] = {label: _axis_regression(points) for label, points in
                            (('raw', raw_points), ('corrected', corrected_points))}
        pulse_counts[axis] = count

        # 復帰残差には遊びや測定誤差が含まれる。追加パルスによる追い込みは行わない。
        for index in range(1, count + 1):
            entry = pulse(f'{axis}-', axis, -1, index, origin)
        positions[f'{axis}0'] = entry['corrected_center']
        residual_px = float(np.linalg.norm(entry['corrected_delta_px']))
        return_errors[axis] = residual_px
        report(f'CAL {axis} 復帰: {count}パルス、残差 {residual_px:.2f}px'
               f' ({residual_px * pixel_scale:.2f}秒角)')
        return entry

    try:
        first = measure()
        initial_radius = first[2]
        pixel_scale = solar_radius_arcsec / initial_radius
        origin = axis_origin('RA', first)
        positions['START'] = origin[1]
        ra_end = calibrate_axis('RA', origin)

        # DECを正方向へ動かし、遊びを取る。この段階は隣接する生の画像間の変位を使い、
        # RA校正前に測ったドリフトを長時間外挿しない。
        # 公称パルス変位の1/4以上かつ最低0.25pxの移動を3回連続で確認する。
        # 初回の有効移動と45°以内に揃った移動だけを同じ方向の連続動作とみなす。
        nominal_arcsec = 360 * 3600 / 86164.0905 * guide_fraction * pulse_ms / 1000
        min_motion_px = max(0.25, nominal_arcsec / pixel_scale / 4)
        consecutive = 0
        first_unit = None
        previous = ra_end['center']
        takeup_origin = (ra_end['timestamp'], previous, np.zeros(2))
        for index in range(1, max_backlash_pulses + 1):
            entry = pulse('DEC 遊び取り', 'DEC', 1, index, takeup_origin)
            shift = entry['center'] - previous
            previous = entry['center']
            distance = float(np.linalg.norm(shift))
            if distance < min_motion_px:
                consecutive = 0
                first_unit = None
            else:
                unit = shift / distance
                if first_unit is None or np.dot(first_unit, unit) < np.sqrt(0.5):
                    consecutive = 0
                    first_unit = unit
                consecutive += 1
            report(f'CAL DEC 遊び取り {index}: 変位 {distance:.2f}px、連続 {consecutive}/3')
            if consecutive == 3:
                break
        else:
            raise CalibrationError(f'DEC 有効移動を{max_backlash_pulses}パルス以内に3回連続で確認できません')
        takeup_count = index
        # 遊び取り後の観測を起点にDEC固有のドリフト窓を設け、窓の最後を正式原点とする。
        origin = axis_origin('DEC', (entry['timestamp'], entry['center'], entry['radius']))
        positions['DEC_START'] = origin[1]
        report(f'CAL DEC 測定原点を再設定（遊び取り {takeup_count}パルス）')
        reference = calibrate_axis('DEC', origin)['center']

        pulse_s = pulse_ms / 1000

        def compare(label, vectors):
            metrics = _axis_metrics(vectors, pixel_scale, pulse_s)
            report(f'CAL {label}: ' + ' / '.join(
                f'{axis}方位 {metrics["directions"][axis]:.3f}° '
                f'速度 {metrics["rates"][axis]:.3f}秒角/s' for axis in ('RA', 'DEC'))
                + f' / 軸間角度 {metrics["angle"]:.3f}° / 条件数 {metrics["condition"]:.3f}')
            return metrics

        # 区間の実測端点から得た2本をそのまま校正行列に使う。回帰・除外結果は診断用。
        raw_metrics = compare('端点 補正前', raw_axis_vectors)
        metrics = compare('端点 補正後', axis_vectors)
        for key, label in (('raw', '補正前'), ('corrected', '補正後')):
            compare(f'全点回帰 {label}', {axis: regression[axis][key]['all'][0]
                                        for axis in ('RA', 'DEC')})
            for axis in ('RA', 'DEC'):
                fit = regression[axis][key]
                slope, rms = fit['all']
                report(f'CAL {axis} 回帰 {label}: RMS={rms:.3f}px '
                       f'外れ値候補={fit["outliers"]}（0=原点、閾値={fit["outlier_threshold_px"]:.3f}px）')
                for subset, name in (('drop_initial', '原点と最初の3パルスを除外'),
                                     ('drop_outliers', '外れ値候補を除外')):
                    if fit[subset] is None:
                        report(f'CAL {axis} 回帰 {label} {name}: 点数不足')
                        continue
                    filtered, filtered_rms = fit[subset]
                    heading = np.degrees(np.arctan2(filtered[1], filtered[0]))
                    change = (heading - np.degrees(np.arctan2(slope[1], slope[0])) + 180) % 360 - 180
                    speed = np.linalg.norm(filtered) * pixel_scale / pulse_s
                    speed_change = speed - np.linalg.norm(slope) * pixel_scale / pulse_s
                    report(f'CAL {axis} 回帰 {label} {name}: 方位 {heading:.3f}° '
                           f'({change:+.3f}°)、速度 {speed:.3f}秒角/s ({speed_change:+.3f})、'
                           f'RMS={filtered_rms:.3f}px')

        condition = metrics['condition']
        angle = metrics['angle']
        # 直交性と各軸の感度差は独立に判定。境界値の丸め誤差だけを許容する。
        if not np.isfinite(condition) or condition > 4.0 + 1e-9:
            raise CalibrationError(f'CAL 行列条件数が上限4.0を超えました: cond={condition:.3f}')
        if not 67.0 - 1e-9 <= angle <= 113.0 + 1e-9:
            raise CalibrationError(f'CAL 軸間角度が67.0°～113.0°の範囲外です: {angle:.3f}°')

        # 自動ガイドの基準には、ドリフト補正前の最新の実画像座標を渡す。
        result = CameraAxisCalibration((axis_vectors['RA'], axis_vectors['DEC']), reference, positions, pulse_counts)
        result.steps_pulse_ms = pulse_ms
        result.pixel_scale_arcsec = pixel_scale
        result.cal_pulse_arcsec = {axis: rate * pulse_s for axis, rate in metrics['rates'].items()}
        result.drift_px_per_s = drift_by_axis
        result.raw_axis_metrics = raw_metrics
        result.axis_regression = regression
        result.pulse_log = pulse_log
        result.dec_takeup_pulses = takeup_count
        result.return_errors_px = return_errors
        result.axis_angles_deg = metrics['directions']
        result.axis_rates_arcsec_per_s = metrics['rates']
        result.axis_separation_deg = angle
        result.condition_number = condition
        return result
    except BaseException:
        if attempted:
            report(f'!!! CAL途中終了: パルス要求{attempted}回。自動復帰は実行しません')
        raise
