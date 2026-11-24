"""Tests for the parts of gpu-logo that do not need the real card.

Run from the repository root:  python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_tool(led_dir: Path, state_dir: Path):
    """Import the gpu-logo script (it has no .py suffix) pointed at fake directories."""
    os.environ["GPU_LOGO_LED"] = str(led_dir)
    os.environ["GPU_LOGO_STATE_DIR"] = str(state_dir)
    loader = importlib.machinery.SourceFileLoader("gpu_logo", str(REPO / "gpu-logo"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("gpu_logo", loader))
    loader.exec_module(module)
    return module


class GpuLogoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.led = Path(tmp.name) / "led"
        self.state = Path(tmp.name) / "state"
        self.led.mkdir()
        self.state.mkdir()
        (self.led / "max_brightness").write_text("255\n")
        (self.led / "brightness").write_text("255\n")
        (self.led / "trigger").write_text("none\n")
        self.g = load_tool(self.led, self.state)
        self.g.SYNC_WAIT_S = 0  # a fake LED never reaches its resting level
        # A plain file cannot list the available triggers the way sysfs does.
        self.g.read_trigger = lambda: (self.attr("trigger"), ["none", "timer", "pattern", "heartbeat",
                                                              "activity", "disk-activity"])

    def attr(self, name: str) -> str:
        return (self.led / name).read_text().strip()


class ScheduleTest(GpuLogoTest):
    def schedule(self, dim="23:30-02:00", brighten="08:30-09:00", level=10):
        (ds, de), (bs, be) = self.g.parse_span(dim), self.g.parse_span(brighten)
        return self.g.normalize_schedule(
            {"dim_start": ds, "dim_end": de, "bright_start": bs, "bright_end": be, "level": level})

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

    def test_rejects_bad_schedules(self):
        for dim, brighten, level in [
            ("22:00", "22:00", 10),              # never dimmed
            ("23:30-02:00", "01:00-09:00", 10),  # brightening starts mid-dim
            ("23:00-01:00", "01:00-23:30", 10),  # the two fades overlap round the clock
            ("22:00", "07:00", 101),
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

    def test_breathe_peak_is_scaled_by_dim(self):
        self.g.apply({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100}, dim=0.2)
        levels = [int(v) for v in self.attr("pattern").split()[::2]]
        self.assertEqual((self.attr("trigger"), min(levels), max(levels)), ("pattern", 0, 51))

    def test_disk_mode_sets_its_flash_level(self):
        self.g.apply({"mode": "disk"}, dim=0.5)
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("disk-activity", "128"))

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
        values = [int(v) for v in self.g.heartbeat_pattern(effort, dim).split()]
        levels, times = values[::2], values[1::2]
        # every fading tuple costs the kernel one extra 50 ms redraw; the final wait does not
        return levels, sum(times[:-1]) + 50 * (len(times) - 1) + times[-1]

    def test_calm_is_slow_and_soft_and_flat_out_is_fast_and_strong(self):
        calm, calm_ms = self.shape(0.0)
        hard, hard_ms = self.shape(1.0)
        self.assertEqual((calm_ms, hard_ms), self.g.BEAT_PERIOD_MS)
        self.assertEqual(max(hard), 255)
        self.assertTrue(max(calm) < 60)
        self.assertTrue(min(calm) > 0)  # a glow remains between beats

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


class TickTest(GpuLogoTest):
    """The schedule follows whatever is showing, and leaves it alone when nothing changed."""

    def set_schedule(self, level):
        # A period covering the whole day except one minute that is never "now"
        # would be fragile, so pin the clock instead.
        self.g.write_json(self.g.SCHEDULE_FILE, {"dim_start": 0, "dim_end": 0, "bright_start": 720,
                                                 "bright_end": 720, "level": level})

    def test_tick_dims_the_showing_mode_once(self):
        self.g.current_dim = lambda schedule: 0.25 if schedule else 1.0
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.set_schedule(25)
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "51")
        (self.led / "brightness").write_text("7\n")  # a second tick must not touch the LED
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "7")

    def test_tick_leaves_a_running_heartbeat_to_the_daemon(self):
        self.g.current_dim = lambda schedule: 0.25 if schedule else 1.0
        self.g.show({"mode": "cpu"}, 1.0)
        before = self.attr("pattern")
        self.set_schedule(25)
        self.g.tick()
        self.assertEqual(self.attr("pattern"), before)
        self.assertEqual(self.g.load_applied(), ({"mode": "cpu"}, 0.25))

    def test_tick_without_a_schedule_does_nothing(self):
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "255")

    def test_record_from_an_earlier_boot_is_ignored(self):
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.g.boot_id = lambda: "a-later-boot"
        self.assertIsNone(self.g.load_applied())


if __name__ == "__main__":
    unittest.main()
