#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SH1107 128x128 OLED: 従来UIを上半分、カメラ画像を下半分へ表示。

AstroEQ制御処理は含まない。
"""
import time
from luma.core.interface.serial import i2c
from luma.oled.device import sh1107
from PIL import Image, ImageDraw, ImageFont


class SolarOLED:
    def __init__(self, bus_number=1, address=0x3C, interval=0.5):
        # SH1107 128x128. 128x64 UI drawing is left unchanged.
        self.interval = interval
        self.last_update = -1e10
        self.font = ImageFont.load_default()
        self.serial = i2c(port=bus_number, address=address)
        self.device = sh1107(self.serial, width=128, height=128, rotate=0)

    def render_center(self, circle, width, height):
        """検出モードを128x64へ描画（I2C不要のテスト可能な描画関数）。

        元画像の縦横比を維持し、表示枠へ等倍率で変換する。
        800x600なら約85x64相当の視野に円を描くため、円が楕円にならない。
        ステータス・数値は円に重なってよい（ユーザー指定）。
        """
        canvas = Image.new('1', (128, 64), 0)
        draw = ImageDraw.Draw(canvas)
        # 元画像をOLEDの高さに収め、画像中心と検出円を同じ変換で描画。
        scale = min(128.0 / width, 64.0 / height)
        ox = 128.0 - width * scale
        oy = (64.0 - height * scale) / 2.0
        draw.rectangle((round(ox), round(oy),
                        round(ox + width * scale - 1),
                        round(oy + height * scale - 1)), outline=1)
        camera_x = ox + width * scale / 2
        camera_y = oy + height * scale / 2
        draw.line((round(camera_x - 3), round(camera_y),
                   round(camera_x + 3), round(camera_y)), fill=1)
        draw.line((round(camera_x), round(camera_y - 3),
                   round(camera_x), round(camera_y + 3)), fill=1)

        if circle is None:
            lines = ('NO DISK', 'X: --', 'Y: --', 'R: --')
        else:
            x, y, radius = circle
            cx, cy, rr = ox + x * scale, oy + y * scale, radius * scale
            draw.ellipse((round(cx - rr), round(cy - rr),
                          round(cx + rr), round(cy + rr)), outline=1)
            draw.line((round(cx - 3), round(cy),
                       round(cx + 3), round(cy)), fill=1)
            draw.line((round(cx), round(cy - 3),
                       round(cx), round(cy + 3)), fill=1)
            lines = ('DISK OK', f'X:{x-width/2:+.1f}',
                     f'Y:{y-height/2:+.1f}', f'R:{radius:.1f}')

        self._draw_status(canvas, lines)
        return canvas

    def _status_lines(self, circle, width, height):
        if circle is None:
            return ('NO DISK', 'X: --', 'Y: --', 'R: --')
        x, y, radius = circle
        return ('DISK OK', f'X:{x-width/2:+.1f}',
                f'Y:{y-height/2:+.1f}', f'R:{radius:.1f}')

    def _draw_status(self, canvas, lines):
        """両モードとも同一位置に4行を表示。重なりは許容する。"""
        draw = ImageDraw.Draw(canvas)
        for i, value in enumerate(lines):
            py = 1 + i * 15
            bbox = draw.textbbox((1, py), value, font=self.font)
            draw.rectangle((bbox[0], bbox[1], min(127, bbox[2] + 1),
                            min(63, bbox[3] + 1)), fill=0)
            draw.text((1, py), value, font=self.font, fill=1)

    def show(self, circle, width, height, force=False):
        now = time.monotonic()
        if not force and now - self.last_update < self.interval:
            return
        canvas = self.render_center(circle, width, height)
        self._send_canvas(canvas)
        self.last_update = now

    def _send_canvas(self, canvas, camera_frame=None, detection_method=None):
        """上半分に従来UI、下半分にカメラの最新RAW画像を合成する。

        camera_frameはCamera.rawの8bitグレースケールnumpy配列。
        128x64へ縦横比を保って縮小し中央に配置する。未取得時は黒。
        """
        if canvas.size != (128, 64):
            raise ValueError(f"Expected original 128x64 UI, got {canvas.size}")
        full = Image.new('1', (128, 128), 0)
        full.paste(canvas.convert('1'), (0, 0))
        if camera_frame is not None:
            # 元のカメラ画像の画素縦横比を維持して縮小。
            camera_image = Image.fromarray(camera_frame).convert('L')
            camera_image.thumbnail((128, 64), Image.Resampling.BILINEAR)
            # モノクロOLED向け誤差拡散二値化。
            camera_image = camera_image.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
            x = (128 - camera_image.width) // 2
            y = 64 + (64 - camera_image.height) // 2
            full.paste(camera_image, (x, y))
            # 下半分のRAW画像へ検出方式を重ねる（OLED全体のY=64..127）。
            # 上半分のCENTER表示に描くのではなく、128x128合成画像に直接描く。
            method = (detection_method or '').upper()
            if method in ('CANNY', 'OTSU', 'NONE'):
                draw = ImageDraw.Draw(full)
                label_x, label_y = 2, 66  # RAW画像の左上隅
                bbox = draw.textbbox((label_x, label_y), method, font=self.font)
                draw.rectangle((label_x - 1, label_y - 1,
                                min(127, bbox[2] + 2), min(127, bbox[3] + 1)), fill=0)
                draw.text((label_x, label_y), method, font=self.font, fill=1)
        self.device.display(full)

    def close(self):
        if getattr(self, 'device', None) is not None:
            self.device.cleanup()
            self.device = None

    def render_raw(self, gray, circle=None):
        """生フレームを等倍率縮小して右端へ配置し、検出数値を左に重ねる。"""
        height, width = gray.shape[:2]
        src = Image.fromarray(gray).convert('L')
        src.thumbnail((128, 64), Image.Resampling.BILINEAR)
        canvas = Image.new('L', (128, 64), 0)
        canvas.paste(src, (128 - src.width, (64 - src.height) // 2))
        # SH1107はモノクロのため表示用の二値化が必要。
        binary = canvas.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
        self._draw_status(binary, self._status_lines(circle, width, height))
        return binary

    def show_raw(self, gray, circle=None, force=False):
        now = time.monotonic()
        if not force and now - self.last_update < self.interval:
            return
        self._send_canvas(self.render_raw(gray, circle))
        self.last_update = now
