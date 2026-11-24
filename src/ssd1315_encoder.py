#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SSD1315 UI input: kernel rotary-encoder/evdev + gpiozero Button.

Hardware: EC12 A=BCM5 B=BCM6, each 10nF to GND; common to GND.
Kernel overlay: dtoverlay=rotary-encoder,pin_a=5,pin_b=6,relative_axis=1,steps-per-period=2
The observed EC12 has ~two EV_REL/REL_X events per physical click.

UIEncoder.consume() stays (one_step, short_count, long_count).
Camera, OLED, and mount software are unchanged. No motor commands.
"""
from collections import deque
import select
import threading
import time

from evdev import InputDevice, ecodes, list_devices
from gpiozero import Button
from gpiozero.pins.lgpio import LGPIOFactory


class UIEncoder:
    def __init__(self, a=5, b=6, sw=13, poll=0.003, long_s=0.8,
                 device_name='rotary@5', events_per_click=2):
        # 'a', 'b', and 'poll' kept only for existing caller compatibility.
        # Kernel driver owns GPIO5/6: do NOT request those pins via gpiozero.
        if events_per_click < 1:
            raise ValueError('events_per_click must be >= 1')
        self.lock = threading.Lock()
        self.rotations = deque(maxlen=256)
        self.shorts = 0
        self.longs = 0
        self.press_at = None
        self.long_s = long_s
        self.events_per_click = events_per_click
        self.accumulator = 0
        self.stopping = threading.Event()
        self.reader = None
        self.device = None
        self.factory = None
        self.button = None
        try:
            matches = []
            for path in list_devices():
                d = InputDevice(path)
                if d.name == device_name:
                    matches.append(d)
                else:
                    d.close()
            if len(matches) != 1:
                for d in matches:
                    d.close()
                raise RuntimeError(
                    f'入力デバイス {device_name!r} が{len(matches)}個見つかりました。'
                    ' /dev/input/event* の権限と dtoverlay 設定を確認してください。')
            self.device = matches[0]
            self.factory = LGPIOFactory()
            self.button = Button(sw, pull_up=True, bounce_time=0.035,
                                 pin_factory=self.factory)
            self.button.when_pressed = self._pressed
            self.button.when_released = self._released
            self.reader = threading.Thread(target=self._read_events,
                                           name='rotary-evdev', daemon=True)
            self.reader.start()
            print(f'rotary-encoder: {self.device.path} ({self.device.name}), '
                  f'{events_per_click} EV_REL = 1クリック', flush=True)
        except BaseException:
            self.close()
            raise

    def _record_event(self, value):
        """Signed half-period event accumulator; preserve full detents.

        +1,+1 => +1 click, -1,-1 => -1 click. A spurious opposite
        single transition cancels the incomplete half-step.
        """
        if value == 0:
            return
        with self.lock:
            self.accumulator += value
            while abs(self.accumulator) >= self.events_per_click:
                direction = 1 if self.accumulator > 0 else -1
                self.rotations.append(direction)
                self.accumulator -= direction * self.events_per_click

    def _read_events(self):
        while not self.stopping.is_set():
            try:
                readable, _, _ = select.select([self.device.fd], [], [], 0.1)
                if not readable:
                    continue
                for event in self.device.read():
                    if event.type == ecodes.EV_REL and event.code == ecodes.REL_X:
                        self._record_event(event.value)
            except (OSError, ValueError) as exc:
                if not self.stopping.is_set():
                    print(f'rotary-encoder 読み取りエラー: {exc}', flush=True)
                break

    def _pressed(self):
        with self.lock:
            self.press_at = time.monotonic()

    def _released(self):
        now = time.monotonic()
        with self.lock:
            if self.press_at is not None:
                if now - self.press_at >= self.long_s:
                    self.longs += 1
                else:
                    self.shorts += 1
            self.press_at = None

    def consume(self):
        """Return at most one rotation; queued steps stay for later UI frames."""
        with self.lock:
            step = self.rotations.popleft() if self.rotations else 0
            result = (step, self.shorts, self.longs)
            self.shorts = 0
            self.longs = 0
            return result

    def close(self):
        self.stopping.set()
        if self.reader is not None and self.reader.is_alive():
            self.reader.join(timeout=0.5)
        if self.button is not None:
            self.button.when_pressed = None
            self.button.when_released = None
            self.button.close()
            self.button = None
        if self.factory is not None:
            self.factory.close()
            self.factory = None
        if self.device is not None:
            self.device.close()
            self.device = None
