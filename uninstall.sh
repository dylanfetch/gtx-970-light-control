#!/bin/sh
# Remove everything install.sh put in place. Run with sudo.
# The logo goes back to the card's default (steady, full brightness) at the next boot.
set -eu

systemctl disable --now gpu-logo-schedule.timer gpu-logo-cpu.path gpu-logo-cpu.service 2>/dev/null || true
rm -f /etc/systemd/system/gpu-logo-*
systemctl daemon-reload
rm -f /usr/local/bin/gpu-logo /etc/udev/rules.d/70-gpu-logo-led.rules /etc/modules-load.d/gpu-logo.conf
rm -rf /var/lib/gpu-logo
udevadm control --reload
echo "removed"
