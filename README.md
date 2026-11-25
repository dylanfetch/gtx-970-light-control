# gtx-970-light-control

Brightness, effects, and night dimming for the WINDFORCE logo light on a
Gigabyte GTX 970 G1 Gaming under Linux. Worked out and built by an AI agent
that could see the card through a webcam.

![The logo at 100%, at 20%, and off, as seen by the webcam](docs/logo-levels.jpg)

## How this came about

The prompt, typed into Claude Code on the machine itself:

> this machine has a gigabyte windforce gtx 970 that has an rgb light on it.
> when I first got it, I was able to control that light. At some point, that
> option was taken away. See if you can figure out how to control it again. I
> set up a web cam pointed at it, so you should be able to see whether it's
> changed or not.

The machine had a GTX 970 with a Gigabyte subsystem ID (`10de:13c2`,
`1458:367a`) running the open-source `nouveau` driver, 21 I2C buses, and one
webcam pointed into the case:

![What the agent saw](docs/webcam-view.jpg)

Newer Gigabyte cards put a vendor RGB controller on an I2C bus (the kind tools
like OpenRGB talk to), so that was the first place to look. Searching for this
specific model (`GV-N970G1 GAMING-4GD`) ruled that out: reviews from its era
noted that the logo was blue only and had been controlled by the LED Visualizer
in NVIDIA's GeForce Experience—the feature NVIDIA later dropped.

If NVIDIA's own software drove the light, the card had to be wired to NVIDIA's
reference logo PWM output (the same one behind the GEFORCE GTX logo on
reference boards). `nouveau` already supports that output and exposes it at
`/sys/class/leds/nvidia-logo`, owned by root and reset to 100% on every boot.
Writing `0` dropped the webcam's lit-pixel count in the logo region from 1,183
to 0; writing `16`, `64`, `128`, and `255` stepped it back up to 388, 611, 799,
and 1,140.

Sampling the webcam over a few seconds verified each effect as it was built.
Here are `breathe` and `blink` at 15 chars/sec (darker to brighter:
`` .:-=+*#%@``):

```
breathe   ::----===+++****##%%%%@@@@@%%%##***+++====-----:::      ::
blink     -@@@@@@@        @@@@@@@-       @@@@@@@*       @@@@@@@%
```

The camera caught two bugs that reading sysfs back never would have: Linux's
built-in `cpu` LED trigger accepted selection without error and never varied
the light on this hardware, and an early version of the background load sampler
overwrote the next mode with one final frame after another mode had replaced it.

## Setup

**With an AI agent:** point it at [AGENTS.md](AGENTS.md). It walks through
checking whether your card exposes `nvidia-logo`, installing, and confirming
each change with you. No webcam is needed.

**By hand:**

```sh
sudo ./install.sh      # install CLI, udev rule, and systemd units
sudo ./uninstall.sh    # remove everything
```

Requires the `nouveau` driver, systemd, Python 3.10+, and membership in the
`plugdev` group (on distros without `plugdev`, edit `GROUP` at the top of
`gpu-logo` before running `install.sh`).

## Usage

```sh
gpu-logo off                 # dark
gpu-logo on                  # full brightness
gpu-logo set 30              # fixed brightness, percent
gpu-logo breathe             # fade in and out (--period SEC, --min/--max PERCENT)
gpu-logo blink               # flash (--on MS, --off MS, --level PERCENT)
gpu-logo cpu                 # heartbeat that follows CPU load (see below)
gpu-logo disk                # flicker on disk activity
gpu-logo status
```

Your chosen mode is saved and re-applied at boot. Pass `--no-save` before the
subcommand to try a mode without changing what boots next time.

### Night dimming

```sh
gpu-logo schedule --dim 23:30-02:00 --brighten 08:30-09:00 --level 10 --tz America/New_York
gpu-logo schedule            # show the current schedule and active dim factor
gpu-logo schedule --off      # remove the schedule
```

That example starts fading down at 23:30, reaches 10% at 02:00, holds until
08:30, and returns to full brightness by 09:00. The schedule scales whatever
mode is currently running—a breathing logo keeps breathing, just fainter.

| Option | Meaning |
| --- | --- |
| `--dim` | `START-END` fades down between the two 24-hour times (may cross midnight); a single `HH:MM` switches immediately. |
| `--brighten` | Same format, for returning to normal. |
| `--level` | Brightness while dimmed, as a percent of normal (`0` turns the logo off). Default `10`. |
| `--tz` | IANA timezone for the schedule times. Defaults to the system timezone (often UTC on servers); run `gpu-logo schedule` to check which clock is active. Times follow local wall clock, including DST shifts. |

### CPU heartbeat

Linux's built-in `activity` LED trigger flashes sharply, which looked like a
strobe on a desk. `gpu-logo cpu` replaces it with a slower lub-dub heartbeat: a
strong pulse, a softer second pulse, and a faint glow during the rest between
beats.

| | Calm | Flat out |
| --- | --- | --- |
| Pace | 20 beats/min | 60 beats/min |
| Peak brightness | ~15% PWM | 100% |

Most of the speed change happens in the pause between beats: the beat itself
tightens from 0.7 s to 0.45 s, while the rest shrinks from 2.3 s to 0.55 s.
Total CPU usage across all logical cores is sampled every 2 seconds and
smoothed over ~10 seconds so brief spikes barely register. Pace and brightness
move in nine quantized steps, and updates wait (capped at five seconds) for the
rest between beats to avoid visible rhythm jumps.

Five seconds each of calm and flat out, captured through the webcam at 15
chars/sec (each row is auto-scaled to its own peak brightness, so the calm
row's low resting glow reads taller relative to its peak):

```
calm       *********#@@######*************************************#@@######***********
flat out   -=@**#*----------@%+##----------*@**#+---------=@**#*----------@@+##-------
```

The timing and brightness constants live at the top of `gpu-logo` if you want
to tune the feel.

## How it works

Nothing here talks to GPU registers directly; everything goes through the
kernel LED device `nouveau` exposes.

| Piece | Role |
| --- | --- |
| `gpu-logo` | CLI. Fixed levels write `brightness`; effects program a kernel LED trigger. No background process stays running except in `cpu` mode. |
| `udev/70-gpu-logo-led.rules` | Runs `gpu-logo udev-hook` as root on `add`/`change` events: grants `GROUP` write access to the LED's sysfs attributes and restores the saved mode on `add`. |
| `systemd/gpu-logo-schedule.timer` | Runs `gpu-logo tick` once a minute, touching the LED only when the schedule's dim factor changes. |
| `systemd/gpu-logo-cpu.path`, `.service` | Watches `/var/lib/gpu-logo/cpu.wake` to start the CPU load sampler when `cpu` mode is selected; the sampler exits as soon as another mode replaces `cpu`. |
| `modules-load.d/gpu-logo.conf` | Loads `ledtrig-pattern` and `ledtrig-timer` at boot. |
| `/var/lib/gpu-logo/` | Root-owned directory holding the writer lock, root-only staging directory, `cpu.wake`, and group-writable `state/` (`state.json`, `schedule.json`, `applied.json`). Root validates every read and serializes updates through the lock. |
| `tools/observe.py` | Optional webcam helper (requires GStreamer `gst-launch-1.0` and Pillow) to verify changes visually. |
| `tests/` | Unit tests for schedule math, validation, locking, pattern timing, and mode transitions against a fake LED directory: `python3 -m unittest discover tests`. |

## Limits

- **Blue only:** the logo is a single-color blue LED, not RGB.
- **`nouveau` only:** NVIDIA's proprietary driver does not expose
  `/sys/class/leds/nvidia-logo` (it has a `GPULogoBrightness` attribute in
  `nvidia-settings`, untested on this card).
- **Hardware and boot testing:** tested on Ubuntu 24.04 (kernel 6.8, `nouveau`)
  with a Gigabyte GTX 970 G1 Gaming (`10de:13c2`, subsystem `1458:367a`). Other
  cards that `nouveau` exposes as `nvidia-logo` should work. Boot restore was
  verified by replaying udev `add` events; an actual reboot and suspend/resume
  have not been tested.
- **`tools/observe.py`:** its logo `ROI` is hardcoded to one camera placement;
  re-tune it for your own webcam.

## License

MIT; see [LICENSE](LICENSE).
