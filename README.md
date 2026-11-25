# gtx-970-light-control

Light control for the WINDFORCE logo on a Gigabyte GTX 970 G1 Gaming under
Linux. Worked out, built, and verified by an AI agent watching the card
through a webcam.

![The logo at 100%, 20%, and off, as seen by the webcam](docs/logo-levels.jpg)

## How this came about

The user's prompt, given to Claude Code on the machine itself:

> this machine has a gigabyte windforce gtx 970 that has an rgb light on it.
> when I first got it, I was able to control that light. At some point, that
> option was taken away. See if you can figure out how to control it again. I
> set up a web cam pointed at it, so you should be able to see whether it's
> changed or not.

The machine had a GTX 970 (`10de:13c2`, Gigabyte subsystem `1458:367a`) running
Linux's open-source `nouveau` driver, 21 I2C buses, and one webcam aimed into
the case:

![What the agent saw](docs/webcam-view.jpg)

Newer Gigabyte cards put a vendor RGB controller on an I2C bus, which is what
tools like OpenRGB talk to. Checking this card's VBIOS (`GV-N970G1 GAMING-4GD`)
and era reviews ruled that out: the logo is a single blue LED, not RGB, and it
used to be controlled by the LED Visualizer in NVIDIA's GeForce Experience
before NVIDIA dropped the feature. (That also led to renaming the repo from
`gtx-970-rgb-control` to `gtx-970-light-control`.)

Because NVIDIA's own software drove the light, the card had to use NVIDIA's
reference logo PWM output (the same one behind the glowing GEFORCE GTX badge on
reference cards). `nouveau` already supports that output and exposes it as a
standard Linux LED device at `/sys/class/leds/nvidia-logo`, owned by root and
reset to full brightness on every boot. To prove that writing to it changed the
physical light, the agent built a webcam helper (`tools/observe.py`) that
counts lit pixels in the logo's region of the frame. Writing `0` dropped the
count from 1,183 to 0; writing `16`, `64`, `128`, and `255` stepped it back up
to 388, 611, 799, and 1,140.

With brightness control working, the agent built `gpu-logo`, a CLI paired with
a udev rule so it works without `sudo` and restores the user's chosen mode at
boot. Because Linux's LED subsystem provides built-in triggers for animations,
the agent also added lighting effects: `breathe`, `blink`, `disk` (flickering
on disk activity), and a CPU load indicator. Sampling the webcam over a few
seconds verified each effect as it was built. Here are `breathe` and `blink` on
camera (dark to bright: `` .:-=+*#%@``):

```
breathe   ::----===+++****##%%%%@@@@@%%%##***+++====-----:::      ::
blink     -@@@@@@@        @@@@@@@-       @@@@@@@*       @@@@@@@%
```

Watching the real light caught what reading sysfs back could not: Linux's
built-in `cpu` LED trigger accepted being selected without error and then never
varied on this x86 machine, so the agent switched the CPU mode to the kernel's
`activity` trigger instead.

Next came a request from the user for night dimming: something like a screen
dimmer that fades the logo down before bed and brings it back up in the
morning. Rather than replacing whatever effect is active, the schedule scales
it, so a breathing logo keeps breathing, just fainter. Because the machine's
system clock turned out to be on UTC (which would have shifted every time by
hours), schedules carry an explicit timezone (`--tz`). A systemd timer checks
once a minute and only touches the LED when the level changes; on the card, a
six-minute test fade stepped PWM brightness down from 238 to 29, one step a
minute. Updating a running pattern mid-cycle originally restarted `breathe`
from dark every minute during a fade, so pattern updates now wait for the
cycle's resting point before writing.

Once the user saw the card sitting on a desk in the kernel's `activity` CPU
mode, the rapid flashing looked like a strobe. At the user's suggestion, the
agent replaced it with a custom heartbeat: a slow, soft lub-dub at idle that
beats faster and brighter under sustained CPU load, driven by a lightweight
background sampler that only runs while `cpu` mode is active. Here the camera
caught one more race: switching from `cpu` mode to `breathe` could be
overwritten by one last frame from the exiting sampler, which led to proper
locking and handoff across the CLI, timer, and daemon. Finally, while the
webcam could verify the beat's shape and timing, only the user in the room
could judge the feel, so a few adjustments dialed in the pace. Calm vs. flat
out on camera (each row scaled to its own peak):

```
calm       *********#@@######*************************************#@@######***********
flat out   -=@**#*----------@%+##----------*@**#+---------=@**#*----------@@+##-------
```

## Using it

```sh
gpu-logo off                 # dark
gpu-logo on                  # full brightness
gpu-logo set 30              # fixed brightness, percent
gpu-logo breathe             # fade in and out (--period SEC, --min/--max PERCENT)
gpu-logo blink               # flash (--on MS, --off MS, --level PERCENT)
gpu-logo cpu                 # heartbeat that follows CPU load
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

That example fades down from 23:30 to 02:00, holds at 10% until 08:30, and
returns to full brightness by 09:00, scaling whatever mode is active.

| Option | Meaning |
| --- | --- |
| `--dim` | `START-END` fades down between two 24-hour times (may cross midnight); a single `HH:MM` switches immediately. |
| `--brighten` | Same format, for returning to normal. |
| `--level` | Brightness while dimmed, as a percent of normal (`0` turns the logo off). Default `10`. |
| `--tz` | IANA timezone for the schedule times. Defaults to the system timezone (often UTC on servers); run `gpu-logo schedule` to check which clock is active. Times follow local wall clock, including DST shifts. |

### CPU heartbeat

```sh
gpu-logo cpu
```

Beats from 20 beats/min (~15% peak PWM) at idle up to 60 beats/min (100%) under
full load across all logical cores, keeping a faint resting glow between beats.
CPU usage is sampled every 2 seconds, smoothed over about 10 seconds so brief
spikes barely register, and stepped across nine levels, waiting up to 5 seconds
for the pause between beats to avoid rhythm jumps. The timing and brightness
constants live at the top of `gpu-logo` if you want to tune the feel.

## Setting it up

**With an agent:** point it at [AGENTS.md](AGENTS.md). It checks whether your
card exposes `nvidia-logo`, confirms the light responds by asking you (no
webcam needed), and handles installation.

**By hand:**

```sh
sudo ./install.sh      # install CLI, udev rule, and systemd units
sudo ./uninstall.sh    # remove everything
```

Requires the `nouveau` driver, systemd, Python 3.10+, and membership in the
`plugdev` group (on distros without `plugdev`, change `GROUP` at the top of
`gpu-logo` before running `install.sh`).

## How it works

Nothing here talks to GPU registers directly; everything goes through the
kernel LED device `nouveau` exposes.

| Piece | Role |
| --- | --- |
| `gpu-logo` | CLI. Fixed levels write `brightness`; effects program a kernel LED trigger. No background process stays running except in `cpu` mode. |
| `udev/70-gpu-logo-led.rules` | Runs `gpu-logo udev-hook` as root on `add`/`change` events: grants `GROUP` write access to the LED's sysfs attributes and restores the saved mode on `add`. |
| `systemd/gpu-logo-schedule.timer` | Runs `gpu-logo tick` once a minute, touching the LED only when the schedule's dim factor changes. |
| `systemd/gpu-logo-cpu.path`, `.service` | Watches `/var/lib/gpu-logo/cpu.wake` to start the CPU load sampler when `cpu` mode is selected (with bounded `Restart=on-failure` recovery); the sampler exits cleanly once another mode replaces `cpu`. |
| `modules-load.d/gpu-logo.conf` | Loads `ledtrig-pattern` and `ledtrig-timer` at boot. |
| `/var/lib/gpu-logo/` | Root-owned directory holding the writer lock (`.lock`), daemon liveness lock (`.cpu.lock`), root-only staging directory, `cpu.wake`, and group-writable `state/` (`state.json`, `schedule.json`, `applied.json`). Root validates every read and serializes updates through the lock. |
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
