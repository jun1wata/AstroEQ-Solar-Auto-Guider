#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""太陽GoTo統合起動専用のSH1107 128x128表示。

GoTo/追尾・GPIOの制御は行わない。既存OLED UIが動き出す前だけ
タイトル画面とGoTo中画面を表示する。1台のOLEDを同時に開かない。
"""
from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)


def font_for(text, max_width, max_size, min_size=7):
    """文字を128pxからはみ出さず、行高に収まる最大フォントを選択。"""
    test_image = Image.new('1', (128, 128), 0)
    pen = ImageDraw.Draw(test_image)
    for size in range(max_size, min_size - 1, -1):
        for path in FONT_CANDIDATES:
            try:
                f = ImageFont.truetype(path, size)
            except OSError:
                continue
            bb = pen.textbbox((0, 0), text, font=f)
            if bb[2] - bb[0] <= max_width and bb[3] - bb[1] <= size + 2:
                return f
    return ImageFont.load_default()


def centered(pen, y, text, font, color=1):
    bb = pen.textbbox((0, 0), text, font=font)
    w = bb[2] - bb[0]
    pen.text(((128 - w) // 2 - bb[0], y - bb[1]), text, font=font, fill=color)


def render_title():
    """128x128: 4単語を各行最大文字で描画し、最終行にcopyright。"""
    image = Image.new('1', (128, 128), 0)
    draw = ImageDraw.Draw(image)
    words = ('AstroEQ', 'Solar', 'Auto', 'Guider')
    for word, y in zip(words, (0, 27, 54, 81)):
        font = font_for(word, 124, 25)
        centered(draw, y, word, font)
    draw.line((6, 110, 121, 110), fill=1)
    copyright_text = 'copyright: jun1wata 2026'
    copyright_font = font_for(copyright_text, 126, 11, 6)
    centered(draw, 116, copyright_text, copyright_font)
    return image


def render_goto():
    """SW1受付後から追尾開始まで表示するGOTO画面。"""
    image = Image.new('1', (128, 128), 0)
    draw = ImageDraw.Draw(image)
    centered(draw, 26, 'AstroEQ', font_for('AstroEQ', 126, 23))
    centered(draw, 56, 'GOTO', font_for('GOTO', 126, 38))
    centered(draw, 97, 'MOVING...', font_for('MOVING...', 126, 17))
    return image


def render_home():
    """SW2を受けた後、GPS再評価を経てReady LED点灯まで使用する128x128画面。"""
    image = Image.new('1', (128, 128), 0)
    draw = ImageDraw.Draw(image)
    centered(draw, 9, 'GOTO', font_for('GOTO', 124, 31))
    centered(draw, 46, 'HOME', font_for('HOME', 124, 31))
    centered(draw, 83, 'POSITION', font_for('POSITION', 124, 24))
    return image


def render_camera_error():
    """USBカメラ未検出時の警告。OLED 128x128に大きな英語で表示。"""
    image = Image.new('1', (128, 128), 0)
    draw = ImageDraw.Draw(image)
    lines = (
        ('USB CAMERA', 3, 17),
        ('OPEN ERROR', 25, 19),
        ('UNPLUG', 58, 21),
        ('AND REPLUG', 81, 18),
        ('USB CONNECTOR', 106, 15),
    )
    for msg, y, max_size in lines:
        centered(draw, y, msg, font_for(msg, 126, max_size))
    return image


STATUS_LINES = {
    'GPS_MISSING': ('GPS NOT FOUND', 'CHECK USB GPS'),
    'GPS_WAIT': ('WAITING FOR GPS',),
    'GPS_NO_FIX': ('NO GPS FIX',),
    'ASTRO_MISSING': ('ASTROEQ', 'NOT FOUND'),
    'ASTRO_NO_RESPONSE': ('ASTROEQ', 'NO RESPONSE'),
    'CAMERA_MISSING': ('CAMERA NOT FOUND',),
    'CAMERA_FRAME_ERROR': ('CAMERA OPEN ERROR', 'CHECK USB CAMERA'),
    'SUN_LOW': ('SUN BELOW', 'HORIZON'),
    'PLEASE_WAIT': ('PLEASE WAIT...',),
    'SLEWING': ('SLEWING', 'TO SUN...'),
    'START_CAMERA': ('STARTING', 'CAMERA...'),
    'READY': ('PRESS GOTO', 'TO START'),
}


def render_status(mode):
    """起動時だけ使うASCII表示。行を独立に計測し、画面中央へ配置する。"""
    image = Image.new('1', (128, 128), 0)
    draw = ImageDraw.Draw(image)
    lines = STATUS_LINES[mode]
    start_y = 55 if len(lines) == 1 else 40
    for index, line in enumerate(lines):
        centered(draw, start_y + 29 * index, line, font_for(line, 124, 21))
    return image


class StartupOLED:
    def __init__(self):
        from luma.core.interface.serial import i2c
        from luma.oled.device import sh1107
        self.device = sh1107(i2c(port=1, address=0x3C), width=128, height=128, rotate=0)
        self.mode = None

    def show(self, mode):
        if mode == self.mode:
            return
        self.device.display(render_title() if mode == 'TITLE' else
                            render_goto() if mode == 'GOTO' else
                            render_home() if mode == 'HOME' else
                            render_camera_error() if mode == 'CAMERA_ERROR' else render_status(mode))
        self.mode = mode

    def close(self, *, preserve=False):
        if self.device is not None:
            # 起動UIへ渡す間はI2Cを解放し、最後の案内だけをパネルに残す。
            self.device.persist = preserve
            self.device.cleanup()
            self.device = None


