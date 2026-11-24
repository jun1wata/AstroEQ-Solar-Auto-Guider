#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Raspberry Pi Zero 2 W: SSD1315 OLED 中心検出表示／カメラ生画像表示モード。

元のsol_track_win.pyと同じ基本処理：フレーム平均→GaussianBlur→Canny→輪郭→
円形度→最小包含円→中心の移動平均。LinuxではV4L2を使い、GUI/仮想カメラは不要。

重要：このプログラムは画像取得・検出のみ。AstroEQに接続せず、モーターは動かさない。
例: python3 solar_center_v4l2.py --device /dev/video0 --frames 60
    python3 solar_center_v4l2.py --image test_sun.png
"""
import argparse
import csv
import math
from itertools import count
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np


def detect_circle(gray, threshold1, threshold2, prev_circle=None, return_method=False):
    """太陽外周候補から1つだけ選択し、(中心x,中心y,半径)を小数画素で返す。

    return_method=True のときのみ (検出円またはNone, "CANNY"/"OTSU"/"NONE")。
    デフォルトの戻り値は旧仕様のまま維持する。

    以前のWindows版と同じCanny/輪郭円形度を使うが、最後の候補を無条件に
    選ばず、円盤として大きく、前回位置に近い円を優先して誤検出を抑える。
    """
    height, width = gray.shape
    ksize = max(5, min(width, height) // 80) | 1
    blurred = cv2.GaussianBlur(gray, (ksize, ksize), 2)
    edges = cv2.Canny(blurred, threshold1, threshold2)
    contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    min_area = width * height * 0.0002  # sol_track_win.py の閾値
    min_radius = min(width, height) * 0.07  # 小さな円形ノイズを排除する追加条件
    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        perimeter = cv2.arcLength(cnt, True)
        if perimeter <= 0:
            continue
        circularity = 4.0 * math.pi * area / (perimeter * perimeter)
        if not (0.5 < circularity <= 1.3):
            continue
        (x, y), radius = cv2.minEnclosingCircle(cnt)
        if radius < min_radius or radius > min(width, height) * 0.9:
            continue
        if not (-radius * 0.1 <= x <= width + radius * 0.1 and
                -radius * 0.1 <= y <= height + radius * 0.1):
            continue
        # 外周に複数の近接輪郭があれば、直前の円への近さも加味する。
        score = area
        if prev_circle is not None:
            px, py, pr = prev_circle
            dist = math.hypot(x - px, y - py)
            radius_diff = abs(radius - pr)
            score -= 2.0 * (dist + radius_diff) * min(width, height)
        candidates.append((score, (float(x), float(y), float(radius))))
    if candidates:
        circle = max(candidates, key=lambda item: item[0])[1]
        return (circle, "CANNY") if return_method else circle

    # Cannyで外周が途切れる場合の代替処理。
    # 太陽円盤が明るく、周囲が暗い画像に対してOtsuの輝度二値化を使う。
    # 白い背景や円盤のない画像を誤検出しないよう、面積・円形度・充填率を検証。
    _, binary = cv2.threshold(blurred, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
                              np.ones((5, 5), dtype=np.uint8))
    blobs, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                cv2.CHAIN_APPROX_SIMPLE)
    fallback = []
    for cnt in blobs:
        area = cv2.contourArea(cnt)
        if area < width * height * 0.01:
            continue
        perimeter = cv2.arcLength(cnt, True)
        if perimeter <= 0:
            continue
        circularity = 4.0 * math.pi * area / (perimeter * perimeter)
        (x, y), radius = cv2.minEnclosingCircle(cnt)
        if not (0.65 <= circularity <= 1.15):
            continue
        if not (min_radius <= radius <= min(width, height) * 0.9):
            continue
        fill_ratio = area / (math.pi * radius * radius)
        if not (0.65 <= fill_ratio <= 1.10):
            continue
        if not (0 <= x < width and 0 <= y < height):
            continue
        score = area
        if prev_circle is not None:
            px, py, pr = prev_circle
            score -= 2.0 * (math.hypot(x - px, y - py) + abs(radius - pr)) * min(width, height)
        fallback.append((score, (float(x), float(y), float(radius))))
    circle = max(fallback, key=lambda item: item[0])[1] if fallback else None
    if return_method:
        return (circle, "OTSU" if circle is not None else "NONE")
    return circle


def save_overlay(gray, circle, path, status):
    """画像へ緑の外周と赤の中心印を描画して保存する。"""
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = gray.shape
    if circle is not None:
        x, y, r = circle
        cv2.circle(img, (round(x), round(y)), round(r), (0, 255, 0), 2)
        cv2.drawMarker(img, (round(x), round(y)), (0, 0, 255),
                       markerType=cv2.MARKER_CROSS, markerSize=16, thickness=2)
        cv2.putText(img, f'center=({x:.2f},{y:.2f}) r={r:.1f}', (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    else:
        cv2.putText(img, 'NO SOLAR DISK', (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
    cv2.putText(img, status, (10, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    if not cv2.imwrite(str(path), img):
        raise RuntimeError(f'画像を保存できません: {path}')


def parse_args():
    p = argparse.ArgumentParser(description='太陽中心検出のみ。AstroEQ/モーター制御なし')
    source = p.add_mutually_exclusive_group()
    source.add_argument('--device', default='/dev/video0', help='V4L2カメラデバイス')
    source.add_argument('--image', help='既存の静止画像で検出を検証（USBカメラ不要）')
    p.add_argument('--width', type=int, default=800)
    p.add_argument('--height', type=int, default=600)
    p.add_argument('--fps', type=float, default=3.0)
    p.add_argument('--frames', type=int, default=60, help='取り込むフレーム数（0で連続）')
    p.add_argument('--frame-average', type=int, default=5, help='画像平均枚数 1～20')
    p.add_argument('--center-average', type=int, default=5, help='中心座標平均枚数 1～20')
    p.add_argument('--canny1', type=int, default=50)
    p.add_argument('--canny2', type=int, default=150)
    p.add_argument('--output', default='solar_center_preview.png')
    p.add_argument('--csv', default='solar_center_log.csv')
    p.add_argument('--no-oled', action='store_true', help='OLED表示を無効にする')
    p.add_argument('--oled-mode', choices=['center', 'raw'], default='center',
                   help='center=円盤中心検出表示 / raw=カメラ生画像表示（注記・検出線なし）')
    p.add_argument('--oled-interval', type=float, default=0.5,
                   help='OLED更新の最短間隔（秒、初期値0.5）')
    p.add_argument('--fourcc', choices=['MJPG', 'YUYV'], default=None,
                   help='デバイス対応形式をv4l2-ctlで確認してから指定')
    return p.parse_args()


def main():
    a = parse_args()
    if not 1 <= a.frame_average <= 20 or not 1 <= a.center_average <= 20:
        raise SystemExit('平均枚数は1～20で指定してください')
    if not (0 <= a.canny1 <= 255 and 0 <= a.canny2 <= 255):
        raise SystemExit('Canny閾値は0～255で指定してください')
    if a.frames < 0 or a.fps <= 0 or a.width < 1 or a.height < 1 or a.oled_interval <= 0:
        raise SystemExit('frames/fps/サイズの指定が不正です')
    oled = None
    if not a.no_oled:
        from ssd1315_oled_modes_right import SolarOLED
        oled = SolarOLED(interval=a.oled_interval)
        print(f'SSD1315 OLED: I2C-1 / 0x3C / mode={a.oled_mode}', flush=True)
    cap = None
    if a.image:
        img = cv2.imread(a.image, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise SystemExit(f'画像が読めません: {a.image}')
        inputs = [img]
        print(f'画像モード: {a.image} / {img.shape[1]}x{img.shape[0]}', flush=True)
    else:
        cap = cv2.VideoCapture(a.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise SystemExit(f'V4L2カメラを開けません: {a.device}。v4l2-ctl --list-devices で確認してください')
        try:
            if a.fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*a.fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
            cap.set(cv2.CAP_PROP_FPS, a.fps)
            print(f'カメラ: {a.device} / 要求={a.width}x{a.height}@{a.fps:.1f}fps', flush=True)
            print(f'取得設定={cap.get(cv2.CAP_PROP_FRAME_WIDTH):.0f}x'
                  f'{cap.get(cv2.CAP_PROP_FRAME_HEIGHT):.0f}@'
                  f'{cap.get(cv2.CAP_PROP_FPS):.1f}fps', flush=True)
        except Exception:
            cap.release()
            raise
        inputs = None

    frames = deque(maxlen=a.frame_average)
    centers = deque(maxlen=a.center_average)
    prev_circle = None
    latest_gray = None
    latest_circle = None
    success = 0
    start = time.monotonic()
    try:
        with open(a.csv, 'w', newline='', encoding='utf-8') as fp:
            writer = csv.writer(fp)
            writer.writerow(['frame', 'time_s', 'detected', 'x_px', 'y_px', 'radius_px', 'dx_px', 'dy_px'])
            total = 1 if a.image else a.frames
            for idx in (count() if not a.image and a.frames == 0 else range(total)):
                if a.image:
                    gray = inputs[0]
                else:
                    ok, img = cap.read()
                    if not ok:
                        print(f'ERROR: frame {idx+1} を取得できません', flush=True)
                        break
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
                # RGBを平均する必要はなく、グレースケールを平均して省メモリ化。
                frames.append(gray.copy())
                if len(frames) < a.frame_average and not a.image:
                    continue
                avg = np.mean(np.stack(tuple(frames)), axis=0).astype(np.uint8)
                detected = detect_circle(avg, a.canny1, a.canny2, prev_circle)
                elapsed = time.monotonic() - start
                if detected is None:
                    centers.clear()  # 見失った場合、過去の中心位置を使わない
                    latest_circle = None
                    writer.writerow([idx+1, f'{elapsed:.3f}', 0, '', '', '', '', ''])
                    print(f'{idx+1:04d}: 太陽外周を検出できません', flush=True)
                else:
                    prev_circle = detected
                    centers.append(detected)
                    x = sum(c[0] for c in centers) / len(centers)
                    y = sum(c[1] for c in centers) / len(centers)
                    r = detected[2]
                    latest_circle = (x, y, r)
                    dx, dy = x - avg.shape[1]/2, y - avg.shape[0]/2
                    writer.writerow([idx+1, f'{elapsed:.3f}', 1, f'{x:.3f}', f'{y:.3f}',
                                     f'{r:.3f}', f'{dx:.3f}', f'{dy:.3f}'])
                    success += 1
                    print(f'{idx+1:04d}: x={x:7.2f} y={y:7.2f} r={r:6.2f} '
                          f'dx={dx:+7.2f} dy={dy:+7.2f}', flush=True)
                latest_gray = avg
                if oled is not None:
                    if a.oled_mode == 'raw':
                        oled.show_raw(gray, latest_circle)  # 生画像と検出数値（外周線なし）
                    else:
                        oled.show(latest_circle, avg.shape[1], avg.shape[0])
    except KeyboardInterrupt:
        print('ユーザー操作で終了', flush=True)
    finally:
        if cap is not None:
            cap.release()
        if oled is not None:
            oled.close()
    if latest_gray is not None:
        save_overlay(latest_gray, latest_circle, a.output,
                     f'detected={success}, elapsed={time.monotonic()-start:.1f}s')
        print(f'プレビュー保存: {a.output}', flush=True)
    print(f'CSV保存: {a.csv} / 検出成功フレーム={success}', flush=True)
    print('AstroEQには接続していません。モーター駆動なし。', flush=True)


if __name__ == '__main__':
    main()
