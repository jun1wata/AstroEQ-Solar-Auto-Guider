#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OLED/encoder UI prototype. NO AstroEQ serial communication, NO motor movement.

Development stand-ins: Tracking logical ON, latitude 35.6, longitude 139.5,
Windows PC UTC supplied at launch by --pc-utc; ongoing time uses monotonic clock.
Physical GPIO20 LED illumination is explicitly opt-in with --test-tracking-led.
"""
import argparse
from collections import deque
from datetime import datetime, timezone, timedelta
import time

from PIL import Image, ImageDraw, ImageFont
from ssd1315_oled_modes_right import SolarOLED

SCREEN_MANUAL = 'manual'
SCREEN_GUIDE = 'guide'
SCREEN_RAW = 'raw'
SCREEN_CENTER = 'center'
SCREEN_SETTINGS = 'settings'
SCREEN_CAL = 'calibration'


class UI:
    def __init__(self):
        self.screen = SCREEN_MANUAL
        self.manual_selection = 0  # 0=RA HI, 1=RA LOW, 2=DEC HI, 3=DEC LOW
        self.manual_moving = False
        self.manual_blink_visible = True
        self.axis = 0  # 互換用 0=RA, 1=DEC
        self.menu = 0  # guide choices
        self.settings_menu = 0
        self.camara = None
        self.circle = None
        self.history_ra = deque(maxlen=56)
        self.history_dec = deque(maxlen=56)
        self.guide_enabled = False  # NEVER turn on without calibration and motor control
        self.calibration_message = 'NOT READY'
        self.font = ImageFont.load_default()

    def rotate(self, steps):
        if not steps:
            return
        if self.screen == SCREEN_MANUAL:
            print('微動要求(模擬・送信なし):', 'RA' if self.axis == 0 else 'DEC', f'{steps:+d} click', flush=True)
        elif self.screen == SCREEN_GUIDE:
            self.menu = (self.menu + steps) % 3
            print('選択:', ('RAW', 'CENTER', 'GUIDE SET')[self.menu], flush=True)
        elif self.screen == SCREEN_SETTINGS:
            self.settings_menu = (self.settings_menu + steps) % 2
            print('選択:', ('CAL START', 'BACK')[self.settings_menu], flush=True)
        elif self.screen in (SCREEN_RAW, SCREEN_CENTER, SCREEN_CAL):
            # The future 128x128 parameter editing UI is intentionally omitted.
            pass

    def short(self):
        if self.screen == SCREEN_MANUAL:
            self.manual_selection = (self.manual_selection + 1) % 4
            self.axis = 0 if self.manual_selection < 2 else 1
            print('微動選択:', ('RA HI', 'RA LOW', 'DEC HI', 'DEC LOW')[self.manual_selection], flush=True)
        elif self.screen == SCREEN_GUIDE:
            self.screen = (SCREEN_RAW, SCREEN_CENTER, SCREEN_SETTINGS)[self.menu]
            print('画面:', self.screen, flush=True)
        elif self.screen in (SCREEN_RAW, SCREEN_CENTER):
            self.screen = SCREEN_GUIDE  # BACK is the sole selectable action on these screens
            print('画面: guide', flush=True)
        elif self.screen == SCREEN_SETTINGS:
            if self.settings_menu == 1:
                self.screen = SCREEN_GUIDE
            else:
                self.calibration_message = 'CAL START'
                self.screen = SCREEN_CAL
                print('CAL START が選択されました', flush=True)
        elif self.screen == SCREEN_CAL:
            self.screen = SCREEN_SETTINGS

    def long(self):
        if self.screen == SCREEN_MANUAL:
            self.screen = SCREEN_GUIDE
            print('画面: guide', flush=True)
        elif self.screen == SCREEN_GUIDE:
            self.screen = SCREEN_MANUAL
            print('画面: manual', flush=True)
        # In child screens long press is intentionally not mapped to motor actions.

    def render(self, utc_now):
        im = Image.new('1', (128, 64), 0)
        d = ImageDraw.Draw(im)
        if self.screen == SCREEN_MANUAL:
            # 微動指令を赤道儀が処理中の場合は、タイトルだけを点滅するMOVING...へ切り替える。
            # 下の4ボタンは消さず、選択状態も保持する。
            if self.manual_moving:
                if self.manual_blink_visible:
                    d.text((1, 0), 'MOVING...', font=self.font, fill=1)
            else:
                d.text((1, 0), 'MOUNT MANUAL', font=self.font, fill=1)
            # 4つの微動選択。表示は横2列×縦2行、切替順は縦方向優先。
            for selection, x0, y0, label in (
                (0, 1, 16, 'RA HI'), (2, 65, 16, 'DEC HI'),
                (1, 1, 38, 'RA LOW'), (3, 65, 38, 'DEC LOW')):
                active = self.manual_selection == selection
                d.rectangle((x0, y0, x0 + 61, y0 + 20), outline=1, fill=1 if active else 0)
                d.text((x0 + 5, y0 + 5), label, font=self.font, fill=0 if active else 1)
        elif self.screen == SCREEN_GUIDE:
            d.text((0, 0), 'AUTO GUIDE: OFF', font=self.font, fill=1)
            # No sampled RA/DEC error yet, so do not fabricate graph curves.
            d.rectangle((1, 12, 126, 41), outline=1)
            d.text((10, 20), 'RA/DEC: NO DATA', font=self.font, fill=1)
            options = ('RAW', 'CENTER', 'SET')
            for i, (x0, x1) in enumerate(((0, 34), (36, 84), (86, 127))):
                selected = i == self.menu
                d.rectangle((x0, 49, x1, 63), outline=1, fill=1 if selected else 0)
                d.text((x0 + 2, 51), options[i], font=self.font, fill=0 if selected else 1)
        elif self.screen == SCREEN_SETTINGS:
            d.text((1, 0), 'CALIBRATION', font=self.font, fill=1)
            d.text((1, 13), 'GUIDE OFF', font=self.font, fill=1)
            for i, name in enumerate(('CAL START', 'BACK')):
                y = 28 + 17*i
                chosen = self.settings_menu == i
                d.rectangle((1, y, 105, y+14), fill=1 if chosen else 0, outline=1)
                d.text((4, y+2), name, font=self.font, fill=0 if chosen else 1)
        elif self.screen == SCREEN_CAL:
            d.text((1, 0), 'CALIBRATION', font=self.font, fill=1)
            d.text((1, 20), 'NOT IMPLEMENTED', font=self.font, fill=1)
            d.text((1, 49), 'PRESS: BACK', font=self.font, fill=1)
        elif self.screen in (SCREEN_RAW, SCREEN_CENTER):
            d.text((1, 0), 'RAW' if self.screen == SCREEN_RAW else 'CENTER', font=self.font, fill=1)
            d.text((1, 20), 'CAMERA PREVIEW', font=self.font, fill=1)
            d.text((1, 30), 'NEXT STAGE', font=self.font, fill=1)
            d.rectangle((0, 48, 61, 63), fill=1)
            d.text((4, 50), 'BACK', font=self.font, fill=0)
        return im


def main():
    p = argparse.ArgumentParser(description='AstroEQ OLED UI mock. NO MOTOR DRIVE.')
    p.add_argument('--pc-utc', help='Windows PC UTC at launch, e.g. 2026-11-17T08:00:00Z; omit to use Pi clock')
    p.add_argument('--lat', type=float, default=35.6)
    p.add_argument('--lon', type=float, default=139.5)
    p.add_argument('--test-tracking-led', action='store_true', help='TEST ONLY: turn GPIO20 physical LED on while running')
    p.add_argument('--no-encoder', action='store_true', help='keyboard terminal mock mode (not normally needed)')
    p.add_argument('--seconds', type=float, default=0, help='optional timed exit, 0 runs until Ctrl+C')
    args = p.parse_args()
    if not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
        p.error('Invalid latitude/longitude')
    if args.pc_utc:
        try:
            base = datetime.fromisoformat(args.pc_utc.replace('Z', '+00:00'))
            if base.tzinfo is None:
                raise ValueError('timezone required')
            base = base.astimezone(timezone.utc)
        except ValueError:
            p.error('--pc-utc requires ISO8601 UTC with Z, e.g. 2026-11-17T08:00:00Z')
        src = 'PC time at launch'
    else:
        base = datetime.now(timezone.utc)
        src = 'Pi system time (NOT PC time)'
    clock_started = time.monotonic()
    print('開発モード: GPS無効 / 緯度=%.4f / 経度=%.4f' % (args.lat, args.lon), flush=True)
    print(f'時刻ソース: {src}; UTC={base.isoformat()}', flush=True)
    print('Tracking LED状態: 模擬ON / 追尾・モーター駆動は実行しません', flush=True)
    from ssd1315_encoder import UIEncoder
    ui = UI()
    oled = None
    enc = None
    led = None
    gpio = None
    try:
        if args.test_tracking_led:
            import lgpio
            gpio = lgpio
            led = gpio.gpiochip_open(0)
            gpio.gpio_claim_output(led, 20, 1)
            print('GPIO20 LED: 開発試験で点灯中 (実追尾状態を示しません)', flush=True)
        oled = SolarOLED(interval=0.15)
        if not args.no_encoder:
            enc = UIEncoder()
        deadline = clock_started + args.seconds if args.seconds > 0 else None
        last_screen = None
        while deadline is None or time.monotonic() < deadline:
            if enc:
                steps, short_clicks, long_clicks = enc.consume()
                # Process the button before rotation to minimize ambiguous operations.
                for _ in range(long_clicks): ui.long()
                for _ in range(short_clicks): ui.short()
                ui.rotate(steps)
            now = base + timedelta(seconds=time.monotonic() - clock_started)
            if last_screen != ui.screen:
                print('表示:', ui.screen, 'UTC:', now.isoformat(timespec='seconds'), flush=True)
                last_screen = ui.screen
            oled._send_canvas(ui.render(now))
            time.sleep(0.18)
    except KeyboardInterrupt:
        print('ユーザー操作で終了', flush=True)
    finally:
        if enc: enc.close()
        if oled: oled.close()
        if led is not None:
            try: gpio.gpio_write(led, 20, 0)
            finally: gpio.gpiochip_close(led)
        print('開発モード終了。AstroEQへコマンド送信なし。', flush=True)


if __name__ == '__main__':
    main()
