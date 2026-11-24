# Third-Party Notices

AstroEQ Solar Auto Guider の独自ソースには、同梱の `LICENSE` が適用されます。以下の外部プロジェクトと依存ソフトウェアには、それぞれのライセンスが適用されます。

## 連携・参考プロジェクト

| 名称 | 用途 | ライセンスと公式出典 |
|---|---|---|
| [AstroEQ](https://github.com/TCWORLD/AstroEQ) | 外部の赤道儀コントローラーとのUSBシリアル通信にAstroEQのプロトコルを使用します。AstroEQのファームウェアと公式ソフトウェアは同梱していません。 | MIT形式の独自ライセンス。変更内容のオープンソース公開、商業的利益を目的とする利用の禁止、元コードと公式リポジトリへのリンクに関する追加条件があります。[公式 License.txt](https://github.com/TCWORLD/AstroEQ/blob/master/License.txt) |
| [PHD2 / OpenPHDGuiding](https://openphdguiding.org/) | 別系統で開発しているDECバックラッシュ量測定・自動補償の考え方を参照しました。PHD2のソースコードを直接コピーしておらず、公開安定版にはPHD2もその開発機能も同梱していません。 | BSD-3-Clause。[公式 LICENSE.txt](https://github.com/OpenPHDGuiding/phd2/blob/master/LICENSE.txt) |

## 実行時依存ライブラリ・サービス

| 名称 | 用途 | ライセンスと公式出典 |
|---|---|---|
| [OpenCV](https://opencv.org/) | V4L2カメラ映像の取得、画像処理、太陽円盤の検出。 | Apache-2.0（実機確認版4.10.0）。[公式ライセンス案内](https://opencv.org/license/) |
| [NumPy](https://numpy.org/) | 太陽中心の座標、キャリブレーション行列、ガイド補正の数値計算。 | BSD-3-Clause。[公式 LICENSE.txt（2.2.4）](https://github.com/numpy/numpy/blob/v2.2.4/LICENSE.txt) |
| [Pillow](https://python-pillow.github.io/) | OLED画面、文字、カメラプレビューの画像描画。 | MIT-CMU（PIL由来）。[公式 LICENSE（11.1.0）](https://github.com/python-pillow/Pillow/blob/11.1.0/LICENSE) |
| [pySerial](https://github.com/pyserial/pyserial) | AstroEQ用USBシリアルポートの検出と通信。 | BSD-3-Clause。[公式 LICENSE.txt（3.5）](https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt) |
| [GPIO Zero](https://gpiozero.readthedocs.io/) | 状態表示LEDとスイッチのGPIO入出力。 | BSD-3-Clause。[公式ライセンス文書](https://gpiozero.readthedocs.io/en/latest/license.html) |
| [python-evdev](https://github.com/gvalkov/python-evdev) | Linux入力デバイスからロータリーエンコーダの回転イベントを取得。 | BSD-3-Clause。[公式 LICENSE](https://github.com/gvalkov/python-evdev/blob/master/LICENSE) |
| [lgpio](https://github.com/joan2937/lg) | GPIO ZeroのLGPIOバックエンドによるGPIOアクセス。 | Unlicense。[公式 UNLICENCE](https://github.com/joan2937/lg/blob/master/UNLICENCE) |
| [luma.oled](https://github.com/rm-hull/luma.oled) | SH1107 OLEDディスプレイの制御。 | MIT。[公式 LICENSE.rst](https://github.com/rm-hull/luma.oled/blob/main/LICENSE.rst) |
| [luma.core](https://github.com/rm-hull/luma.core) | OLEDドライバー向けのI2Cインターフェースと共通機能。 | MIT。[公式 LICENSE.rst](https://github.com/rm-hull/luma.core/blob/main/LICENSE.rst) |
| [gpsd / Python GPS interface](https://gpsd.io/) | gpsdが受信機データを提供し、本プログラムはPythonの `gps` モジュール経由でUTC・緯度・経度を取得。 | 主要部分はBSD-2-Clause。個別ファイルの条件は配布物を参照してください。[公式ライセンス案内](https://gpsd.gitlab.io/gpsd/faq.html)・[Debian配布物の著作権情報への案内](https://packages.debian.org/trixie/source/gpsd) |

AstroEQのコードやファームウェアを別途改変・再配布する場合、または商用利用を検討する場合は、対象物に適用されるAstroEQの原文と追加条件を確認してください。依存ライブラリを将来バイナリやOSイメージに同梱する場合は、実際に配布するパッケージの版と構成に沿ってライセンスおよび著作権表示を確認してください。
