#!/bin/sh
# Install gpu-logo system-wide. Run with sudo. Safe to re-run.
set -eu
here=$(cd "$(dirname "$0")" && pwd)

# Check prerequisites before copying any files.
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "install.sh: Python 3.10 or newer is required" >&2
    exit 1
}
group=$(sed -n 's/^GROUP = "\([^"]*\)"$/\1/p' "$here/gpu-logo")
if [ -z "$group" ]; then
    echo "install.sh: could not read GROUP from $here/gpu-logo" >&2
    exit 1
fi
if ! getent group "$group" >/dev/null 2>&1; then
    echo "install.sh: group '$group' does not exist; create it or change GROUP in gpu-logo" >&2
    exit 1
fi
modprobe -a ledtrig-pattern ledtrig-timer

install -m 755 "$here/gpu-logo" /usr/local/bin/gpu-logo
install -m 644 "$here/udev/70-gpu-logo-led.rules" /etc/udev/rules.d/70-gpu-logo-led.rules
install -m 644 "$here/modules-load.d/gpu-logo.conf" /etc/modules-load.d/gpu-logo.conf
install -m 644 "$here"/systemd/gpu-logo-* /etc/systemd/system/
# setgid + group-writable so members of $group can save the mode and schedule.
install -d -m 2775 -g "$group" /var/lib/gpu-logo
# Root-only staging directory for atomic writes by privileged hooks/services.
install -d -m 700 -o root -g root /var/lib/gpu-logo/.root-tmp
if [ ! -e /var/lib/gpu-logo/.lock ]; then
    install -m 664 -g "$group" /dev/null /var/lib/gpu-logo/.lock
fi

udevadm control --reload
# "change" fixes permissions now without re-applying any saved mode
udevadm trigger --action=change --subsystem-match=leds --sysname-match=nvidia-logo
udevadm settle
systemctl daemon-reload
systemctl enable --now gpu-logo-schedule.timer gpu-logo-cpu.path
systemctl enable gpu-logo-cpu.service
systemctl restart gpu-logo-cpu.service
echo "installed (group: $group): try 'gpu-logo status'"
