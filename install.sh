#!/bin/sh
# Install gpu-logo system-wide. Run with sudo. Safe to re-run.
set -eu
here=$(cd "$(dirname "$0")" && pwd)

install -m 755 "$here/gpu-logo" /usr/local/bin/gpu-logo
install -m 644 "$here/udev/70-gpu-logo-led.rules" /etc/udev/rules.d/70-gpu-logo-led.rules
install -m 644 "$here/modules-load.d/gpu-logo.conf" /etc/modules-load.d/gpu-logo.conf
# setgid + group-writable so members of plugdev can save the mode for next boot
install -d -m 2775 -g plugdev /var/lib/gpu-logo

modprobe -a ledtrig-pattern ledtrig-timer ledtrig-heartbeat ledtrig-activity
udevadm control --reload
# "change" fixes permissions now without re-applying any saved mode
udevadm trigger --action=change --subsystem-match=leds --sysname-match=nvidia-logo
udevadm settle
echo "installed: try 'gpu-logo status'"
