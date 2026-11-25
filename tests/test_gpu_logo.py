"""Tests for the parts of gpu-logo (and observe.py) that do not need real hardware.

Run from the repository root:  python3 -m unittest discover tests
"""
import fcntl
import importlib.machinery
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent


def load_tool(led_dir: Path, state_dir: Path):
    """Import the gpu-logo script (it has no .py suffix) pointed at fake directories."""
    os.environ["GPU_LOGO_LED"] = str(led_dir)
    os.environ["GPU_LOGO_STATE_DIR"] = str(state_dir)
    loader = importlib.machinery.SourceFileLoader("gpu_logo", str(REPO / "gpu-logo"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("gpu_logo", loader))
    loader.exec_module(module)
    return module


def load_observe():
    spec = importlib.util.spec_from_file_location("observe", REPO / "tools" / "observe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pattern_duration_ms(pattern: str) -> int:
    """Real cycle duration under Linux's ledtrig-pattern timing rules.

    Adjacent tuples with different brightness levels spend one extra 50 ms redraw;
    adjacent tuples at the same level hold for exactly the written duration.
    """
    values = [int(v) for v in pattern.split()]
    levels, times = values[::2], values[1::2]
    n = len(levels)
    return sum(times[i] + (50 if levels[i] != levels[(i + 1) % n] else 0) for i in range(n))


class GpuLogoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.led = Path(tmp.name) / "led"
        self.state = Path(tmp.name) / "state-root"
        self.data = self.state / "state"
        self.led.mkdir()
        self.state.mkdir()
        self.data.mkdir()
        (self.state / ".lock").write_text("")
        (self.state / ".cpu.lock").write_text("")
        (self.state / "cpu.wake").write_text("")
        (self.led / "max_brightness").write_text("255\n")
        (self.led / "brightness").write_text("255\n")
        (self.led / "trigger").write_text("none\n")
        self.g = load_tool(self.led, self.state)
        self.g.SYNC_WAIT_S = 0  # a fake LED never reaches its resting level
        self.g._wait_path_unit_armed = lambda timeout=2.0, require_active=True: True  # never query host systemd in unit tests
        self.available_triggers = ["none", "timer", "pattern", "heartbeat", "activity", "disk-activity"]
        # A plain file cannot list the available triggers the way sysfs does.
        self.g.read_trigger = lambda: (self.attr("trigger"), list(self.available_triggers))

    def attr(self, name: str) -> str:
        return (self.led / name).read_text().strip()


class ScheduleTest(GpuLogoTest):
    def schedule(self, dim="23:30-02:00", brighten="08:30-09:00", level=10, tz=None):
        (ds, de), (bs, be) = self.g.parse_span(dim), self.g.parse_span(brighten)
        raw = {"dim_start": ds, "dim_end": de, "bright_start": bs, "bright_end": be, "level": level}
        if tz is not None:
            raw["tz"] = tz
        return self.g.normalize_schedule(raw)

    def at(self, schedule, clock):
        return self.g.dim_at(schedule, self.g.parse_clock(clock))

    def test_slow_evening_fade_and_quick_morning_one(self):
        s = self.schedule()
        self.assertEqual(self.at(s, "23:29"), 1.0)
        self.assertEqual(self.at(s, "23:30"), 1.0)  # fade only just starting
        self.assertTrue(0.1 < self.at(s, "00:45") < 1.0)  # half way down, across midnight
        self.assertTrue(0.1 < self.at(s, "01:59") < 0.2)  # nearly there, not there yet
        self.assertAlmostEqual(self.at(s, "02:00"), 0.1)
        self.assertAlmostEqual(self.at(s, "08:30"), 0.1)  # brightening only just starting
        self.assertTrue(0.1 < self.at(s, "08:45") < 1.0)
        self.assertEqual(self.at(s, "09:00"), 1.0)
        self.assertEqual(self.at(s, "15:00"), 1.0)

    def test_fades_move_steadily_in_the_right_direction(self):
        s = self.schedule()
        down = [self.g.dim_at(s, 23 * 60 + 30 + m) for m in range(151)]
        up = [self.g.dim_at(s, 8 * 60 + 30 + m) for m in range(31)]
        self.assertEqual(down, sorted(down, reverse=True))
        self.assertEqual(up, sorted(up))

    def test_single_times_switch_at_once(self):
        s = self.schedule(dim="01:00", brighten="05:00", level=0)
        self.assertEqual(self.at(s, "00:59"), 1.0)
        self.assertEqual(self.at(s, "01:00"), 0.0)
        self.assertEqual(self.at(s, "04:59"), 0.0)
        self.assertEqual(self.at(s, "05:00"), 1.0)

    def test_dst_transitions_follow_wall_clock(self):
        ny = ZoneInfo("America/New_York")
        # Fall-back night: 2026-11-01 repeats 01:00-01:59.
        s = self.schedule(dim="23:00", brighten="01:30-02:30", level=10, tz="America/New_York")
        before_fallback = datetime(2026, 11, 1, 1, 59, tzinfo=ny, fold=0)
        after_fallback = datetime(2026, 11, 1, 1, 0, tzinfo=ny, fold=1)
        self.assertGreater(self.g.current_dim(s, before_fallback), 0.1)
        self.assertEqual(self.g.current_dim(s, after_fallback), 0.1)

        # Spring-forward night: 2026-03-08 skips 02:00-02:59.
        s2 = self.schedule(dim="23:00", brighten="02:00-03:00", level=10, tz="America/New_York")
        before_spring = datetime(2026, 3, 8, 1, 59, tzinfo=ny)
        after_spring = datetime(2026, 3, 8, 3, 0, tzinfo=ny)
        self.assertEqual(self.g.current_dim(s2, before_spring), 0.1)
        self.assertEqual(self.g.current_dim(s2, after_spring), 1.0)

    def test_rejects_bad_schedules(self):
        for dim, brighten, level in [
            ("22:00", "22:00", 10),              # never dimmed
            ("23:30-02:00", "01:00-09:00", 10),  # brightening starts mid-dim
            ("23:00-01:00", "01:00-23:30", 10),  # the two fades overlap round the clock
            ("22:00", "07:00", 101),
            ("23:30-", "08:00", 10),             # trailing dash without end time
            ("-02:00", "08:00", 10),
        ]:
            with self.assertRaises(self.g.Fail, msg=(dim, brighten)):
                self.schedule(dim, brighten, level)
        for text in ["24:00", "7", "07:60", "seven"]:
            with self.assertRaises(self.g.Fail):
                self.g.parse_clock(text)

    def test_timezone_is_kept_only_if_real(self):
        base = {"dim_start": 0, "dim_end": 0, "bright_start": 60, "bright_end": 60, "level": 10}
        self.assertEqual(self.g.normalize_schedule(base | {"tz": "America/New_York"})["tz"], "America/New_York")
        self.assertNotIn("tz", self.g.normalize_schedule(base))
        for bad in ["Mars/Olympus", "../../etc/passwd", 5]:
            with self.assertRaises(self.g.Fail):
                self.g.normalize_schedule(base | {"tz": bad})


class NormalizeTest(GpuLogoTest):
    def test_rejects_anything_unexpected(self):
        for bad in [
            {"mode": "static", "level": 101},
            {"mode": "static", "level": True},
            {"mode": "blink", "on_ms": "5; reboot"},
            {"mode": "breathe", "min": 50, "max": 50},
            {"mode": "pattern"},
            ["static"],
            None,
        ]:
            with self.assertRaises(self.g.Fail):
                self.g.normalize(bad)

    def test_drops_unknown_keys(self):
        self.assertEqual(self.g.normalize({"mode": "disk", "extra": "x"}), {"mode": "disk"})

    def test_cli_rejects_non_finite_and_overflowing_breathe_period(self):
        for bad in ("nan", "inf", "-inf", "1e308", "0.1", "601"):
            res = subprocess.run(
                [sys.executable, str(REPO / "gpu-logo"), "breathe", f"--period={bad}"],
                env=os.environ | {"GPU_LOGO_LED": str(self.led), "GPU_LOGO_STATE_DIR": str(self.state)},
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(res.returncode, 0, bad)
            self.assertIn("period must be between", res.stderr)
            self.assertNotIn("Traceback", res.stderr)


class ApplyTest(GpuLogoTest):
    def test_static_is_scaled_by_dim(self):
        self.g.apply({"mode": "static", "level": 50}, dim=0.1)
        self.assertEqual(self.attr("brightness"), "13")
        self.assertEqual(self.attr("trigger"), "none")

    def test_dimming_never_rounds_a_lit_logo_to_off(self):
        self.g.apply({"mode": "static", "level": 1}, dim=0.05)
        self.assertEqual(self.attr("brightness"), "1")

    def test_dim_zero_turns_every_mode_off(self):
        for mode in [{"mode": "cpu"}, {"mode": "disk"}, {"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100},
                     {"mode": "blink", "on_ms": 500, "off_ms": 500, "level": 100}]:
            self.g.apply(mode, dim=0.0)
            self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"), mode)

    def test_breathe_peak_is_scaled_by_dim_and_keeps_period(self):
        for dim, expected_peak in [(1.0, 255), (0.2, 51), (0.1, 26)]:
            self.g.apply({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100}, dim=dim)
            pat = self.attr("pattern")
            levels = [int(v) for v in pat.split()[::2]]
            self.assertEqual((self.attr("trigger"), min(levels), max(levels)), ("pattern", 0, expected_peak))
            self.assertEqual(pattern_duration_ms(pat), 4000, f"dim={dim}")

    def test_disk_mode_sets_its_flash_level(self):
        self.g.apply({"mode": "disk"}, dim=0.5)
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("disk-activity", "128"))

    def test_missing_trigger_leaves_current_effect_intact(self):
        self.g.apply({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100})
        before_pattern = self.attr("pattern")
        self.available_triggers.remove("disk-activity")
        with self.assertRaises(self.g.Fail):
            self.g.apply({"mode": "disk"})
        self.assertEqual(self.attr("trigger"), "pattern")
        self.assertEqual(self.attr("pattern"), before_pattern)

    def test_changing_a_running_pattern_keeps_the_trigger(self):
        writes = []
        real = self.g.write_attr
        self.g.write_attr = lambda name, value, wait=0.0: (writes.append(name), real(name, value, wait))[1]
        self.g.apply({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100})
        del writes[:]
        self.g.apply({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100}, dim=0.5)
        self.assertEqual(writes, ["pattern"])  # resetting the trigger would blank the logo


class HeartbeatTest(GpuLogoTest):
    def shape(self, effort, dim=1.0):
        """(levels, real duration in ms) of the beat at this effort."""
        pat = self.g.heartbeat_pattern(effort, dim)
        levels = [int(v) for v in pat.split()[::2]]
        return levels, pattern_duration_ms(pat)

    def test_calm_is_slow_and_soft_and_flat_out_is_fast_and_strong(self):
        calm, calm_ms = self.shape(0.0)
        hard, hard_ms = self.shape(1.0)
        self.assertEqual((calm_ms, hard_ms), (3000, 1000))  # 20 and 60 beats a minute
        self.assertEqual(max(hard), 255)
        self.assertTrue(max(calm) < 60)
        self.assertTrue(min(calm) > 0)  # a glow remains between beats

    def test_low_dim_heartbeat_preserves_duration_when_levels_collide(self):
        # At dim=0.08 calm, peak=3, base=1, so dip == dub == 2.
        _, ms = self.shape(0.0, dim=0.08)
        self.assertEqual(ms, 3000)

    def test_beat_returns_to_its_resting_level_only_at_rest(self):
        for effort in [0.0, 0.5, 1.0]:
            levels, _ = self.shape(effort)
            base, peak, dip, dub, rest = levels
            self.assertEqual(base, rest)
            self.assertTrue(base + 1 < dip < dub < peak, levels)

    def test_no_pattern_when_dimmed_to_nothing(self):
        self.assertIsNone(self.g.heartbeat_pattern(0.5, dim=0.0))

    def test_steps_hold_near_a_boundary(self):
        steps = self.g.CPU_STEPS
        self.assertEqual(self.g.next_step(0.0, None), 0)
        self.assertEqual(self.g.next_step(1.0, None), steps)
        self.assertEqual(self.g.next_step(3.6 / steps, 3), 3)   # not far enough to move
        self.assertEqual(self.g.next_step(3.9 / steps, 3), 4)


class SecurityAndStateTest(GpuLogoTest):
    def test_read_json_rejects_symlinks_fifos_oversized_and_deep_nesting(self):
        target = self.data / "real.json"
        target.write_text('{"mode": "disk"}\n')
        link = self.data / "link.json"
        link.symlink_to(target)
        with self.assertRaises(self.g.Fail):
            self.g.read_json(link)

        fifo = self.data / "fifo.json"
        os.mkfifo(fifo)
        with self.assertRaises(self.g.Fail):
            self.g.read_json(fifo)

        big = self.data / "big.json"
        big.write_text(" " * (self.g.MAX_JSON_BYTES + 10))
        with self.assertRaises(self.g.Fail):
            self.g.read_json(big)

        deep = self.data / "deep.json"
        deep.write_text('{"mode": "disk"}\n')
        real_loads = self.g.json.loads
        self.g.json.loads = lambda _s: (_ for _ in ()).throw(RecursionError("deep"))
        try:
            with self.assertRaises(self.g.Fail):
                self.g.read_json(deep)
        finally:
            self.g.json.loads = real_loads

    def test_write_json_replaces_symlink_without_touching_target(self):
        victim = self.data / "victim.txt"
        victim.write_text("untouched")
        victim.chmod(0o600)
        link = self.data / "state.json"
        link.symlink_to(victim)

        self.g.write_json(link, {"mode": "disk"})
        self.assertFalse(link.is_symlink())
        self.assertEqual(stat.S_IMODE(link.stat().st_mode), 0o660)
        self.assertEqual(victim.read_text(), "untouched")
        self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o600)

    def test_load_applied_rejects_out_of_range_non_finite_and_overflow_dim(self):
        huge_int = "1" + "0" * 400
        for raw_dim in ("-0.1", "1.1", "NaN", "Infinity", "true", '"0.5"', huge_int):
            (self.data / "applied.json").write_text(
                f'{{"boot": "{self.g.boot_id()}", "state": {{"mode": "cpu"}}, "dim": {raw_dim}}}\n'
            )
            self.assertIsNone(self.g.load_applied(), raw_dim)

    def test_state_lock_rejects_symlink_fails_closed_and_times_out_when_contended(self):
        self.g.LOCK_FILE.unlink()
        other = self.state / "other"
        other.write_text("")
        self.g.LOCK_FILE.symlink_to(other)
        with self.assertRaises(self.g.Fail):
            with self.g.state_lock(timeout=0.05):
                pass
        self.g.LOCK_FILE.unlink()

        self.g.LOCK_FILE.write_text("")
        with open(self.g.LOCK_FILE, "rb") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            try:
                with self.assertRaises(self.g.Fail):
                    with self.g.state_lock(timeout=0.05):
                        pass
            finally:
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    def test_wake_cpu_daemon_surfaces_invalid_or_missing_wake_file(self):
        self.g.CPU_WAKE_FILE.unlink()
        with self.assertRaises(self.g.Fail):
            self.g.wake_cpu_daemon()
        self.g.CPU_WAKE_FILE.mkdir()
        with self.assertRaises(self.g.Fail):
            self.g.wake_cpu_daemon()

    def test_wait_path_unit_armed_surfaces_inactive_failed_and_timeout(self):
        # Load a fresh module copy with real _wait_path_unit_armed intact.
        g = load_tool(self.led, self.state)
        orig_which = g.shutil.which
        orig_run = g.subprocess.run
        g.shutil.which = lambda cmd: "/bin/systemctl" if cmd == "systemctl" else orig_which(cmd)
        try:
            class FakeRes:
                def __init__(self, stdout, rc=0):
                    self.stdout = stdout
                    self.returncode = rc

            g.subprocess.run = lambda *a, **kw: FakeRes("loaded\nfailed\ndead\n")
            with self.assertRaises(g.Fail):
                g._wait_path_unit_armed(timeout=0.05)

            g.subprocess.run = lambda *a, **kw: FakeRes("loaded\nactive\nrunning\n")
            with self.assertRaises(g.Fail):
                g._wait_path_unit_armed(timeout=0.05)

            g.subprocess.run = lambda *a, **kw: FakeRes("loaded\nactive\nwaiting\n")
            self.assertTrue(g._wait_path_unit_armed(timeout=0.05))
        finally:
            g.shutil.which = orig_which
            g.subprocess.run = orig_run

    def test_failed_applied_write_invalidates_stale_cpu_state_and_blocks_daemon_undo(self):
        self.g.show({"mode": "cpu"}, 1.0)
        seen = self.g._applied_stamp()

        def enospc(*_args, **_kwargs):
            raise OSError(28, "No space left on device")

        real_write_json = self.g.write_json
        self.g.write_json = enospc
        try:
            with self.assertRaises(self.g.Fail):
                self.g.show({"mode": "static", "level": 0}, 1.0)
        finally:
            self.g.write_json = real_write_json

        # LED is off and stale cpu applied.json was invalidated so daemon cannot undo off.
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"))
        self.assertIsNone(self.g.load_applied())
        keep, _, _ = self.g._cpu_daemon_step(seen, 0, 1.0, 4)
        # Stamp changed because applied.json was unlinked; re-checking sees None and exits.
        self.assertTrue(keep)
        keep2, _, _ = self.g._cpu_daemon_step(self.g._applied_stamp(), 0, 1.0, 4)
        self.assertFalse(keep2)
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"))

    def test_udev_hook_tolerates_disappearing_trigger_attribute(self):
        pattern_attr = self.led / "pattern"
        pattern_attr.write_text("0 100\n")
        real_chown = self.g.shutil.chown
        seen = []

        def flaky_chown(path, group=None):
            seen.append(Path(path).name)
            if Path(path).name == "pattern":
                pattern_attr.unlink()
                raise FileNotFoundError(path)
            return None

        self.g.shutil.chown = flaky_chown
        try:
            os.environ["ACTION"] = "change"
            self.g.udev_hook()
        finally:
            self.g.shutil.chown = real_chown
            os.environ.pop("ACTION", None)
        self.assertIn("brightness", seen)
        self.assertIn("pattern", seen)


class TickAndDaemonTest(GpuLogoTest):
    """The schedule follows whatever is showing, and coordinates cleanly with cpu-daemon."""

    def set_schedule(self, level):
        self.g.write_json(self.g.SCHEDULE_FILE, {"dim_start": 0, "dim_end": 0, "bright_start": 720,
                                                 "bright_end": 720, "level": level})

    def test_tick_dims_the_showing_mode_once(self):
        self.g.current_dim = lambda schedule, now=None: 0.25 if schedule else 1.0
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.set_schedule(25)
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "51")
        (self.led / "brightness").write_text("7\n")  # a second tick must not touch the LED
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "7")

    def test_tick_leaves_a_running_heartbeat_to_the_daemon(self):
        self.g.current_dim = lambda schedule, now=None: 0.25 if schedule else 1.0
        self.g.show({"mode": "cpu"}, 1.0)
        before = self.attr("pattern")
        self.set_schedule(25)
        self.g.tick()
        self.assertEqual(self.attr("pattern"), before)
        self.assertEqual(self.g.load_applied(), ({"mode": "cpu"}, 0.25))

    def test_tick_turns_off_running_heartbeat_at_dim_zero_and_restores_it(self):
        dim_value = [0.0]
        self.g.current_dim = lambda schedule, now=None: dim_value[0] if schedule else 1.0
        self.g.show({"mode": "cpu"}, 1.0)
        self.assertEqual(self.attr("trigger"), "pattern")
        self.set_schedule(0)

        # Evening: dim reaches 0.0 -> LED must actually turn off.
        self.g.tick()
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"))
        self.assertEqual(self.g.load_applied(), ({"mode": "cpu"}, 0.0))

        # Morning: dim rises back to 0.5 -> LED must resume heartbeat pattern.
        dim_value[0] = 0.5
        self.g.tick()
        self.assertEqual(self.attr("trigger"), "pattern")
        self.assertEqual(self.g.load_applied(), ({"mode": "cpu"}, 0.5))

    def test_tick_does_not_overwrite_newer_cli_off_during_pattern_wait(self):
        self.g.current_dim = lambda schedule, now=None: 0.25 if schedule else 1.0
        self.g.show({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100}, 1.0)
        self.set_schedule(25)

        def cli_turns_off_mid_wait(still_wanted=lambda: True):
            self.g.show({"mode": "static", "level": 0}, 0.25)

        self.g.wait_for_pattern_rest = cli_turns_off_mid_wait
        self.g.tick()
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"))
        self.assertEqual(self.g.load_applied(), ({"mode": "static", "level": 0}, 0.25))

    def test_state_lock_serializes_overlapping_tick_and_cli_write(self):
        """A second writer must block until tick's locked transaction finishes.

        This fails immediately if state_lock is removed or replaced with a no-op.
        """
        self.g.current_dim = lambda schedule, now=None: 0.25 if schedule else 1.0
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.set_schedule(25)

        tick_in_apply = threading.Event()
        release_tick = threading.Event()
        cli_in_apply = threading.Event()
        real_apply = self.g.apply

        def gated_apply(state, dim=1.0, wait_rest=True):
            if state["level"] == 80:
                tick_in_apply.set()
                release_tick.wait(timeout=2.0)
            else:
                cli_in_apply.set()
            return real_apply(state, dim, wait_rest=wait_rest)

        self.g.apply = gated_apply
        errors = []

        def run(fn):
            try:
                fn()
            except BaseException as e:
                errors.append(e)

        t_tick = threading.Thread(target=run, args=(self.g.tick,))
        t_tick.start()
        self.assertTrue(tick_in_apply.wait(timeout=2.0))

        t_cli = threading.Thread(target=run, args=(lambda: self.g.show({"mode": "static", "level": 0}, 0.25),))
        t_cli.start()
        # While tick holds state_lock inside apply(), CLI must not enter apply().
        self.assertFalse(cli_in_apply.wait(timeout=0.1))

        release_tick.set()
        t_tick.join(timeout=2.0)
        t_cli.join(timeout=2.0)
        self.assertEqual(errors, [])
        self.assertTrue(cli_in_apply.is_set())
        # CLI ran strictly after tick completed, so off wins.
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"))
        self.assertEqual(self.g.load_applied(), ({"mode": "static", "level": 0}, 0.25))

    def test_cpu_daemon_step_does_not_overwrite_newer_breathe_selection(self):
        self.g.show({"mode": "cpu"}, 1.0)
        seen = self.g._applied_stamp()
        breathe = {"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100}
        orig_wait = self.g.wait_for_pattern_rest

        def cli_switches_to_breathe(still_wanted=lambda: True):
            self.g.wait_for_pattern_rest = orig_wait
            self.g.show(breathe, 1.0)

        self.g.wait_for_pattern_rest = cli_switches_to_breathe
        keep, step, changed = self.g._cpu_daemon_step(seen, 0, 1.0, 5)
        self.assertTrue(keep)
        self.assertTrue(changed)
        self.assertEqual(step, 0)
        self.assertEqual(self.attr("pattern"), self.g.breathe_pattern(4000, 0, 255))

    def test_cpu_daemon_step_turns_off_at_dim_zero_and_restarts_pattern(self):
        self.g.show({"mode": "cpu"}, 1.0)
        self.g.record_applied({"mode": "cpu"}, 0.0)
        seen = self.g._applied_stamp()
        keep, step, changed = self.g._cpu_daemon_step(seen, None, 0.0, 4)
        self.assertTrue(keep)
        self.assertFalse(changed)
        self.assertEqual(step, 4)
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"))

        # Low dim where calm is steady (no beat) but full load beats again.
        self.g.record_applied({"mode": "cpu"}, 0.02)
        seen = self.g._applied_stamp()
        keep, step, changed = self.g._cpu_daemon_step(seen, None, 0.02, self.g.CPU_STEPS)
        self.assertTrue(keep)
        self.assertEqual(self.attr("trigger"), "pattern")
        self.assertEqual(self.attr("pattern"), self.g.heartbeat_pattern(1.0, 0.02))

    def test_overlapping_cpu_selection_during_daemon_failure_preserves_recovery(self):
        woke = [0]
        orig_wake = self.g.wake_cpu_daemon
        self.g.wake_cpu_daemon = lambda require_path_active=True: (
            woke.__setitem__(0, woke[0] + 1),
            orig_wake(require_path_active=require_path_active),
        )[1]

        self.g.show({"mode": "cpu"}, 1.0)
        self.assertEqual(woke[0], 1)

        # Simulate a newer CLI `gpu-logo cpu` committing *while* the old daemon holds
        # .cpu.lock, immediately before the old daemon fails through `main()`.
        old_argv = sys.argv

        def select_cpu_then_fail():
            self.assertTrue(self.g._daemon_lock_held())
            sys.argv = ["gpu-logo", "--no-save", "cpu"]
            self.g.main()
            raise self.g.Fail("injected failure")

        self.g.cpu_times = select_cpu_then_fail
        sys.argv = ["gpu-logo", "cpu-daemon"]
        try:
            with self.assertRaises(SystemExit) as cm:
                self.g.main()
            self.assertIn("injected failure", str(cm.exception))
        finally:
            sys.argv = old_argv

        # The failed daemon exited non-zero (so systemd Restart=on-failure restarts it),
        # released .cpu.lock cleanly, and left applied=cpu and cpu.wake intact.
        self.assertFalse(self.g._daemon_lock_held())
        self.assertEqual(self.g.load_applied(), ({"mode": "cpu"}, 1.0))
        self.assertTrue(self.g.CPU_WAKE_FILE.is_file())
        unit_text = (REPO / "systemd" / "gpu-logo-cpu.service").read_text()
        self.assertIn("Restart=on-failure", unit_text)

        # A restarted daemon can acquire .cpu.lock immediately.
        restarted_fd = self.g._acquire_daemon_lock()
        self.assertGreaterEqual(restarted_fd, 0)
        self.g._release_fd(restarted_fd)

    def test_tick_without_a_schedule_does_nothing(self):
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "255")

    def test_record_from_an_earlier_boot_is_ignored(self):
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.g.boot_id = lambda: "a-later-boot"
        self.assertIsNone(self.g.load_applied())


class ObserveTest(unittest.TestCase):
    def setUp(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed")

    def test_measure_checks_frame_bounds_and_counts_lit_pixels(self):
        from PIL import Image

        obs = load_observe()
        with tempfile.TemporaryDirectory() as d:
            small = Path(d) / "small.jpg"
            Image.new("RGB", (320, 180), (0, 0, 0)).save(small)
            with self.assertRaises(SystemExit):
                obs.measure(small)

            ok = Path(d) / "ok.jpg"
            img = Image.new("RGB", (640, 360), (0, 0, 0))
            for x in range(300, 310):
                for y in range(170, 180):
                    img.putpixel((x, y), (0, 0, 255))
            img.save(ok, quality=95)
            m = obs.measure(ok)
            self.assertEqual(m["size"], (640, 360))
            self.assertGreater(m["lit"], 50)

    def test_rejects_bad_series_durations(self):
        for bad in ("0", "-2", "nan", "inf", "1e308", "101", "0.03", "0.001"):
            res = subprocess.run(
                [sys.executable, str(REPO / "tools" / "observe.py"), f"--series={bad}"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(res.returncode, 2, bad)
            self.assertIn("--series", res.stderr)
            self.assertIn("0.04", res.stderr)
            self.assertNotIn("Traceback", res.stderr)


if __name__ == "__main__":
    unittest.main()
