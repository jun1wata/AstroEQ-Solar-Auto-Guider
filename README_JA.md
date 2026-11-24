# AstroEQ Solar Auto Guider

[English](README.md)

**AstroEQ用スタンドアロン太陽オートガイダー**

AstroEQ Solar Auto Guiderは、Raspberry Pi Zero 2Wで動作するPC不要の太陽GoTo・オートガイドコントローラーです。AstroEQ赤道儀コントローラーへ直接USB接続します。GPSから位置とUTCを取得、USBカメラで太陽中心を検出し、ガイドパルスでRA・DECを制御します。

システムの一例を以下の画像で示します。

![AstroEQ Solar Auto Guiderコントローラー外観](images/system.jpeg)

上部左から、USB接続のGPS受信機、太陽ガイド鏡に取付たUSBカメラ、AstroEQ赤道儀コントローラー、下が今回のAstroEQ Solar Auto Guiderです。

本プロジェクトは独立した非公式開発であり、AstroEQおよびPHD2/OpenPHDGuidingとは提携しておらず、承認・支援を受けたものでもありません。

## 簡単インストール

本プロジェクトの標準インストール対象は、Raspberry Pi Desktop搭載の **Raspberry Pi OS (64-bit)** です。インストーラの対応条件はDebian 13（Trixie）およびARM64です。

1. Raspberry Pi Imager v2.0.12で、デバイスに **Raspberry Pi Zero 2 W** を選択します。
2. OS一覧の一番上にある **Raspberry Pi OS (64-bit)** を選択します。説明欄には **A port of Debian Trixie with the Raspberry Pi Desktop** と表示されます。
3. Imagerでネットワークを設定してSSHを有効にし、OSをmicroSDカードに書き込んでRaspberry Piを起動します。

Raspberry Piがネットワークにつながり、Gitが使える状態になったら、次を実行します。

```bash
git clone https://github.com/jun1wata/AstroEQ-Solar-Auto-Guider.git
cd AstroEQ-Solar-Auto-Guider
sudo bash install.sh
```

インストールが正常に完了したら、表示される確認に従って再起動してください。再起動後、AstroEQ Solar Auto Guiderのサービスが自動起動します。インストール状態と接続機器は次のコマンドで確認できます。

```bash
sudo bash /opt/astroeq-solar-auto-guider/scripts/verify_install.sh \
  --post-reboot --hardware
```

アンインストール前には、第12章の手順で通常停止を行い、赤道儀が停止していることを確認してください。

```bash
sudo bash /opt/astroeq-solar-auto-guider/scripts/uninstall.sh --stop-active
```

手動設定、接続機器の確認、トラブルシューティングについては第6〜8章を参照してください。

## 1. コントローラー

![AstroEQ Solar Auto Guiderコントローラー外観](images/IMG_5224.jpeg)

128×128 OLED、状態表示LED、GoTo・HOMEスイッチ、ロータリーエンコーダを1つのケースに組み込んでいます。電源投入時にRaspberry Piがコントローラーソフトウェアを起動し、GPS、AstroEQ、カメラ、太陽高度の必要な確認が完了した場合にReady状態になります。

| 表示・操作部 | 機能 |
|---|---|
| OLED | 起動状態、手動操作、ガイドメニュー、キャリブレーション状態、設定、ガイドカメラ画像を表示 |
| Ready LED（緑） | 起動時の確認を通過し、GOTO可能な場合に点灯 |
| GoToスイッチ | 待機状態から太陽GoToを開始 |
| Tracking LED（青） | 赤道儀が追尾状態へ移行すると点灯 |
| HOMEスイッチ | 追尾を停止し、GoTo直前に記録した位置へ復帰 |
| ロータリーエンコーダ | 手動移動、ガイド機能、キャリブレーション、設定を選択し、軸スイッチで決定 |

記録されるHOME位置はモーターカウンター上の基準であり、絶対位置を検出するホームセンサーには対応していません。

## 2. ケース内部

![AstroEQ Solar Auto Guiderケース内部](images/IMG_5226.jpeg)

ケース内にはRaspberry Pi Zero 2W、 OLEDモジュール、前面操作部、状態表示LEDと配線を収めています。GPS、AstroEQ、UVC接続のUSBカメラはRaspberry PiのUSBホスト経路へ接続します。

この写真は実機の記録として掲載しています。

## 3. 使用したハードウェア

| 機器 | 実機構成 |
|---|---|
| コンピューター | Raspberry Pi Zero 2W |
| 表示器 | 1.5" OLED MC01506、128×128、I2Cアドレス`0x3C` |
| スイッチ | GoToスイッチ、HOMEスイッチ、押しボタン式スイッチ |
| スイッチ付きロータリーエンコーダ| EC12PLGRSDVF-D-25K-24-24C-31/0 |
| LED | Ready LED、Tracking LED |
| GPS | u-blox USB GPS、VID:PID `1546:01A7` |
| 赤道儀コントローラー | USBシリアルで直接接続するAstroEQ |
| ガイドカメラ | UVC接続USBカメラ |
| 主望遠鏡 | 実機試験で太陽Hα撮影に使用した望遠鏡 |
| ガイド鏡 | 同じ赤道儀へ搭載した太陽ガイド鏡 |

ソースコードは、次のAstroEQ USB識別番号を認識します。

| AstroEQ USBインターフェース | VID:PID |
|---|---|
| CH340搭載コントローラー | `1A86:7523` |
| Arduino Mega搭載コントローラー | `2341:0042` |

これらは実機で使用する検出経路を示すものであり、同じUSBインターフェースを持つすべてのコントローラーとの互換性を保証するものではありません。

### 3.1 補助部品と電源・USB接続

| 補助部品 | 用途・確認事項 |
|---|---|
| microSDカード | Piの起動メディア。容量・型番は要実機確認。 |
| Pi用電源・micro-USB電源ケーブル | 基板の電源入力に使用。実機の給電経路、電源の出力電圧・電流定格は要実機確認。 |
| USBハブ・micro-USB OTGアダプターまたはホスト用ケーブル | 3台のUSB機器を接続する構成例。ハブの型番・給電方式、実際の接続部品は要実機確認。 |
| LED用電流制限抵抗 | LEDをGPIOから直接駆動する場合は各LEDに1本。実機の抵抗値は要実機確認。 |
| 10 nFコンデンサー | エンコーダのA・B各入力からGNDへ1個ずつ、計2個。安定版ソースに記載されている配線です。 |
| ケース・固定部品・配線材 | Pi、OLED、LED、操作部品を固定・配線。部品型番は要実機確認。 |

Raspberry Pi Zero 2Wには、給電用のmicro-USB端子 `PWR IN` とUSBホスト用に使えるmicro-USBデータ端子があります。ホスト接続の構成例では、データ端子からOTGアダプターまたはホスト用ケーブルでハブの上流側につなぎ、下流側にAstroEQのUSBシリアル、USB GPS、USBガイドカメラを接続します。基板仕様は[Raspberry Pi公式資料](https://www.raspberrypi.com/documentation/computers/zero.html)を参照してください。PiとUSB機器の給電は、使用する機器の仕様と消費電流を確認して決めてください。赤道儀の駆動電源はAstroEQと赤道儀の仕様に従って接続します。

## 4. Raspberry Pi ZeroのGPIO接続

本プロジェクトのGPIO番号は**BCM番号**です。物理ピン番号とは区別してください。

| 部品 | BCM GPIO | 物理ピン番号 | 接続・役割 |
|---|---:|---:|---|
| Ready LED | GPIO21 | 40 | 点灯時はHIGHを出力します。 アノードー電流制限抵抗470ΩーGPIO21 カソードはGNDへ接続|
| Tracking LED | GPIO20 | 38 | 点灯時はHIGHを出力します。アノードー電流制限抵抗470ΩーGPIO20 カソードはGNDへ接続|
| GoToスイッチ | GPIO22 | 15 | GPIO22とGND間の接点検出 内部プルアップを使用し、押すとGNDへ導通する入力です。|
| HOMEスイッチ | GPIO26 | 37 | GPIO26とGND間の接点検出 内部プルアップを使用し、押すとGNDへ導通する入力です。|
| ロータリーエンコーダA | GPIO5 | 29 | Linux rotary-encoder overlay入力 A―GND間に10 nFを接続します。|
| ロータリーエンコーダB | GPIO6 | 31 | Linux rotary-encoder overlay入力 B―GND間に10 nFを接続します。|
| ロータリーエンコーダC | GND | 30 | GND |
| ロータリー押し込みスイッチ | GPIO13 | 33 | GPIO13とGND間の接点検出 |
| OLED SDA | GPIO2 | 3 | I2Cデータ |
| OLED SCL | GPIO3 | 5 | I2Cクロック |
| OLED VCC | 3.3V | 1 | 3.3V |
| OLED GND | GND | 6 | GND |

GPIO配線は3.3V系で行います。GPIO端子へ5Vを直接接続しないでください。

## 5. 操作の流れ

AstroEQ Solar Auto Guiderは、Raspberry Pi Zero 2Wを使ったスタンドアロン太陽GoTo・オートガイダーです。起動時の確認が完了すると、太陽GoTo、追尾、手動操作、オートガイド、HOME復帰の順に操作できます。本章では、コントローラーのLEDとOLED画面を写真で示しながら、操作方法を説明します。

> **安全上の注意：** 太陽観測には適切な太陽観測用光学系とフィルターを使用してください。GoTo・追尾・HOME動作中は赤道儀の可動範囲やケーブルを監視し、異常時にモーター駆動電源を遮断できるようにしてください。GoToスイッチやHOMEスイッチは緊急停止装置ではありません。

### 5.1 Ready LED点灯

電源投入後、Raspberry Piが制御プログラムを起動し、GPS測位、AstroEQ赤道儀コントローラーとの通信、太陽高度など、GoTo開始に必要な条件を確認します。準備が整うと**Ready LED（緑）**が点灯し、OLEDに`PRESS GOTO` / `TO START`と表示されます。この時、太陽望遠鏡はNCP方向、赤道儀のウエイト軸は鉛直下向きのホームポジションに設定しておきます。

![Ready LED点灯・GoTo待機画面](images/ready.jpeg)

Ready LEDが点灯しない場合は、OLEDの待機表示を確認してください。GPS測位待ちでは`WAITING FOR GPS`、測位できない場合は`NO GPS FIX`、AstroEQが応答しない場合は`ASTROEQ` / `NO RESPONSE`などが表示されます。

### 5.2 GOTOボタン

赤道儀を所定のホーム姿勢（鏡筒を天の北極方向へ向け、カウンターウエイト軸を鉛直下）に合わせ、周囲とケーブルの安全を確認します。Ready LEDが点灯している状態で**GOTOボタン**を押すと、太陽位置への自動導入を開始します。

OLEDには、受付直後に`PLEASE WAIT...`、移動開始後に`SLEWING` / `TO SUN...`が表示されます。

![太陽GoTo動作中の表示](images/slewing.jpeg)

GoTo中は赤道儀が自動的に回転します。鏡筒やケーブルの干渉に注意し、動作を監視してください。

### 5.3 Tracking LED点灯

太陽位置へのGoToが完了して追尾が開始されると、**Tracking LED（青）**が点灯します。続いてガイドカメラの初期化が行われ、OLEDに`STARTING` / `CAMERA...`が表示された後、手動操作画面`MOUNT MANUAL`に切り替わります。

Tracking LEDの点灯は**赤道儀が追尾状態になったこと**を示します。オートガイドによる画像補正が始まったことを示すものではありません。オートガイドには、後述するキャリブレーションが必要です。

### 5.4 手動操作モード

追尾開始後、OLEDには`MOUNT MANUAL`が表示されます。この画面では、ロータリーエンコーダの**短押し**で移動軸と移動量を選択し、**ノブを回す**ことで赤道儀を微動できます。

![MOUNT MANUAL手動操作画面](images/mount_manual.jpeg)

| 選択項目 | 操作する軸 | 1クリック当たりの移動量 |
|---|---|---:|
| `RA HI` | RA（赤経） | 180秒角 |
| `RA LOW` | RA（赤経） | 20秒角 |
| `DEC HI` | DEC（赤緯） | 180秒角 |
| `DEC LOW` | DEC（赤緯） | 20秒角 |

初期選択は`RA HI`です。移動中は画面上部に`MOVING...`が点滅します。ロータリーエンコーダを**長押し（0.8秒以上）**すると、オートガイド画面へ移ります。

### 5.5 オートガイドモード

手動操作モードからロータリーエンコーダを長押しすると、オートガイド画面に切り替わります。キャリブレーション前は`AUTO GUIDE: OFF`と表示されます。ロータリーエンコーダを回して項目を選び、短押しで開きます。

![オートガイドメニュー画面](images/auto_guide.jpeg)

#### 5.5.1 SET：カメラとガイドの設定

`SET`を選ぶと、カメラ設定とオートガイドの補正条件を変更できます。

![SET設定画面](images/auto_guide_set.jpeg)

| 項目 | 機能 |
|---|---|
| `CAM DEFAULT` | カメラ設定を既定値に戻す |
| `CAM MANUAL` | カメラ設定を手動で調整する |
| `RA MAX PULSE` | RA方向の最大補正パルス時間を設定する |
| `DEC MAX PULSE` | DEC方向の最大補正パルス時間を設定する |
| `GUIDE PERIOD` | ガイド補正の周期を設定する |
| `EXIT` | 設定画面を終了する |

設定画面を開くとガイド補正は一時停止します。`EXIT`でガイド画面に戻ります。

#### 5.5.2 CENTER：太陽中心の確認

`CENTER`を選ぶと、ガイドカメラ画像に表示される太陽円盤と、画像処理で検出した中心位置を確認できます。CANNYはCANNYフィルターで高精度の中心点検出、OTSUは大津の二値フィルターでの中心点検出、NO DISKは未検出を示します。

![CENTER太陽中心確認画面](images/auto_guide_center.jpeg)

キャリブレーションを開始する前に、太陽円盤が画面内に十分収まり、検出中心が安定していることを確認してください。短押しでガイド画面へ戻ります。

#### 5.5.3 CALIB：RA・DECキャリブレーション

`CALIB`を選ぶと、`CAL START`と`BACK`が表示されます。太陽像が安定していること、赤道儀が安全に移動できることを確認し、`CAL START`を選んで短押しすると、キャリブレーションが始まります。

![CALIBキャリブレーション画面](images/auto_guide_calib.jpeg)

キャリブレーションでは、RA・DEC方向にガイドパルスを送り、太陽中心の移動量から**カメラ画像のX・Y方向と赤道儀のRA・DEC方向の対応関係**を求めます。進行中は`CALIBRATION` / `MEASURING...`、`RA+ 1`や`DEC- 1`などの軸・方向・パルス回数、および測定変位が表示されます。DEC測定の前には、機械的な遊びを取り除く処理（最大24パルス）が行われます。

キャリブレーションが成功するとガイド画面へ戻ります。公開手順の`--enable-guide-after-cal`を指定して起動している場合は、安定した太陽中心からガイド基準点を取得した後、`GUIDE ON`に移行します。失敗すると`CAL FAILED`が表示され、ガイド補正は無効になります。

**注意：** キャリブレーション実行中は画面に`PRESS: BACK`と表示されても、短押しによる即時中断はできません。危険な動作があれば独立した駆動電源遮断手段を使用してください。

### 5.6 HOMEボタン

追尾を終了するときは、赤道儀が安全に復帰できることを確認してから、**HOMEボタン**を押します。ガイドを終了して追尾を停止し、停止が確認できた場合に、太陽GoTo直前に記録した位置へ復帰します。復帰中のOLEDには`GOTO` / `HOME` / `POSITION`と表示されます。

![HOME位置への復帰画面](images/goto_home.jpeg)

HOME位置はAstroEQの**モーターカウンターから記録した基準位置**であり、ホームセンサーで確認した絶対位置ではありません。脱調、電源断、手動移動などによって実際の姿勢とカウンターの対応が失われた場合は、自動HOMEを使用しないでください。復帰が完了したら、赤道儀が実際に停止していることを確認します。ホームに戻るとReadyランプが点灯し、GOTOできる状態になります。

## 6. Raspberry Pi Zeroのセットアップ

### 6.1 実機確認済みソフトウェア環境

| 構成要素 | 安定版実機のバージョン |
|---|---|
| Debian | 13.7（trixie）、ARM64 |
| Linux kernel | `6.18.50+rpt-rpi-v8` |
| Python | 3.13.5 |
| OpenCV | 4.10.0 |
| NumPy | 2.2.4 |
| Pillow | 11.1.0 |
| pySerial | 3.5 |
| GPIO Zero | 2.0.1 |
| python-evdev | 1.9.1 |
| lgpio | 0.2.2.0 |
| luma.oled | 3.16.0 |
| luma.core | 2.4.2 |
| gpsd / Python GPS interface | 3.25 |
| v4l-utils | 1.30.1 |

実機はapt中心のシステムワイド環境で、Python仮想環境を使用していません。Debianリポジトリの更新後は、提供されるパッケージのバージョンが変わる場合があります。

### 6.2 I2C・USBホスト・ロータリーエンコーダ

`/boot/firmware/config.txt`へ次の設定を追加します。

```ini
dtparam=i2c_arm=on
dtoverlay=dwc2,dr_mode=host
dtoverlay=rotary-encoder,pin_a=5,pin_b=6,relative_axis=1,steps-per-period=2
```

ブート設定の変更後は再起動します。ロータリーエンコーダoverlayは、プログラムが`evdev`を介して読むLinux入力デバイスを作成します。

### 6.3 システムパッケージ

Raspberry Piのターミナルで、利用可能なDebianパッケージをインストールします。aptパッケージ名とPyPI配布名を混同しないでください。

```bash
sudo apt update
sudo apt install python3-opencv python3-numpy python3-pil python3-serial \
  python3-gpiozero python3-evdev python3-lgpio \
  python3-luma.oled python3-luma.core python3-gps \
  gpsd gpsd-clients v4l-utils i2c-tools
```

Debian 13（trixie）ARM64のaptパッケージは、`python3-luma.oled`が`3.10.0-1`、`python3-luma.core`が`2.4.2-1`です。このため、aptだけでは実機確認済みのluma.oled 3.16.0になりませんが、luma.coreは実機確認済みの2.4.2と一致します。

実機と同じバージョンが必要な場合に限り、サービスを実行するユーザーで次のようにユーザー領域へ固定版を導入できます。Debian 13のPythonはexternally-managed環境であり、`--break-system-packages`はその保護を解除します。`--user`を付けてもインストールした版がapt版より優先され、同じユーザーで動く他のPythonプログラムへ影響する可能性があるため、影響を理解した場合だけ使用し、無条件には実行しないでください。

```bash
sudo apt install python3-pip
python3 -m pip install --user --break-system-packages 'luma.core==2.4.2' 'luma.oled==3.16.0'
python3 -c "import importlib.metadata as m; print(m.version('luma.core'), m.version('luma.oled'))"
```

実機環境と照合するには、OS・aptパッケージの版と、実行時のPythonが読み込む場所を確認します。固定版を追加した場合も、サービスの`User`と同じユーザーで実行してください。

```bash
cat /etc/os-release /etc/debian_version
dpkg --print-architecture
uname -r
/usr/bin/python3 --version
dpkg-query -W python3-opencv python3-numpy python3-pil python3-serial \
  python3-gpiozero python3-evdev python3-lgpio \
  python3-luma.oled python3-luma.core python3-gps gpsd v4l-utils
apt-cache policy python3-luma.oled python3-luma.core
/usr/bin/python3 - <<'PY'
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
for name, dist in (
    ("cv2", "opencv-python"), ("numpy", "numpy"), ("PIL", "Pillow"),
    ("serial", "pyserial"), ("gpiozero", "gpiozero"), ("evdev", "evdev"),
    ("lgpio", "lgpio"), ("gps", "gps"), ("luma.core", "luma.core"),
    ("luma.oled.device", "luma.oled"),
):
    mod = import_module(name)
    value = getattr(mod, "__version__", None)
    if value is None:
        try:
            value = version(dist)
        except PackageNotFoundError:
            value = "dpkg-queryの結果を参照"
    print(f"{name}: {value}  {getattr(mod, '__file__', '')}")
from luma.oled.device import sh1107
PY
```

実機確認済みの組み合わせを再現する場合は、`luma.oled`が`3.16.0`、`luma.core`が`2.4.2`であり、`sh1107`をインポートできることを確認します。別の版が出る場合は、実行ユーザー、Pythonのパス、表示されたモジュールの場所を照合してください。

実行ユーザーには、プログラムが使うデバイスへのアクセス権が必要です。一般的なグループは`dialout`、`video`、`input`、`gpio`、`i2c`ですが、ユーザー追加前に実機のデバイス所有グループを確認してください。

```bash
sudo usermod -aG dialout,video,input,gpio,i2c USERNAME
```

グループ変更後はログアウト・ログインまたは再起動が必要です。自動起動を有効にする前に、アドレス`0x3C`のOLED、V4L2カメラ、gpsd経由のGPS測位、AstroEQシリアルデバイスを個別に確認してください。

### 6.4 接続機器の確認（必要な場合）

第6.2節の設定を適用して再起動し、第6.3節の依存パッケージを導入した後に確認します。以下は、統合プログラムを起動する前に、サービスの実行ユーザーで行います。`lsusb`がない場合は、aptで`usbutils`を導入してください。

**OLED**

```bash
i2cdetect -l
i2cdetect -y 1
```

I2Cバス1があり、スキャンで`3c`が見えることを確認します。`UU`はそのアドレスをカーネルドライバーが使用中であることを示します。`3c`が表示されない場合は、バス番号、OLEDの電源、GND、SDA・SCL、アドレスを確認します。権限エラーの場合は`/dev/i2c-1`の所有グループと実行ユーザーを確認します。

**ロータリーエンコーダ**

```bash
/usr/bin/python3 - <<'PY'
from evdev import InputDevice, list_devices
for path in list_devices():
    device = InputDevice(path)
    try:
        print(path, device.name)
    finally:
        device.close()
PY
```

実行ユーザーから`rotary@5`が1個見えることを確認します。見つからない場合は第6.2節のoverlayと`/dev/input/event*`の権限を確認します。押し込みスイッチの入力は別経路のBCM13です。

**GPSとAstroEQ**

```bash
lsusb
systemctl status --no-pager gpsd.socket
gpspipe -w -n 20
/usr/bin/python3 -m serial.tools.list_ports -v
```

`lsusb`ではGPSの`1546:01a7`と、第3章に載っているAstroEQのVID:PIDを確認します。GPSは`gpspipe`の`DEVICES`に受信機のパスがあり、`TPV`で有効な測位とUTC・緯度・経度が得られることを確認します。USB一覧にGPSがあるのに`DEVICES`に出ない場合は、`gpsd`への登録と`journalctl -u gpsd.service -b --no-pager`を確認します。

AstroEQはシリアル一覧のVID:PIDとデバイス名を照合します。実行ユーザーの読み書き権限と、別プロセスがポートを使用していないことも確認します。AstroEQからの応答は、第7章の統合起動で確認します。

**USBガイドカメラ**

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --all
v4l2-ctl -d /dev/video0 --list-formats-ext
```

統合起動の既定デバイスは`/dev/video0`です。このノードが対象カメラの映像キャプチャ用であることと、対応フォーマットを確認します。安定版の要求値は`YUYV`、800×600、15 fpsです。実際に選ばれるフォーマットは機器とドライバーに依存します。フレーム取得は追尾開始後のカメラ初期化時に確認されます。

## 7. Pythonスクリプトのインストール

### 7.1 ワンコマンドインストーラ

インストーラの対応対象は、Debian 13（Trixie）をARM64で動かすRaspberry Pi Zero 2 Wです。標準のRaspberry Pi OS (64-bit) の導入は、冒頭の「簡単インストール」を参照してください。先にネットワークとSSHを設定し、リポジトリをcloneしてインストーラを1回実行します。

```bash
git clone https://github.com/jun1wata/AstroEQ-Solar-Auto-Guider.git
cd AstroEQ-Solar-Auto-Guider
sudo bash install.sh
```

インストーラは、変更前にハードウェア、OS、aptリポジトリ、空き容量、既存サービス、boot設定を確認します。第6.3節のDebianパッケージを導入し、実機確認済みの`luma.core 2.4.2`と`luma.oled 3.16.0`だけを隔離したPython環境で使用します。`--break-system-packages`は使用せず、サービスの起動、AstroEQシリアルポートのopen、モーターコマンドの送信も行いません。`/boot/firmware/config.txt`をバックアップしてから、不足していて競合しない設定だけを追加し、安定版ソース16本を`/opt/astroeq-solar-auto-guider/src`へ配置して、次回起動用にサービスを有効化します。

最後の確認で、準備ができてから再起動してください。非対話実行では、明示的に指定した場合だけ再起動します。

```bash
sudo bash install.sh --reboot
```

再起動後は、次の読み取り中心の検証を実行します。GPS、AstroEQ、カメラ、OLEDが未接続の場合は警告として報告し、AstroEQコマンドは送信しません。

```bash
sudo bash /opt/astroeq-solar-auto-guider/scripts/verify_install.sh --post-reboot --hardware
```

アンインストール前には第12章の通常停止を行い、赤道儀が停止していることを確認してください。アンインストーラは`--stop-active`を明示しない限り、動作中のサービスを停止しません。管理対象ファイルと実行データは削除せず`/var/backups/astroeq-solar-auto-guider`へ退避し、他のソフトウェアも使用し得るDebianパッケージは残します。

```bash
sudo bash /opt/astroeq-solar-auto-guider/scripts/uninstall.sh --stop-active
```

### 7.2 手動インストール

リポジトリを任意のインストール先へ配置します。以下では`/home/pi/AstroEQ-Solar-Auto-Guider`を例示します。実際のユーザー名とパスに置き換えてください。

```bash
git clone https://github.com/jun1wata/AstroEQ-Solar-Auto-Guider.git /home/pi/AstroEQ-Solar-Auto-Guider
```

`src`には、変更していない安定版Pythonソース16本を収録します。

| 役割 | ファイル |
|---|---|
| 統合起動プログラム | `run_solar_goto_with_oled.py` |
| 太陽位置・GoTo・追尾 | `astroeq_sun_position.py`、`astroeq_sun_tracking_generic_ch340.py`、`astroeq_sun_tracking_guided.py`、`astroeq_tracking_relative_goto_1arcsec.py` |
| 赤道儀・ガイドインターフェース | `astroeq_remote_mount.py`、`astroeq_guide_socket.py`、`astroeq_pulse_autoguide.py`、`astroeq_guide_quality.py` |
| カメラキャリブレーション | `astroeq_camera_calibration_halfarcsec.py` |
| 起動・OLED操作画面 | `astroeq_startup_oled.py`、`oled_ui_dev.py`、`oled_ui_camera_calibration_1arcsec_dev.py`、`solar_center_oled_modes_right.py`、`ssd1315_encoder.py`、`ssd1315_oled_modes_right.py` |

`src`ディレクトリから統合起動プログラムを実行します。

```bash
cd /home/pi/AstroEQ-Solar-Auto-Guider/src
python3 -u run_solar_goto_with_oled.py \
  --ra-west-dir 0 \
  --dec-south-dir 1 \
  --enable-guide-after-cal \
  --cal-drift off
```

方向引数は実機固有です。無人運転を行う前に、安全な条件で両軸を確認してください。別の赤道儀で使われている方向値を未確認のままコピーしないでください。

最初の手動起動は、第8章のサービスを有効にする前に、サービスの`User`に指定するユーザーで行います。すでにサービスを使用している場合は、第12章の通常停止を済ませ、サービスを停止してから手動起動してください。旧`astroeq-sun.service`や他のAstroEQ制御ソフトも同時に起動しないでください。

初回は第12章の無動作確認から行い、端末のログとOLEDの待機理由を確認します。手動起動を終えるときは赤道儀が停止していることを確認してから`Ctrl+C`を押します。

## 8. 電源投入時の自動起動

1本のsystemdサービスで統合プログラムを起動できます。実機設定は`Restart=no`を使用し、`ExecStartPre`によるUSB接続待機を使用しません。

```ini
[Unit]
Description=AstroEQ Solar Auto Guider
Wants=gpsd.socket
After=gpsd.socket systemd-udev-trigger.service
[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/AstroEQ-Solar-Auto-Guider/src
ExecStart=/usr/bin/python3 -u /home/pi/AstroEQ-Solar-Auto-Guider/src/run_solar_goto_with_oled.py --ra-west-dir 0 --dec-south-dir 1 --enable-guide-after-cal --cal-drift off
Restart=no
[Install]
WantedBy=multi-user.target
```

上記定義の`User`、`WorkingDirectory`、`ExecStart`内のユーザー名とパスを実際の値へ置き換え、次のコマンドでサービスファイルを配置します。

```bash
sudoedit /etc/systemd/system/astroeq-solar-auto-guider.service
```

上記のサービス定義を保存した後、systemdへ反映して有効化・起動します。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now astroeq-solar-auto-guider.service
```

サービス状態と今回の起動以降のログを確認します。

```bash
systemctl status --no-pager astroeq-solar-auto-guider.service
journalctl -u astroeq-solar-auto-guider.service -b --no-pager
```

サービスが`active`でも、GPS測位や機器の確認が続いていることがあります。OLEDの表示と上記のログを照合してください。`failed`または即時終了の場合は、ログの最初のエラーを確認します。

- `ModuleNotFoundError`やOLEDのインポートエラーは、第6.3節の手順をサービスの`User`と`/usr/bin/python3`で確認します。
- パスや権限のエラーは、`WorkingDirectory`、`ExecStart`のスクリプトパス、対象デバイスの所有グループを確認します。
- GPS待ち、AstroEQ無応答、カメラの認識・起動エラーは、第6.4節の機器確認に戻ります。赤道儀を安全に停止させてからUSB接続を点検します。

`Restart=no`のため、異常終了したサービスは自動で起動し直しません。原因を解消し、赤道儀の停止と姿勢を確認してから起動します。ユニット定義を変更した場合は、起動前に`sudo systemctl daemon-reload`を実行してください。

```bash
sudo systemctl start astroeq-solar-auto-guider.service
```

## 9. AstroEQ赤道儀コントローラーの制御方法

### 9.1 USBシリアル直接制御

Raspberry PiがAstroEQの直接ホストになります。PC、ASCOM、INDI、PHD2プロセスは不要です。起動プログラムは対応するUSB識別番号を検出し、9600 bit/sでAstroEQシリアル接続を開き、応答確認とGoTo・ガイドに必要なモーターパラメーター取得を行います。

統合構成では、物理シリアル接続の所有者を1つに保ちます。カメラキャリブレーションとオートガイドは、競合するシリアル接続を開かず、ローカルガイドインターフェース経由で赤道儀の移動を要求します。

### 9.2 太陽中心検出

ガイドカメラをV4L2デバイスとして読み込みます。画像処理で太陽輪郭を検出し、太陽円盤の中心と半径を求めます。キャリブレーションでは短いフレーム列から安定した中心を要求し、検出できない場合や中心のばらつきが大きい場合は、赤道儀移動へ変換せずに不成立とします。

OLEDカメラ表示では、実装されている`CANNY`、`OTSU`、`NONE`の表示モードを使用できます。これらは画面表示の選択であり、110分間試験の実測データの意味を変更するものではありません。

### 9.3 RA・DECキャリブレーション

キャリブレーションでは、ガイド速度パルスによる画像変位を測定し、カメラX・Y方向と赤道儀RA・DEC方向の対応行列を作成します。公開するサービス引数では`--cal-drift off`によりドリフト測定を無効化し、成功後は`--enable-guide-after-cal`によりオートガイドへ移行できます。

安定版のキャリブレーション順序は次のとおりです。

1. 安定した太陽中心を取得する。
2. 実装上のパルス上限内で目標変位へ到達するまでRA正方向パルスを送る。
3. 同じパルス数でRAを逆方向へ戻す。
4. 次節のDEC機械的遊び取りを行う。
5. DEC正方向パルスで測定し、同じパルス数で逆方向へ戻す。
6. 2本のキャリブレーションベクトルを検証し、ガイド変換を有効にする。

安定版アルゴリズムは、利用可能なガイド速度の95%で1000msのキャリブレーションパルスを使用し、目標変位を180秒角、各軸の正方向測定を最大60パルスとしています。これらは安定版ソース内のキャリブレーション条件であり、ガイド性能値ではありません。

### 9.4 DEC機械的遊び取り：最大24パルス

DECキャリブレーションの前に、安定版ソースはDECの一方向へパルスを送り、隣接画像間の太陽中心移動を確認します。一貫した移動検出を要求し、最大24パルスで停止します。この範囲で有効な遊び取りを確認できない場合は、そのままキャリブレーションを続けず失敗とします。

この処理は、DECキャリブレーションベクトル測定前に機械的な遊びを取り除くためのものです。

### 9.5 速度×時間方式のRA・DECオートガイド

キャリブレーション後、安定した太陽中心サンプルから固定ガイド基準を記録します。各補正では、X・Y中心誤差をキャリブレーション行列で変換し、1回の補正周期で1軸を選択し、計算した時間だけAstroEQのガイド速度で駆動します。パルス時間は、軸ごとに設定された最大時間で制限されます。

OLEDの`SET`では、RA最大パルス、DEC最大パルス、ガイド周期を変更できます。ガイドゲインはガイドUIプログラムの起動引数`--guide-gain`で指定され、パルス補正量の計算に使われます。統合起動はこの引数を渡さないため、ガイドUIの既定値0.2が使われます。安定版UIの初期値には最大500msパルスと5秒ガイド周期が含まれます。短時間の太陽円盤検出失敗から任意の補正を生成せず、検出喪失が長引いた場合はガイドを停止します。

### 9.6 OLEDとロータリーエンコーダ操作

#### 起動から手動操作まで

電源投入後に統合プログラムが起動すると、OLEDに`AstroEQ`、`Solar`、`Auto`、`Guider`のタイトルが表示され、その後は機器の確認状況が表示されます。GPS待機中は`WAITING FOR GPS`、未測位時は`NO GPS FIX`、AstroEQが応答しない場合は`ASTROEQ` / `NO RESPONSE`です。

Ready LED（緑）が点灯したときのOLED表示は`PRESS GOTO` / `TO START`です。第12章の事前確認を済ませてからGoToスイッチを押します。受付後は`PLEASE WAIT...`、駆動開始後は`SLEWING` / `TO SUN...`が表示されます。追尾が始まるとTracking LED（青）が点灯し、カメラ初期化の`STARTING` / `CAMERA...`を経て`MOUNT MANUAL`に移ります。

手動画面では短押しで`RA HI` → `RA LOW` → `DEC HI` → `DEC LOW`の順に選択します。初期選択は`RA HI`です。ノブを回すと、選択した軸の相対移動を要求します。1クリックあたりの要求量はHIが180秒角、LOWが20秒角です。移動処理中は画面上部の`MOVING...`が点滅します。

押し込みを離した時点で、0.8秒未満を短押し、0.8秒以上を長押しとして判定します。手動画面で長押しするとガイド画面に移ります。未キャリブレーション時の表示は`AUTO GUIDE: OFF`です。

#### ガイド画面とキャリブレーション

ノブを回して項目を選択し、短押しで開きます。

| 項目 | 操作 |
|---|---|
| `SET` | カメラとガイド設定。`CAM DEFAULT`、`CAM MANUAL`、`RA MAX PULSE`、`DEC MAX PULSE`、`GUIDE PERIOD`、`EXIT`があります。設定画面に入るとガイド補正を一時停止します。`EXIT`を短押しすると画面だけが戻ります。 |
| `CENTER` | 太陽像と検出中心を確認します。短押しでガイド画面へ戻ります。 |
| `CALIB` | `CAL START`と`BACK`を表示します。ノブで`CAL START`を選び、短押しすると実機キャリブレーションを開始します。 |

開始時は`CALIBRATION`の下に`MEASURING...`が表示されます。計測中は`RA+ 1`や`DEC- 1`のように軸・方向・パルス回数を表示し、その下に測定変位と目標変位を秒角で表示します。実行中も`PRESS: BACK`が表示されますが、処理は同期実行のため、押し込みによる即時中断はできません。危険時は第12章の駆動電源遮断を行ってください。

成功するとガイド画面へ戻ります。公開手順の起動引数では、`CAL OK / GUIDE OFF`の状態で安定した太陽中心から新しい基準点を取得し、ガイドが有効になると`GUIDE ON`に変わります。

失敗時は`CAL FAILED`と`PRESS: BACK`を表示し、ガイド補正を無効にします。短押しで`CAL START` / `BACK`に戻れます。失敗までに行った移動は自動で復帰しません。太陽像、赤道儀の姿勢、ケーブル余裕、ログの失敗理由を確認してから再試行してください。DECの遊び取りは第9.4節の最大24パルスの処理です。

#### ガイド補正の停止とHOME

ガイド画面で長押しして`MOUNT MANUAL`へ移ると、自動補正が一時停止します。この間も太陽時追尾は続きます。有効なキャリブレーションが残っている状態でガイド画面へ戻ると、公開手順の起動引数では基準点を取り直して自動補正を再開します。

追尾も終了する場合は、復帰経路が安全であることを確認して、追尾中に専用のHOMEスイッチを押します。ガイド側を終了し、両軸の停止を確認できた場合にGoTo直前の記録位置へ復帰します。復帰時のOLEDは`GOTO` / `HOME` / `POSITION`です。復帰後は待機条件を再確認し、条件が揃うと`PRESS GOTO` / `TO START`へ戻ります。

## 10. 太陽導入の計算方法

### 10.1 太陽位置の計算

GPSからUTCと観測地点を取得し、UTCをユリウス日へ変換します。USNOの近似式に基づいて太陽の赤経・赤緯を計算し、地方恒星時から太陽の時角を求めます。

### 10.2 ホーム位置からの移動角

太陽の時角・赤緯からRA・DECの赤道儀移動量へ変換する機械座標処理は、実機確認したAstroEQ Solar GoTo実装に従います。その後、RA西方向・DEC南方向の設定引数により軸の符号を変換します。

想定するホーム姿勢は、望遠鏡を天の北極方向へ向け、ウエイト軸を鉛直下にした状態です。この機械的条件は画像キャリブレーションでは置き換えられません。

### 10.3 モーターカウントへの変換

AstroEQから読み取ったRA・DECの1回転当たりカウント数を使って角度を移動カウントへ変換します。GoTo実装はコントローラーの24bitカウンターを使用し、プログラムが用いる符号付き差分で一意に表現できない移動を拒否します。

### 10.4 ホーム位置の管理

太陽GoTo直前にRA・DECカウンターを復帰基準として記録します。HOME操作は追尾を停止し、記録した基準への復帰を要求します。脱調、手動移動、電源断などによりカウンターと実際の赤道儀姿勢の対応が失われた場合、復帰位置は無効になる可能性があります。

## 11. 太陽GoToを利用した極軸の簡易調整

太陽GoToは極軸の簡易調整にも利用できます。規定のホーム姿勢から太陽GoToを行い、安全な太陽フィルターを通した像が中心へ来るように、赤道儀の高度・方位調整機構を操作します。

これは完全な極軸合わせではありません。太陽1点だけでは高度誤差と方位誤差を独立して完全に除去できず、オートガイダーが像のドリフトを補正しても、極軸自体の精度を証明するものではありません。

## 12. 安全上の注意

太陽観測用として設計された機材だけを使用してください。フィルターのない光学系、ファインダー、カメラで太陽を見ないでください。適切な太陽フィルターまたは太陽専用望遠鏡を使用し、電源投入前にフィルター、赤道儀、ケーブル、ストッパーを確認してください。

本ソフトウェアは独立した衝突検出機能を備えていません。最初のGoTo前に、ホーム姿勢、モーター方向、ケーブル余裕、可動範囲、バランス、赤道儀の停止状態を確認し、駆動部へ近づかないでください。

GoTo、HOME、ロータリースイッチはソフトウェア入力であり、確実な緊急停止装置ではありません。ソフトウェア障害やシリアル通信障害により停止指令がAstroEQへ届かない場合があります。モーター電源を直ちに遮断するなど、赤道儀を安全に停止できる独立した手段を確保してください。

### 12.1 光学系と機械系の事前確認

主鏡、ガイド鏡、ファインダーをそれぞれ確認します。対物側フィルターを使用する光学系では、メーカーがその用途に指定した太陽フィルターを前面に確実に固定し、傷、破れ、浮き、脱落のおそれがないことを確認します。太陽専用望遠鏡はメーカー指定の光学構成と手順で使用します。使用しないファインダーは外すか、入光側を確実に遮光してください。

GoTo開始前は第10.2節の機械的ホームに合わせます。鏡筒は天の北極方向、カウンターウエイト軸は鉛直下です。三脚、鏡筒、ウエイトの固定とバランスを確認します。電源・USB・カメラのケーブルは回転軸への巻き付きや挟み込みを避け、GoTo・追尾・HOMEの可動範囲にわたり引っ張られない長さと取り回しにしてください。

### 12.2 初回試験の順序

1. 初回の無動作確認と駆動方向試験は、主鏡、ガイド鏡、ファインダーに危険な太陽光が入らない状態で行います。太陽望遠鏡はNCP方向、赤道儀のウエイト軸は鉛直下向きのホームポジションに設定しておきます。Readyランプが点灯しない場合は、第6.4節のデバイス確認を行います。最初の起動ではGoToを押さず、OLEDで起動状態を確認します。太陽高度などの条件により、待機表示が続くことがあります。
2. 初回の駆動方向確認は太陽から離れた安全な向きで行います。赤道儀メーカーが指定する試験手順で、可動限界から離れた位置で両軸を一軸ずつ短く動かし、RA西方向とDEC南方向の対応を確認します。方向または停止に疑いがある場合は、太陽GoToへ進まず設定と配線を確認します。
3. 光学系を太陽観測用に整え、機械的ホームに戻します。GoTo前に赤道儀の停止、駆動経路、ケーブル余裕、独立した駆動電源遮断手段を確認します。操作者が動作を監視し、遮断手段にすぐ手が届く状態でGoToを開始します。
4. キャリブレーション前に`CENTER`で太陽円盤と検出中心を確認します。円盤が視野内に収まり、中心が安定し、RA・DEC両方向の試験移動を行える機械的・画像上の余裕があることを確認してから`CAL START`を選びます。ガイド補正が始まった後も、実際の動きとケーブルを監視します。

### 12.3 異常の検出と通常停止

安定版は、ガイド中に円盤検出が3秒を超えて途絶えた場合や、補正判定時に画像誤差が既定の35 pxを超えた場合に、自動補正を停止します。補正後の画像誤差が3回連続で改善しない場合はログに警告を出し、補正は継続します。異音、移動方向の異常、ケーブルの張り、視野からの逸脱があれば、その場で動作を確認してください。

自動補正だけを止めるには、第9.6節の操作でガイド画面から手動画面へ移ります。この操作後も太陽時追尾は継続します。通常の運転終了時は、復帰経路が安全であることを確認してから追尾中にHOMEスイッチを押します。HOME操作は先に追尾を停止し、停止を確認できた場合に記録位置への移動を開始します。復帰完了と実際の停止を確認してください。

手動で軸を動かした場合や、脱調、電源断などで記録位置の信頼性が失われた場合は、自動HOMEを避け、機器の手順に従って姿勢を復元してください。

### 12.4 非常時の駆動電源遮断

運転前に、AstroEQと赤道儀の仕様に従い、モーターへの電力供給をその場で切れる外部電源スイッチまたは独立した遮断手段を確認してください。衝突や巻き込みのおそれ、意図しない動作、停止を確認できない状況では、ソフトウェア操作の完了を待たず、指定した遮断手段で駆動電源を切ります。

USB抜去、Piの電源断、`Ctrl+C`、サービス停止だけでは、駆動電源の遮断や赤道儀の停止を確認できません。通信断や異常終了で停止指令の到達を確認できない場合は、駆動電源を遮断して実際に停止したことを確認します。再給電する前に原因を解消し、ケーブルと姿勢を確認して、機械的ホームからやり直してください。

## 13. 現在の制限と今後の課題

### 13.1 公開する安定版の範囲

| 機能 | 安定版の公開状態 |
|---|---|
| GPS位置・UTC取得 | 収録 |
| 太陽GoTo・AstroEQ直接制御 | 収録 |
| 太陽中心検出 | 収録 |
| 画像を使ったRA・DECキャリブレーション | 収録 |
| キャリブレーション時のDEC遊び取り（最大24パルス） | 収録 |
| 速度×時間方式のRA・DECオートガイド | 収録 |
| OLED・ロータリーエンコーダ操作 | 収録 |

他機種GPS、カメラ、赤道儀の機械系、AstroEQ構成との互換性は、この試験だけでは確認していません。絶対位置ホームセンサーと機械的衝突の自動検出は含みません。

### 13.2 実機試験構成

次の写真は実機試験時の構成です。赤道儀、太陽Hα撮影に使用した主望遠鏡、その上部に取り付けた太陽ガイド鏡、写真左下のAstroEQ Solar Auto Guider本体が写っています。

![AstroEQ Solar Auto Guider実機試験構成](images/astroeq_solar_autoguider_test.jpg)

### 13.3 110分間の太陽オートガイド実機試験

Raspberry Pi Zero 2WとAstroEQ Solar Auto Guiderを使用した、スタンドアロン太陽オートガイドの実測試験です。

主望遠鏡と太陽ガイド鏡を同じ赤道儀に搭載しました。AstroEQ Solar Auto Guiderは、ガイド鏡画像から求めた太陽中心位置を利用し、RA・DECをガイド速度×パルス時間方式で制御しました。主望遠鏡で記録したSER画像からも、太陽中心位置の変動を独立に測定しました。

グラフは約110分間のX・Y方向の変動を秒角単位で示します。上段は主望遠鏡のSER画像から計算した平均位置からの位置ズレ、下段はAstroEQ Solar Auto Guiderが使用した太陽ガイド鏡画像から計算した符号付き位置ズレです。両グラフとも青線がX方向、オレンジ線がY方向です。同じオートガイド試験における2つの光学系の位置変動を上下に並べて比較しています。

![110分間の太陽オートガイド実測結果：上段は主望遠鏡、下段はガイド鏡](images/solar_guiding_110min.png)

画像は再描画せず、実測データとして掲載しています。未計算のRMS値、最大誤差、補正率、ガイドなしとの性能比較は示していません。また、この試験は安定版の速度×時間方式オートガイドの試験であり、バックラッシュ量測定・自動補償機能の試験ではありません。

## 14. ライセンス

本プロジェクトの独自Pythonソースコードは、既存の著作権表示を維持してMIT Licenseで公開します。

```text
Copyright (c) 2026 Junichi Watanabe
```

[LICENSE](LICENSE)に著作権表示と許諾文を収録しています。第三者ライセンスの公式出典と用途は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。

### 第三者ソフトウェア・参考プロジェクト

| プロジェクト・ライブラリ | ライセンス・表示 | 本プロジェクトとの関係 |
|---|---|---|
| AstroEQ — Thomas Carpenter | AstroEQ `License.txt`：MIT形式の許諾に加え、変更のオープンソース公開、商業的利益への不使用、元プロジェクトへのリンクという独自条件 | シリアル通信プロトコルを利用。AstroEQファームウェア・公式ソフトウェアは未収録 |
| PHD2 / OpenPHDGuiding | BSD 3-Clause | 別系統で開発した「PHD2方式」の考え方を参照。PHD2ソースは未収録 |
| OpenCV | Apache License 2.0 | 実行時依存ライブラリ |
| NumPy | BSD 3-Clause | 実行時依存ライブラリ |
| Pillow | MIT-CMU（PIL由来） | 実行時依存ライブラリ |
| pySerial | BSD 3-Clause | 実行時依存ライブラリ |
| GPIO Zero | BSD 3-Clause | 実行時依存ライブラリ |
| python-evdev | BSD 3-Clause | 実行時依存ライブラリ |
| luma.oled / luma.core | MIT | 実行時依存ライブラリ |
| lgpio | Unlicense | 実行時依存ライブラリ |
| gpsd / Python GPS interface | gpsdプロジェクトのライセンス。主要構成要素はBSD-2-Clause | 実行時・サービス依存 |

各第三者ソフトウェアには個別のライセンスが適用され、本プロジェクトのMIT Licenseで置き換わるものではありません。AstroEQの成果物自体を再配布・改変する場合は、AstroEQ独自の追加条件も確認し遵守してください。本プロジェクトはAstroEQおよびPHD2/OpenPHDGuidingの非公式・非提携開発です。

## 15. 参考資料

| 資料 | URL |
|---|---|
| AstroEQ | https://github.com/TCWORLD/AstroEQ |
| AstroEQ License | https://github.com/TCWORLD/AstroEQ/blob/master/License.txt |
| PHD2 / OpenPHDGuiding | https://github.com/OpenPHDGuiding/phd2 |
| PHD2 License | https://github.com/OpenPHDGuiding/phd2/blob/master/LICENSE.txt |
| USNO: Computing Approximate Solar Coordinates | https://aa.usno.navy.mil/faq/sun_approx |
| USNO: Computing Approximate Sidereal Time | https://aa.usno.navy.mil/faq/GAST |
| Raspberry Pi Documentation | https://www.raspberrypi.com/documentation/ |
| OpenCV Documentation | https://docs.opencv.org/ |

**---**

本リポジトリでは、Raspberry Pi Zero 2W実機の安定版Pythonソース16本を、キャリブレーションアルゴリズム、AstroEQ通信方式、DEC最大24パルスの遊び取り処理を変更せず公開します。
