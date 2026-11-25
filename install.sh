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

# Pause existing services while updating units and migrating state.
systemctl stop gpu-logo-schedule.service gpu-logo-cpu.service 2>/dev/null || true

install -m 755 "$here/gpu-logo" /usr/local/bin/gpu-logo
install -m 644 "$here/udev/70-gpu-logo-led.rules" /etc/udev/rules.d/70-gpu-logo-led.rules
install -m 644 "$here/modules-load.d/gpu-logo.conf" /etc/modules-load.d/gpu-logo.conf
install -m 644 "$here"/systemd/gpu-logo-* /etc/systemd/system/

# Provision /var/lib/gpu-logo safely without following symlinks or trusting
# hard-linked legacy control files:
#   /var/lib/gpu-logo           0750 root:$group  (immutable to group members)
#   /var/lib/gpu-logo/.root-tmp 0700 root:root    (privileged atomic write staging)
#   /var/lib/gpu-logo/.lock     0640 root:$group  (shared writer lock)
#   /var/lib/gpu-logo/.cpu.lock 0640 root:$group  (cpu-daemon liveness lock)
#   /var/lib/gpu-logo/cpu.wake  0660 root:$group  (watched by gpu-logo-cpu.path)
#   /var/lib/gpu-logo/state     2770 root:$group  (group-writable JSON state)
python3 - "$group" <<'PY'
import fcntl
import grp
import os
import secrets
import stat
import sys
from pathlib import Path

group = sys.argv[1]
gid = grp.getgrnam(group).gr_gid
base = Path("/var/lib/gpu-logo")
max_json = 16 * 1024

try:
    os.mkdir(base, 0o750)
except FileExistsError:
    pass
base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    orig_st = os.fstat(base_fd)
    legacy_writable = orig_st.st_uid != 0 or bool(orig_st.st_mode & 0o022)
    # Remove group write on the top-level directory FIRST so group members
    # cannot swap any control entries inside it during upgrade or reinstall.
    os.fchown(base_fd, 0, gid)
    os.fchmod(base_fd, 0o750)

    def ensure_dir(name: str, owner_gid: int, mode: int, require_root: bool = False) -> int:
        try:
            st = os.lstat(name, dir_fd=base_fd)
            if not stat.S_ISDIR(st.st_mode):
                os.unlink(name, dir_fd=base_fd)
                os.mkdir(name, mode, dir_fd=base_fd)
            elif require_root and st.st_uid != 0:
                raise SystemExit(f"install.sh: unsafe non-root directory at {base / name}")
        except FileNotFoundError:
            os.mkdir(name, mode, dir_fd=base_fd)
        dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
        os.fchown(dfd, 0, owner_gid)
        os.fchmod(dfd, mode)
        return dfd

    def write_fresh(dst_dir_fd: int, stage_dir_fd: int, name: str, data: bytes,
                    owner_gid: int, mode: int) -> tuple[int, int]:
        while True:
            tmp = f".{name}.{secrets.token_hex(8)}"
            try:
                wfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stage_dir_fd)
                break
            except FileExistsError:
                continue
        try:
            with os.fdopen(wfd, "wb") as f:
                wfd = -1
                if data:
                    f.write(data)
                    f.flush()
                os.fchown(f.fileno(), 0, owner_gid)
                os.fchmod(f.fileno(), mode)
                st = os.fstat(f.fileno())
            os.replace(tmp, name, src_dir_fd=stage_dir_fd, dst_dir_fd=dst_dir_fd)
            return st.st_dev, st.st_ino
        except BaseException:
            if wfd >= 0:
                os.close(wfd)
            try:
                os.unlink(tmp, dir_fd=stage_dir_fd)
            except OSError:
                pass
            raise

    def ensure_control_file(name: str, stage_dir_fd: int, owner_gid: int, mode: int,
                            seen_inodes: set[tuple[int, int]]) -> None:
        # Preserve an existing control inode only on reinstalls from a hardened
        # root-owned base, and only when it is a root-owned unlinked-unique
        # regular file (nlink == 1) distinct from all other control files.
        if not legacy_writable:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=base_fd)
            except FileNotFoundError:
                fd = -1
            except OSError:
                fd = -1
            if fd >= 0:
                try:
                    st = os.fstat(fd)
                    key = (st.st_dev, st.st_ino)
                    if stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1 and key not in seen_inodes:
                        os.fchown(fd, 0, owner_gid)
                        os.fchmod(fd, mode)
                        seen_inodes.add(key)
                        return
                finally:
                    os.close(fd)
        try:
            st = os.lstat(name, dir_fd=base_fd)
            if stat.S_ISDIR(st.st_mode):
                raise SystemExit(f"install.sh: unexpected directory at {base / name}")
        except FileNotFoundError:
            pass
        key = write_fresh(base_fd, stage_dir_fd, name, b"", owner_gid, mode)
        seen_inodes.add(key)

    def read_regular(dir_fd: int, name: str) -> bytes | None:
        try:
            rfd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(rfd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > max_json:
                raise SystemExit(f"install.sh: unsafe or oversized state file '{name}'")
            with os.fdopen(rfd, "rb") as f:
                rfd = -1
                data = f.read(max_json + 1)
            if len(data) > max_json:
                raise SystemExit(f"install.sh: oversized state file '{name}'")
            return data
        finally:
            if rfd >= 0:
                os.close(rfd)

    stage_fd = ensure_dir(".root-tmp", 0, 0o700, require_root=True)
    try:
        seen_inodes: set[tuple[int, int]] = set()
        ensure_control_file(".lock", stage_fd, gid, 0o640, seen_inodes)
        ensure_control_file(".cpu.lock", stage_fd, gid, 0o640, seen_inodes)
        ensure_control_file("cpu.wake", stage_fd, gid, 0o660, seen_inodes)

        # Hold the verified writer lock while migrating and re-staging state files
        # so a concurrent CLI invocation cannot have its save overwritten.
        lock_fd = os.open(".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=base_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            state_fd = ensure_dir("state", gid, 0o2770)
            try:
                # Migrate legacy top-level JSON files into state/ into fresh root:$group inodes.
                for name in ("state.json", "schedule.json", "applied.json"):
                    try:
                        data = read_regular(base_fd, name)
                    except OSError:
                        os.unlink(name, dir_fd=base_fd)
                        continue
                    if data is not None:
                        try:
                            os.lstat(name, dir_fd=state_fd)
                        except FileNotFoundError:
                            write_fresh(state_fd, stage_fd, name, data, gid, 0o660)
                        os.unlink(name, dir_fd=base_fd)

                # Clean up legacy cpu.active marker if present at top level.
                try:
                    st = os.lstat("cpu.active", dir_fd=base_fd)
                    if not stat.S_ISDIR(st.st_mode):
                        os.unlink("cpu.active", dir_fd=base_fd)
                except FileNotFoundError:
                    pass

                # Re-stage existing regular state files under the current group so a
                # GROUP change on reinstall revokes access from the previous group.
                for name in ("state.json", "schedule.json", "applied.json"):
                    try:
                        data = read_regular(state_fd, name)
                    except OSError as e:
                        raise SystemExit(f"install.sh: unsafe state file {base / 'state' / name}: {e.strerror}") from e
                    if data is not None:
                        write_fresh(state_fd, stage_fd, name, data, gid, 0o660)
            finally:
                os.close(state_fd)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    finally:
        os.close(stage_fd)
finally:
    os.close(base_fd)
PY

udevadm control --reload
# "change" fixes permissions now without re-applying any saved mode
udevadm trigger --action=change --subsystem-match=leds --sysname-match=nvidia-logo
udevadm settle
systemctl daemon-reload
systemctl enable --now gpu-logo-schedule.timer gpu-logo-cpu.path
systemctl enable gpu-logo-cpu.service
systemctl restart gpu-logo-cpu.service
echo "installed (group: $group): try 'gpu-logo status'"
