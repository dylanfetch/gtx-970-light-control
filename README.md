# gtx-970-rgb-control

Control the WINDFORCE logo light on a Gigabyte GTX 970 G1 Gaming
(`GV-N970G1 GAMING-4GD`, PCI `10de:13c2` subsystem `1458:367a`) from Linux.

Despite the repository name, the light is not RGB: it is a single blue LED.
What you get is brightness, effects, and night dimming.

```
gpu-logo off                 # dark
gpu-logo on                  # full brightness
gpu-logo set 30              # fixed brightness, percent
gpu-logo breathe             # fade in and out (--period SEC, --min/--max PERCENT)
gpu-logo blink               # flash (--on MS, --off MS, --level PERCENT)
gpu-logo heartbeat           # double pulse, faster under load
gpu-logo activity            # CPU load meter (see below)
gpu-logo disk                # flicker on disk activity
gpu-logo status
```

The mode you pick is remembered and re-applied at boot. Add `--no-save` before
the command to try something without remembering it.

## Night dimming

```
gpu-logo schedule --from 22:00 --to 07:00 --level 10 --fade 30 --tz America/New_York
gpu-logo schedule            # show the schedule and what it is doing right now
gpu-logo schedule --off      # remove it
```

Like a screen dimmer, this scales whatever mode is showing rather than
replacing it: a breathing logo keeps breathing, just fainter.

| Option | Meaning |
| --- | --- |
| `--from`, `--to` | When dimming begins and when brightening begins, 24-hour. The period may cross midnight. |
| `--level` | Brightness while dimmed, as a percent of normal. `0` turns the logo off. Default 10. |
| `--fade` | Minutes each transition takes, starting at the given time. `0` switches instantly. Default 30. |
| `--tz` | Timezone the times are in. Defaults to the system clock's, which on a server is often UTC; `gpu-logo schedule` prints the time it is working from. |

## What was found

- **The light is blue only.** It is a single-colour LED: brightness and effects
  can be controlled, colour cannot. Reviews and owner reports from the card's
  era say the same, and the only colour changes people managed were physical
  LED swaps.
- **It is NVIDIA's reference "logo LED", not a Gigabyte RGB controller.** The
  card wires the logo to the GPU's own logo-LED PWM output, the same mechanism
  as the GEFORCE GTX logo on reference cards. On Windows that was driven by the
  LED Visualizer in GeForce Experience, which NVIDIA later dropped from it.
- **nouveau already drives it.** The open-source Linux driver registers the
  output as a standard kernel LED at `/sys/class/leds/nvidia-logo` (0-255). Out
  of the box only root can write it, and it resets to full brightness every
  boot, so it looks uncontrollable.

Nothing here talks to the card directly; it all goes through that kernel LED.

## The CPU load meter

`gpu-logo activity` uses the kernel's `activity` LED trigger. It shows one
number, total CPU use across every logical core, by how the logo flashes:

| Load | What the logo does |
| --- | --- |
| Idle | A 10 ms blip once a second |
| Up to one core's worth | Same short blip, coming faster, up to 10 per second |
| Beyond that | 10 flashes per second, each one longer, until the logo is lit 90% of the time with every core busy |

It never goes fully steady, so a hung machine looks different from an idle or a
saturated one. It cannot show cores individually: there is one LED.

## How it works

| Piece | Role |
| --- | --- |
| `gpu-logo` | CLI. Fixed levels write `brightness`; effects select a kernel LED trigger (`pattern`, `timer`, `heartbeat`, `activity`, `disk-activity`), so nothing stays running in the background. |
| `udev/70-gpu-logo-led.rules` | Runs `gpu-logo udev-hook` as root when the LED appears or changes: gives group `plugdev` write access to the LED's attributes, and on first appearance re-applies the saved mode. |
| `systemd/gpu-logo-schedule.timer` | Runs `gpu-logo tick` once a minute. It does nothing unless a schedule is set and the brightness it calls for has changed. |
| `modules-load.d/gpu-logo.conf` | Loads the trigger modules at boot. |
| `/var/lib/gpu-logo/` | The saved mode, the schedule, and a note of what was last applied. Group-writable, so everything read from it is validated strictly before root acts on it. |
| `tools/observe.py` | Measures the logo through a webcam, to confirm a command changed the real light. |

## Install

```
sudo ./install.sh
```

Requires the `nouveau` driver, systemd, Python 3.10 or newer, and membership of
the `plugdev` group. Re-run it after editing `gpu-logo`; the installed copy
lives in `/usr/local/bin`. `sudo ./uninstall.sh` removes everything.

## Tests

```
python3 -m unittest discover tests
```

These cover the schedule arithmetic, input validation and mode logic against a
fake LED directory, so they run anywhere.

## Verified

Tested on Ubuntu 24.04 (kernel 6.8, nouveau) with a webcam aimed at the card,
counting lit pixels in the logo region:

- off, and brightness 16 / 64 / 128 / 255, each produced a distinct level
- breathe, blink, heartbeat, activity (idle and with all cores busy) and disk
  each showed the expected pattern over time
- every mode was visibly fainter under a 20% schedule
- the timer stepped a fade down minute by minute
- the boot-time restore, by replaying the device's udev `add` event

Not tested: an actual reboot, and suspend/resume.

## Limits

- Colour cannot change; see above.
- nouveau only. NVIDIA's proprietary driver does not create
  `/sys/class/leds/nvidia-logo`. It has its own `GPULogoBrightness` setting in
  `nvidia-settings`, which is untested with this card.
- Other cards: anything nouveau exposes as `nvidia-logo` should work, but only
  this model has been tried.
- `tools/observe.py` has the logo's position in the webcam frame hard-coded
  (`ROI`); re-tune it for your own camera.

## License

MIT; see [LICENSE](LICENSE).
