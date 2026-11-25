#!/bin/sh
# Remove everything install.sh put in place. Run with sudo.
# The logo goes back to the card's default (steady, full brightness) at the next boot.
set -eu

systemctl disable --now gpu-logo-schedule.timer gpu-logo-schedule.service \
    gpu-logo-cpu.path gpu-logo-cpu.service 2>/dev/null || true
rm -f /etc/systemd/system/gpu-logo-*
systemctl daemon-reload
rm -f /usr/local/bin/gpu-logo /etc/udev/rules.d/70-gpu-logo-led.rules /etc/modules-load.d/gpu-logo.conf
udevadm control --reload
# Wait for any in-flight udev hook to finish before removing state and resetting LED access.
udevadm settle
rm -rf /var/lib/gpu-logo

# Drop live group write access on the LED attributes instead of waiting for reboot.
python3 - <<'PY'
import os
from pathlib import Path

led = Path("/sys/class/leds/nvidia-logo")
if led.is_dir():
    for attr in ("brightness", "trigger", "pattern", "repeat", "delay_on", "delay_off", "invert"):
        path = led / attr
        try:
            os.chown(path, 0, 0)
            os.chmod(path, 0o644)
        except FileNotFoundError:
            continue
PY
echo "removed"
