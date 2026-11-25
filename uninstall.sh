#!/bin/sh
# Remove everything install.sh put in place. Run with sudo.
# The logo goes back to the card's default (steady, full brightness) at the next boot.
set -eu

systemctl disable --now gpu-logo-schedule.timer gpu-logo-schedule.service \
    gpu-logo-cpu.path gpu-logo-cpu.service 2>/dev/null || true
rm -f /etc/systemd/system/gpu-logo-*
systemctl daemon-reload
rm -f /usr/local/bin/gpu-logo /etc/udev/rules.d/70-gpu-logo-led.rules /etc/modules-load.d/gpu-logo.conf
rm -rf /var/lib/gpu-logo
udevadm control --reload

# Drop live group write access on the LED attributes instead of waiting for reboot.
led=/sys/class/leds/nvidia-logo
if [ -d "$led" ]; then
    for attr in brightness trigger pattern repeat delay_on delay_off invert; do
        if [ -e "$led/$attr" ]; then
            chown root:root "$led/$attr"
            chmod 644 "$led/$attr"
        fi
    done
fi
echo "removed"
