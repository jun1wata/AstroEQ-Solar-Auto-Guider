"""AstroEQ guide proxy. No serial port is opened here."""
from astroeq_guide_socket import request, STATUS_TIMEOUT, READ_TRANSACTION_BUDGET
from astroeq_tracking_relative_goto_1arcsec import SIDEREAL_DAY, FULL_ARCSEC

class RemoteGuideMount:
    def __init__(self, port=None, arcsec=4.0, ra_positive_dir=0, dec_positive_dir=1,
                 guide_fraction=0.95, cal_arcsec_per_step=0.5, cal_counts_multiplier=16,
                 cal_pulse_ms=1000):
        if ra_positive_dir != 0 or dec_positive_dir not in (0, 1):
            raise ValueError('Invalid motor direction')
        self.guide_fraction = guide_fraction
        self.cal_pulse_ms = cal_pulse_ms
        self.cal_pulse_arcsec = FULL_ARCSEC / SIDEREAL_DAY * guide_fraction * cal_pulse_ms / 1000
        self.manual_counts = {'RA': None, 'DEC': None}  # Source of truth stays in the serial owner
        self.arcsec = arcsec
        self.check_tracking()
        print('Remote mount: AstroEQ serial is owned by the Solar GoTo controller', flush=True)

    def check_tracking(self):
        data = request({'cmd': 'STATUS'}, timeout=STATUS_TIMEOUT)
        if data.get('state') != 'TRACKING' or data.get('f1') != '111':
            raise RuntimeError('Solar GoTo tracking is not confirmed')
        return data

    def pulse_guide(self, axis, direction, duration_ms):
        # 応答待ちだけを延長。PULSEはソケット側でも再送しない。
        return request({'cmd': 'PULSE', 'axis': axis, 'direction': direction,
                        'ms': duration_ms}, timeout=duration_ms / 1000 + 12 + 7 * READ_TRANSACTION_BUDGET)

    def move_cal_pulse(self, axis, direction):
        # 追尾側でパルスと位置採取をまとめ、他の指令の割り込みを避ける。
        return request({'cmd': 'PULSE', 'axis': axis, 'direction': direction,
                        'ms': self.cal_pulse_ms, 'record_counters': True},
                       timeout=self.cal_pulse_ms / 1000 + 12 + 11 * READ_TRANSACTION_BUDGET)

    def move(self, axis, clicks, arcsec=20):
        # ソケット指令に角度を付ける。旧30秒角の呼出しはそのまま互換。
        if (axis not in ('RA', 'DEC') or type(clicks) is not int or
                not 1 <= abs(clicks) <= 8 or
                arcsec not in (20, 180)):
            raise ValueError('Remote manual MOVE requires RA/DEC, 20 or 180 arcsec')
        self.check_tracking()
        base_timeout = 35.0 if arcsec == 180 else 20.0
        result = request({'cmd': 'MOVE', 'axis': axis, 'clicks': clicks,
                          'arcsec': arcsec}, timeout=base_timeout + 10 * READ_TRANSACTION_BUDGET)
        print(f'Remote manual relative GoTo: {axis} {clicks:+d}click ×{arcsec}arcsec / '
              f'counter delta={result["counts"]:+d}', flush=True)
        return result

    def close(self):
        pass  # MUST NOT send K1/K2: the Solar GoTo owner controls tracking and HOME
