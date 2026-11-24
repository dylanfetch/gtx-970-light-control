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
        # A plain file cannot list the available triggers the way sysfs does.
        self.g.read_trigger = lambda: (self.attr("trigger"), ["none", "timer", "pattern", "heartbeat",
                                                              "activity", "disk-activity"])

    def attr(self, name: str) -> str:
        return (self.led / name).read_text().strip()


class DimAtTest(GpuLogoTest):
    def schedule(self, start="22:00", end="07:00", level=10, fade=30):
        return self.g.normalize_schedule({
            "start_min": self.g.parse_clock(start), "end_min": self.g.parse_clock(end),
            "level": level, "fade_min": fade})

    def at(self, schedule, clock):
        return self.g.dim_at(schedule, self.g.parse_clock(clock))

    def test_night_spanning_midnight(self):
        s = self.schedule()
        self.assertEqual(self.at(s, "21:59"), 1.0)
        self.assertEqual(self.at(s, "22:00"), 1.0)  # fade only just starting
        self.assertAlmostEqual(self.at(s, "22:30"), 0.1)
        self.assertAlmostEqual(self.at(s, "23:59"), 0.1)
        self.assertAlmostEqual(self.at(s, "00:00"), 0.1)
        self.assertAlmostEqual(self.at(s, "07:00"), 0.1)  # brightening only just starting
        self.assertEqual(self.at(s, "07:30"), 1.0)
        self.assertEqual(self.at(s, "12:00"), 1.0)

    def test_fade_moves_steadily_in_the_right_direction(self):
        s = self.schedule()
        down = [self.g.dim_at(s, 22 * 60 + m) for m in range(31)]
        up = [self.g.dim_at(s, 7 * 60 + m) for m in range(31)]
        self.assertEqual(down, sorted(down, reverse=True))
        self.assertEqual(up, sorted(up))
        self.assertTrue(0.1 < down[15] < 1.0)

    def test_period_within_one_day(self):
        s = self.schedule(start="01:00", end="05:00", level=0, fade=0)
        self.assertEqual(self.at(s, "00:59"), 1.0)
        self.assertEqual(self.at(s, "01:00"), 0.0)
        self.assertEqual(self.at(s, "04:59"), 0.0)
        self.assertEqual(self.at(s, "05:00"), 1.0)

    def test_rejects_bad_schedules(self):
        for start, end, level, fade in [
            ("22:00", "22:00", 10, 0),    # empty period
            ("22:00", "23:00", 10, 90),   # fade longer than the night
            ("01:00", "23:30", 10, 120),  # fade longer than the day
            ("22:00", "07:00", 101, 0),
        ]:
            with self.assertRaises(self.g.Fail):
                self.schedule(start, end, level, fade)
        for text in ["24:00", "7", "07:60", "seven"]:
            with self.assertRaises(self.g.Fail):
                self.g.parse_clock(text)

    def test_timezone_is_kept_only_if_real(self):
        base = {"start_min": 0, "end_min": 60, "level": 10}
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
        for mode in [{"mode": "heartbeat"}, {"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100},
                     {"mode": "blink", "on_ms": 500, "off_ms": 500, "level": 100}]:
            self.g.apply(mode, dim=0.0)
            self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("none", "0"), mode)

    def test_breathe_peak_is_scaled_by_dim(self):
        self.g.apply({"mode": "breathe", "period_ms": 4000, "min": 0, "max": 100}, dim=0.2)
        levels = [int(v) for v in self.attr("pattern").split()[::2]]
        self.assertEqual((self.attr("trigger"), min(levels), max(levels)), ("pattern", 0, 51))

    def test_trigger_modes_set_their_flash_level(self):
        self.g.apply({"mode": "activity"}, dim=0.5)
        self.assertEqual((self.attr("trigger"), self.attr("brightness")), ("activity", "128"))


class TickTest(GpuLogoTest):
    """The schedule follows whatever is showing, and leaves it alone when nothing changed."""

    def set_schedule(self, level):
        # A period covering the whole day except one minute that is never "now"
        # would be fragile, so pin the clock instead.
        self.g.write_json(self.g.SCHEDULE_FILE, {"start_min": 0, "end_min": 720, "level": level, "fade_min": 0})

    def test_tick_dims_the_showing_mode_once(self):
        self.g.current_dim = lambda schedule: 0.25 if schedule else 1.0
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.set_schedule(25)
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "51")
        (self.led / "brightness").write_text("7\n")  # a second tick must not touch the LED
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "7")

    def test_tick_without_a_schedule_does_nothing(self):
        self.g.tick()
        self.assertEqual(self.attr("brightness"), "255")

    def test_record_from_an_earlier_boot_is_ignored(self):
        self.g.show({"mode": "static", "level": 80}, 1.0)
        self.g.boot_id = lambda: "a-later-boot"
        self.assertIsNone(self.g.load_applied())


if __name__ == "__main__":
    unittest.main()
