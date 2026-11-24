#!/usr/bin/env python3
"""Launch the proven Solar GoTo owner, then start/restart camera UI during tracking only."""
import argparse
import os
import queue
import threading
import signal
import subprocess
import sys
import time
from astroeq_guide_socket import request, STATUS_TIMEOUT
from astroeq_startup_oled import StartupOLED


def startup_view(mode, camera_present, camera_error=False):
    # READYはGPIO21の所有者から受け取る。カメラの独立確認は追尾開始後だけ。
    if mode != 'START_CAMERA':
        return mode
    if not camera_present:
        return 'CAMERA_MISSING'
    return 'CAMERA_FRAME_ERROR' if camera_error else mode


def stop_child(proc, interrupt_timeout):
    """所有プロセス終了時に、応答待ちのOLED子プロセスを残さない。"""
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=interrupt_timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ra-west-dir', type=int, default=0, choices=(0, 1))
    p.add_argument('--dec-south-dir', type=int, default=1, choices=(0, 1))
    p.add_argument('--guide-fraction', type=float, default=0.95)
    p.add_argument('--cal-pulse-ms', type=int, default=1000)
    p.add_argument('--cal-drift', choices=('on', 'off'), default='on',
                   help='CALの自然ドリフト補正（次回実機試験では off）')
    p.add_argument('--cal-clicks', type=int, default=16)
    p.add_argument('--enable-guide-after-cal', action='store_true')
    p.add_argument('--astroeq-port', default=None)
    p.add_argument('--allow-below-horizon', action='store_true',
                   help='夜間テストのみ。地平線下への実機GoToに注意')
    args = p.parse_args()
    if not .25 <= args.guide_fraction <= .95:
        p.error('guide fraction must be 0.25..0.95')
    if not 500 <= args.cal_pulse_ms <= 2000 or not 1 <= args.cal_clicks <= 16:
        p.error('bad calibration parameters')

    # DO NOT launch a second independent serial owner.
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'astroeq-sun.service'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit('astroeq-sun.service is active: stop it before launching this integrated mode')

    sun_cmd = [sys.executable, '-u', 'astroeq_sun_tracking_guided.py', '--execute',
               '--ra-west-dir', str(args.ra_west_dir), '--dec-south-dir', str(args.dec_south_dir),
               '--guide-bridge', '--guide-fraction', str(args.guide_fraction)]
    if args.astroeq_port:
        sun_cmd += ['--astroeq-port', args.astroeq_port]
    if args.allow_below_horizon:
        sun_cmd.append('--allow-below-horizon')
    ui_cmd = [sys.executable, '-u', 'oled_ui_camera_calibration_1arcsec_dev.py',
              '--execute-mount', '--solar-goto-remote',
              '--arcsec-per-click', '20', '--cal-pulse-ms', str(args.cal_pulse_ms),
              '--cal-clicks', str(args.cal_clicks), '--guide-fraction', str(args.guide_fraction),
              '--cal-drift', args.cal_drift,
              '--ra-positive-dir', '0', '--dec-positive-dir', str(args.dec_south_dir)]
    if args.enable_guide_after_cal:
        ui_cmd.append('--enable-guide-after-cal')

    sun = None
    ui = None
    startup = None
    lines = queue.Queue()
    last_message_state = 'TITLE'
    startup_mode = 'PLEASE_WAIT'
    # USBカメラ不在では既存のOLED/カメラUIが起動できない。
    # 追尾本体には触れず、下記の専用画面でUSBコネクター抜き差しを促す。
    camera_error = threading.Event()
    last_ui_launch = float('-inf')
    UI_RETRY_SECONDS = 8.0
    camera_path = '/dev/video0'  # 現行UI側の既定値に合わせる。

    def read_sun_output(stream):
        """元GoToプロセスのログを転送し、UI切替に必要な通知だけキューへ送る。"""
        try:
            for line in stream:
                print(line, end='', flush=True)
                if ('SW1押下: 太陽GoToを開始します' in line
                        or '太陽時追尾開始・LED2点灯' in line
                        or 'SW2押下' in line
                        or 'LED1消灯：GoTo Home実行中' in line
                        or 'ホーム復帰完了。GPS再測位後' in line
                        or line.startswith(('STARTUP_STATE=', '太陽GoTo開始'))):
                    lines.put(line)
        finally:
            stream.close()

    def read_ui_output(stream):
        """OLED UIの出力をログへ転送し、USBカメラ障害だけを識別する。"""
        try:
            for line in stream:
                print(line, end='', flush=True)
                if ('カメラを開けません:' in line
                        or 'カメラフレームを取得できません' in line):
                    camera_error.set()
        finally:
            stream.close()

    def close_startup(*, preserve=False):
        nonlocal startup
        if startup is not None:
            try:
                startup.close(preserve=preserve)
            except Exception as exc:
                print(f'起動画面OLED終了エラー: {exc}', flush=True)
            startup = None

    def show_startup(mode):
        nonlocal startup
        if ui is not None and ui.poll() is None:
            return  # 既存UIがOLEDを使用中なら絶対に二重アクセスしない
        if startup is None:
            try:
                startup = StartupOLED()
            except Exception as exc:
                print(f'起動画面OLED初期化失敗: {exc}', flush=True)
                return
        try:
            startup.show(mode)
        except Exception as exc:
            print(f'起動画面OLED表示失敗: {exc}', flush=True)
            close_startup()

    try:
        # GPS測位/Ready待ちからOLEDを表示。GoTo本体のGPIO操作は従来どおり。
        show_startup('TITLE')
        title_until = time.monotonic() + 2.0  # 既存タイトルの絵を短時間確保する。
        sun = subprocess.Popen(sun_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
        reader = threading.Thread(target=read_sun_output, args=(sun.stdout,), daemon=True)
        reader.start()
        print('太陽GoTo起動：TITLE→SW1押下でGOTO→追尾確認で従来UI', flush=True)
        while sun.poll() is None:
            while True:
                try:
                    message = lines.get_nowait()
                except queue.Empty:
                    break
                if 'SW1押下: 太陽GoToを開始します' in message:
                    last_message_state = 'PRE_SLEW'
                    show_startup('PLEASE_WAIT')
                elif message.startswith('太陽GoTo開始'):
                    last_message_state = 'GOTO'
                    show_startup('SLEWING')  # 既存の始動指令送信後の通知。
                elif '太陽時追尾開始・LED2点灯' in message:
                    last_message_state = 'TRACKING'
                    show_startup('PLEASE_WAIT')
                elif message.startswith('STARTUP_STATE='):
                    startup_mode = message.partition('=')[2].strip()
                    if last_message_state == 'PRE_SLEW' and (
                            startup_mode.startswith('GPS_') or startup_mode == 'SUN_LOW'):
                        last_message_state = 'TITLE'
                elif ('SW2押下' in message or 'LED1消灯：GoTo Home実行中' in message):
                    # HOMEボタン押下直後から、Ready LEDが再点灯するまでHOME画面を保持。
                    last_message_state = 'HOME'
                    show_startup('HOME')
                elif 'ホーム復帰完了。GPS再測位後' in message:
                    # HOME移動完了後は、所有者が再判定する待機理由を表示する。
                    last_message_state = 'TITLE'
                    startup_mode = 'GPS_WAIT'

            try:
                if last_message_state == 'TRACKING':
                    resp = request({'cmd': 'STATUS'}, timeout=STATUS_TIMEOUT)
                    tracking = resp.get('state') == 'TRACKING' and resp.get('f1') == '111'
                else:
                    tracking = False
            except (TimeoutError, ConnectionResetError):
                # STATUSの応答遅延・一時切断だけでOLEDを停止しない。HOME通知、
                # ソケット消失、追尾プロセスの終了は従来の終了経路で処理する。
                tracking = (last_message_state == 'TRACKING' and sun.poll() is None
                            and ui is not None and ui.poll() is None)
            except (OSError, RuntimeError, ValueError):
                tracking = False

            if tracking and (ui is None or ui.poll() is not None):
                # カメラが抜けたときはUIを繰り返し即起動しない。
                # OLEDを占有するプロセスがないことを確認して警告を表示する。
                camera_present = os.path.exists(camera_path)
                if not camera_present or time.monotonic() - last_ui_launch < UI_RETRY_SECONDS:
                    show_startup(startup_view('START_CAMERA', camera_present, camera_error.is_set()))
                else:
                    # 追尾実機確認後、OLEDの所有権を既存UIへ引き渡す。
                    show_startup('START_CAMERA')
                    close_startup(preserve=True)
                    print('追尾確認：128x128 OLED + CAL/ガイドUI起動', flush=True)
                    camera_error.clear()  # 子プロセス起動前に前回のエラー状態を解除。
                    ui = subprocess.Popen(ui_cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, bufsize=1)
                    threading.Thread(target=read_ui_output, args=(ui.stdout,), daemon=True).start()
                    last_ui_launch = time.monotonic()
                    last_message_state = 'TRACKING'
            elif not tracking and ui is not None and ui.poll() is None:
                print('追尾解除またはHOME：OLEDのガイド側を停止', flush=True)
                stop_child(ui, interrupt_timeout=4)
                ui = None
                # ガイド側のOLED解放後にHOME画面を安全に表示。
                if last_message_state == 'HOME':
                    show_startup('HOME')
            elif not tracking and (ui is None or ui.poll() is not None):
                if last_message_state == 'TITLE':
                    mode = 'TITLE' if time.monotonic() < title_until else startup_mode
                    show_startup(mode)
                elif last_message_state == 'HOME':
                    show_startup('HOME')
            time.sleep(0.5)
        if sun.returncode != 0:
            raise SystemExit('太陽GoTo終了コード: ' + str(sun.returncode))
    except KeyboardInterrupt:
        print('ランチャー終了要求：ガイドを止めて太陽GoToへSIGINT', flush=True)
    finally:
        try:
            stop_child(ui, interrupt_timeout=5)
        finally:
            try:
                # 所有者には両軸の停止送信と状態読み取りの再試行が終わる時間を与える。
                stop_child(sun, interrupt_timeout=STATUS_TIMEOUT + 10)
            finally:
                close_startup()
        print('プロセス終了。実機の両軸停止は現物確認してください', flush=True)


if __name__ == '__main__':
    main()
