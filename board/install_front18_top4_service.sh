#!/bin/sh
set -eu
APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON=/usr/local/share/pynq-venv/bin/python3
SERVICE=/etc/systemd/system/fpga-vision-front18-top4.service
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo' >&2; exit 1; }
for unit in fpga-vision.service fpga-vision-hints-5mp.service fpga-vision-dual50.service fpga-vision-dual50-copy.service fpga-vision-dual18-pairs.service fpga-vision-ov5640-only.service; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        echo "$unit is active; stop it before installing" >&2
        exit 1
    fi
done
for relative in vivado/camera_5mp_quality.bit vivado/camera_5mp_quality.hwh config/usb_host_mio7.dtbo pynq_app/main_front18_top4.py pynq_app/front18_top4_capture.py pynq_app/front_only_capture.py; do
    [ -s "$APP_DIR/$relative" ] || { echo "Missing $relative" >&2; exit 1; }
done
[ ! -e "$SERVICE" ] || { echo 'Service already exists; inspect before replacing' >&2; exit 1; }
cat > "$SERVICE" <<EOF
[Unit]
Description=Qian OV5640 18 stills plus 4 concurrent USB-top patches
After=network.target bootpy.service
Conflicts=fpga-vision.service fpga-vision-hints-5mp.service fpga-vision-dual50.service fpga-vision-dual50-copy.service fpga-vision-dual18-pairs.service fpga-vision-ov5640-only.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR/pynq_app
Environment=XILINX_XRT=/usr
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStartPre=$PYTHON -u $APP_DIR/pynq_app/usb_host_restore.py --overlay $APP_DIR/config/usb_host_mio7.dtbo
ExecStart=$PYTHON -u $APP_DIR/pynq_app/main_front18_top4.py --host 192.168.2.1 --port 5012 --control-port 5011 --usb-device /dev/video0 --output $APP_DIR/captures_front18_top4
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemd-analyze verify "$SERVICE"
echo 'Installed 18+4 concurrent service; enable/start after static acceptance.'
