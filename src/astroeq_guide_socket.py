#!/usr/bin/env python3
"""Restricted local Unix socket control: the Sun GoTo process alone owns AstroEQ serial.

Only STATUS, bounded PULSE and bounded manual relative MOVE requests are accepted; never raw AstroEQ commands.
"""
import json
import math
import os
import socket
import time
from datetime import timedelta

SOCKET_PATH = '/tmp/astroeq_solar_guide.sock'
SIDEREAL_DAY = 86164.0905
ARCSEC_PER_REV = 360 * 3600
MANUAL_TIMEOUT = 12.0
MIN_GUIDE_PULSE_MS = 100
SERIAL_IO_TIMEOUT = 2.0
READ_ATTEMPTS = 3
READ_RETRY_DELAY = 0.2
# 各試行には書き込みと読み取りのタイムアウトがある。クライアントの待ち時間は
# 複数の読み取りを含む要求について、サーバー側の再試行より先に切れないようにする。
READ_TRANSACTION_BUDGET = READ_ATTEMPTS * (2 * SERIAL_IO_TIMEOUT + READ_RETRY_DELAY)
STATUS_TIMEOUT = 2 * READ_TRANSACTION_BUDGET + 2


class MountSafetyFault(RuntimeError):
    """シリアル所有者の安全停止が必要な異常。"""


class ReadRetriesExhausted(MountSafetyFault):
    pass


class MotorCommandUncertain(MountSafetyFault):
    def __init__(self, command, reason):
        self.command = command
        super().__init__(f'{command}: 実行結果不明: {reason}')


def counter_delta(before, after):
    """24bitの前後値を符号付き最短差へ変換する（半周未満の移動を想定）。"""
    return ((after - before + 0x800000) % 0x1000000) - 0x800000


def record_calibration_pulse(ser, read24, normal_period, axis, move):
    """両軸の実測差と、RAの通常追尾・ガイド成分を区別して返す。"""
    if axis == 'RA':
        timer = read24(ser, 'b1')
        if timer <= 0 or normal_period <= 0:
            raise RuntimeError('Invalid RA timer or tracking period')

    def snapshot():
        # RAの観測時刻はj1要求と応答の中間。j2はその直後に読む。
        started = time.monotonic()
        ra = read24(ser, 'j1')
        ra_time = (started + time.monotonic()) / 2
        return {'RA': ra, 'DEC': read24(ser, 'j2')}, ra_time

    before, started = snapshot()
    move()
    after, ended = snapshot()
    elapsed = ended - started
    total = counter_delta(before['RA'], after['RA'])
    # G110の通常追尾はカウンター増加方向。RAパルス時の通常追尾分は、
    # 同じ測定区間を通常周期で回した場合の推定値。残差をガイド成分とする。
    # DECパルス中はRA速度を変更しないので、RAの実測差が通常追尾分。
    tracking = timer * elapsed / normal_period if axis == 'RA' else total
    return {'before': before, 'after': after, 'ra_counter_elapsed_s': elapsed,
            'ra_tracking_counts': tracking, 'ra_guide_counts': total - tracking}


def request(message, path=SOCKET_PATH, timeout=8.0):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((json.dumps(message, separators=(',', ':')) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError('Guide bridge connection closed')
            data += chunk
            if len(data) > 8192:
                raise RuntimeError('Bridge reply too large')
        answer = json.loads(data)
        if not answer.get('ok'):
            raise RuntimeError(answer.get('error', 'Guide bridge error'))
        return answer


class GuideSocket:
    def __init__(self, path=SOCKET_PATH, fraction=0.95, dec_positive_dir=1,
                 ra_positive_dir=0, manual_arcsec=20.0, gps_fix=None):
        if (not 0.25 <= fraction <= 0.95 or dec_positive_dir not in (0, 1)
                or ra_positive_dir != 0 or not 0.5 <= manual_arcsec <= 30.0):
            raise ValueError('Invalid guide settings')
        # 既存のastroeq_sun_tracking_guided.pyはmanual_arcsec=4.0を明示指定する。
        # 元の太陽GoToコードは変更せず、統合版の従来値4.0を20.0秒角に読み替える。
        # 旧値以外の明示指定はそのまま使う。
        if manual_arcsec == 4.0:
            manual_arcsec = 20.0
        self.path = path
        self.fraction = fraction
        self.dec_positive_dir = dec_positive_dir
        self.ra_positive_dir = ra_positive_dir
        self.manual_arcsec = manual_arcsec
        # GPS fix belongs to the Sun GoTo owner; advance its UTC with monotonic time.
        self.gps_fix = gps_fix
        self.fault = False
        self.sock = None
        # 応答遅延や切断だけでは所有者の終了と断定できない。接続拒否時だけ古いパスを除く。
        if os.path.exists(path):
            try:
                request({'cmd': 'STATUS'}, path=path, timeout=0.5)
            except (ConnectionRefusedError, FileNotFoundError):
                os.unlink(path)
            else:
                raise RuntimeError('Another solar GoTo bridge is already running')
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        os.chmod(path, 0o600)
        self.sock.listen(2)
        self.sock.setblocking(False)

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def poll(self, ser, send, read24, hex24, stopped, normal_period):
        try:
            conn, _ = self.sock.accept()
        except BlockingIOError:
            return
        with conn:
            conn.settimeout(0.5)
            terminal = None
            try:
                line = b''
                while not line.endswith(b'\n') and len(line) < 2048:
                    part = conn.recv(2048)
                    if not part:
                        break
                    line += part
                msg = json.loads(line)
                if msg.get('cmd') == 'STATUS':
                    resp = {'ok': True, 'state': 'TRACKING', 'f1': send(ser, 'f1'),
                            'f2': send(ser, 'f2')}
                    if self.gps_fix is not None:
                        fix_utc, fix_lat, fix_lon, fix_mono = self.gps_fix
                        resp.update(gps_utc=(fix_utc + timedelta(seconds=time.monotonic()-fix_mono)).isoformat(),
                                    gps_lat=fix_lat, gps_lon=fix_lon)
                elif msg.get('cmd') == 'PULSE':
                    axis, direction, ms = msg.get('axis'), msg.get('direction'), msg.get('ms')
                    if axis not in ('RA', 'DEC') or type(direction) is not int or direction not in (-1, 1) or type(ms) is not int or not MIN_GUIDE_PULSE_MS <= ms <= 2000:
                        raise ValueError('Invalid pulse parameters')
                    if not self._ra_tracking(send(ser, 'f1')):
                        raise RuntimeError('RA tracking not active; guide refused')
                    # CALだけ、シリアル所有プロセス内で同じ要求の前後を記録する。
                    record = msg.get('record_counters') is True
                    def move():
                        self._pulse(ser, send, read24, hex24, stopped, normal_period,
                                    axis, direction, ms, calibration=record)

                    resp = {'ok': True, 'state': 'TRACKING', 'axis': axis,
                            'direction': direction, 'ms': ms}
                    if record:
                        if self.fraction != 0.95 or ms != 1000:
                            raise ValueError('CAL requires 1000 ms and 0.95x sidereal')
                        resp.update(record_calibration_pulse(ser, read24, normal_period, axis, move))
                    else:
                        move()
                elif msg.get('cmd') == 'MOVE':
                    axis, clicks = msg.get('axis'), msg.get('clicks')
                    arcsec = msg.get('arcsec', self.manual_arcsec)
                    if (axis not in ('RA', 'DEC') or type(clicks) is not int or
                            type(arcsec) not in (int, float) or arcsec not in (20, 180) or
                            not 1 <= abs(clicks) <= 8):
                        raise ValueError('MOVE requires RA/DEC, 20/180arcsec and signed clicks 1..8')
                    if self.fault:
                        raise RuntimeError('Previous mount movement fault: restart and verify both axes')
                    if not self._ra_tracking(send(ser, 'f1')):
                        raise RuntimeError('RA tracking not active: MOVE refused')
                    if not stopped(send(ser, 'f2')):
                        raise RuntimeError('DEC axis not stopped: MOVE refused')
                    try:
                        moved = self._move(ser, send, read24, hex24, stopped,
                                           normal_period, axis, clicks, arcsec)
                    except Exception:
                        # A failed/mid-flight movement cannot be treated as an ordinary UI error.
                        self.fault = True
                        raise
                    resp = {'ok': True, 'state': 'TRACKING', 'axis': axis,
                            'clicks': clicks, 'counts': moved}
                else:
                    raise ValueError('Unknown command')
            except MountSafetyFault as exc:
                # 応答を返せる場合は切断前に理由を伝える。停止はシリアル所有者だけが行う。
                terminal = exc
                resp = {'ok': False, 'error': str(exc)}
            except Exception as exc:
                resp = {'ok': False, 'error': str(exc)}
            try:
                conn.sendall((json.dumps(resp) + '\n').encode())
            except OSError:
                pass
            if terminal is not None:
                raise terminal

    @staticmethod
    def _ra_tracking(status):
        return len(status) == 3 and all(int(c, 16) & 1 for c in status)

    def _wait_stopped(self, ser, send, stopped, n, timeout=MANUAL_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stopped(send(ser, f'f{n}')):
                return
            time.sleep(0.05)
        raise TimeoutError(f'MOVE axis{n} did not stop within {timeout:g}s')

    def _move(self, ser, send, read24, hex24, stopped, norm, axis, clicks, arcsec):
        """Execute one bounded manual relative GoTo while preserving Sun GoTo ownership.

        j1/j2 are motor-command counters, NOT encoder-derived proof of physical motion.
        The original Sun GoTo home reference stays in its owner process and its
        current-count HOME calculation naturally accounts for manual movements.
        """
        n = 1 if axis == 'RA' else 2
        cpr = read24(ser, f'a{n}')
        if not 1000 <= cpr <= 0xFFFFFF:
            raise RuntimeError(f'Invalid {axis} CPR: {cpr}')
        unit = math.floor(cpr * arcsec / ARCSEC_PER_REV + 0.5)
        count = unit * abs(clicks)
        if not 1 <= count <= 0x7FFFFF:
            raise RuntimeError('Manual move count out of range')
        pos_dir = self.ra_positive_dir if axis == 'RA' else self.dec_positive_dir
        bit = pos_dir if clicks > 0 else 1 - pos_dir
        before = read24(ser, f'j{n}')
        print(f'MOVE start: {axis} {clicks:+d}click ×{arcsec}arcsec, {count}counts, dir={bit}, from={before}', flush=True)
        # RA must be stopped only during RA move; DEC moves leave RA tracking running.
        if axis == 'RA':
            send(ser, 'K1')
            self._wait_stopped(ser, send, stopped, 1)
        send(ser, f'G{n}2{bit}')
        send(ser, f'H{n}' + hex24(count))
        send(ser, f'J{n}')
        self._wait_stopped(ser, send, stopped, n, timeout=30.0 if arcsec == 180 else MANUAL_TIMEOUT)
        send(ser, f'K{n}')
        self._wait_stopped(ser, send, stopped, n)
        after = read24(ser, f'j{n}')
        delta = counter_delta(before, after)
        sign = 1 if bit == 0 else -1
        if delta * sign < -2 or abs(delta) > count + 25:
            raise RuntimeError(f'{axis} counter mismatch: expected {count}, delta {delta}')
        if axis == 'RA':
            send(ser, 'G110')
            send(ser, 'I1' + hex24(norm))
            send(ser, 'J1')
            deadline = time.monotonic() + 3
            while not self._ra_tracking(send(ser, 'f1')):
                if time.monotonic() > deadline:
                    raise RuntimeError('RA tracking resume failed')
                time.sleep(0.05)
        elif not self._ra_tracking(send(ser, 'f1')):
            raise RuntimeError('RA tracking was interrupted by DEC MOVE')
        print(f'MOVE complete: {axis} {clicks:+d}click, counter delta={delta:+d}, RA tracking confirmed', flush=True)
        return delta

    def _pulse(self, ser, send, read24, hex24, stopped, norm, axis, direction, ms,
               *, calibration=False):
        if axis == 'RA':
            # CALの速度差は恒星時基準。通常周期は太陽時なので比率を換算する。
            fraction = self.fraction * 86400.0 / SIDEREAL_DAY if calibration else self.fraction
            period = round(norm / (1 + direction * fraction))
            if not 1 <= period <= 0xFFFFFF:
                raise ValueError('RA guide period out of range')
            changed = False
            try:
                changed = True
                send(ser, 'I1' + hex24(period))
                time.sleep(ms / 1000.0)
            finally:
                if changed:
                    send(ser, 'I1' + hex24(norm))
        else:
            if not stopped(send(ser, 'f2')):
                raise RuntimeError('DEC axis not stopped')
            dec_cpr = read24(ser, 'a2')
            dec_timer = read24(ser, 'b2')
            period = round(dec_timer * SIDEREAL_DAY / (dec_cpr * self.fraction))
            if not 1 <= period <= 0xFFFFFF:
                raise ValueError('DEC guide period out of range')
            bit = self.dec_positive_dir if direction > 0 else (1 - self.dec_positive_dir)
            started = False
            try:
                send(ser, 'G21' + str(bit))
                send(ser, 'I2' + hex24(period))
                send(ser, 'J2')
                started = True
                time.sleep(ms / 1000.0)
            finally:
                if started:
                    send(ser, 'K2')
            deadline = time.monotonic() + 2
            while not stopped(send(ser, 'f2')):
                if time.monotonic() > deadline:
                    raise MountSafetyFault('DEC failed to stop')
                time.sleep(0.05)
        if not self._ra_tracking(send(ser, 'f1')):
            raise RuntimeError('RA tracking lost after guide pulse')
