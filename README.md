# gtx-970-light-control

Brightness, effects and night dimming for the WINDFORCE logo light on a
Gigabyte GTX 970 G1 Gaming, from Linux. Worked out and built by an AI agent
that could see the card through a webcam.

![The logo at 100%, at 20%, and off, as seen by the webcam](docs/logo-levels.jpg)

## How this came about

The brief, typed to Claude Code on the machine itself:

> this machine has a gigabyte windforce gtx 970 that has an rgb light on it.
> when I first got it, I was able to control that light. At some point, that
> option was taken away. See if you can figure out how to control it again. I
> set up a web cam pointed at it, so you should be able to see whether it's
> changed or not.

What the agent did with it, in order:

1. **Looked at the machine.** A GTX 970 with a Gigabyte subsystem ID, running
   the open-source nouveau driver, with 21 I2C buses and one webcam. The first
   frame from the webcam showed the logo, lit blue.

   ![What the agent saw](docs/webcam-view.jpg)

2. **Chased the obvious idea, and dropped it.** Newer Gigabyte cards have an RGB
   controller chip on an I2C bus, and tools such as OpenRGB talk to it. Searching
   turned up no such thing for this card. It turned up something better: reviews
   from the card's era saying the logo was controlled by the LED Visualizer in NVIDIA's
   GeForce Experience, and that it was blue only.
3. **Followed what that implied.** If NVIDIA's own software drove the light, the
   card must use NVIDIA's reference logo-LED output, the one behind the GEFORCE
   GTX logo on reference cards. nouveau has a driver for that output. One `ls`
   later: `/sys/class/leds/nvidia-logo`, sitting there the whole time, writable
   only by root and reset to full brightness on every boot.
4. **Proved it with the camera.** Wrote `0` to the LED; lit pixels in the logo
   region went from 1183 to 0. Then 16, 64, 128 and 255 gave 388, 611, 799 and
   1140.
5. **Checked the premise.** The card's firmware names it `GV-N970G1 GAMING-4GD`.
   That model's logo is a single blue LED, so the "RGB" in the brief was a
   misremembering, and the option that "was taken away" was NVIDIA dropping the
   LED Visualizer.
6. **Built the tool, and kept watching.** Each effect was checked by sampling
   the webcam over a few seconds. This is the logo's brightness over time, one
   character per video frame:

   ```
   breathe    %@@@@@@@@@@@@%%%%%%######**********++++++===========------------:::                 .:::::---------------=========++=++
   blink      -%@@@@@@@@@@@@@@               #@@@@@@@@@@@@@@-              #@@@@@@@@@@@@@@*              *@@@@@@@@@@@@@@%
   heartbeat      *@@ -#@          +%% -#@:         +%% =%@-         =%% -#@          =%% -#@          +%@ :#@-         =@%  #@=
   ```

   The camera also caught a mode that did nothing. A "cpu" effect ran without
   error, but the logo never changed, so it was replaced with one that works.
7. **Added night dimming when asked for it.** The request was for something
   like a screen dimmer, so the schedule scales whatever mode is showing
   instead of replacing it: a breathing logo keeps breathing, just fainter.
   Two things surfaced along the way. The machine's clock turned out to be on
   UTC, which would have put every time hours off, so a schedule carries its
   own timezone. And some effects ignore the LED's brightness and flash at a
   level left over from whatever ran before, so that level is now set
   explicitly. On the card, every mode came out fainter under a 20% schedule,
   and a six-minute fade stepped 238, 189, 136, 91, 56, 29, one step a minute.

## Using it

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

### Night dimming

```
gpu-logo schedule --from 23:30 --to 08:30 --level 10 --fade 30 --tz America/New_York
gpu-logo schedule            # show the schedule and what it is doing right now
gpu-logo schedule --off      # remove it
```

This scales whatever mode is showing rather than replacing it.

| Option | Meaning |
| --- | --- |
| `--from`, `--to` | When dimming begins and when brightening begins, 24-hour. The period may cross midnight. |
| `--level` | Brightness while dimmed, as a percent of normal. `0` turns the logo off. Default 10. |
| `--fade` | Minutes each transition takes, starting at the given time. `0` switches instantly. Default 30. |
| `--tz` | Timezone the times are in. Defaults to the system clock's, which on a server is often UTC; `gpu-logo schedule` prints the time it is working from. |

### The CPU load meter

`gpu-logo activity` uses the kernel's `activity` LED trigger. It shows one
number, total CPU use across every logical core, by how the logo flashes:

| Load | What the logo does |
| --- | --- |
| Idle | A 10 ms blip once a second |
| Up to one core's worth | Same short blip, coming faster, up to 10 per second |
| Beyond that | 10 flashes per second, each one longer, until the logo is lit 90% of the time with every core busy |

It never goes fully steady, so a hung machine looks different from an idle or a
saturated one. It cannot show cores individually: there is one LED. Be warned
that on a lightly loaded desktop it reads as a strobe.

## Setting it up

Hand it to your agent: [AGENTS.md](AGENTS.md) tells it how to check whether your
card qualifies, install, and confirm with you that the light responds. No
webcam is needed; it will ask you what the light is doing.

By hand: `sudo ./install.sh`, and `sudo ./uninstall.sh` to remove everything.
It needs the nouveau driver, systemd, Python 3.10 or newer, and membership of
the `plugdev` group.

## How it works

Nothing here talks to the card directly; it all goes through the kernel LED
that nouveau provides.

| Piece | Role |
| --- | --- |
| `gpu-logo` | CLI. Fixed levels write `brightness`; effects select a kernel LED trigger (`pattern`, `timer`, `heartbeat`, `activity`, `disk-activity`), so nothing stays running in the background. |
| `udev/70-gpu-logo-led.rules` | Runs `gpu-logo udev-hook` as root when the LED appears or changes: gives group `plugdev` write access to the LED's attributes, and on first appearance re-applies the saved mode. |
| `systemd/gpu-logo-schedule.timer` | Runs `gpu-logo tick` once a minute. It does nothing unless a schedule is set and the brightness it calls for has changed. |
| `modules-load.d/gpu-logo.conf` | Loads the trigger modules at boot. |
| `/var/lib/gpu-logo/` | The saved mode, the schedule, and a note of what was last applied. Group-writable, so everything read from it is validated strictly before root acts on it. |
| `tools/observe.py` | Optional. Measures the logo through a webcam, to confirm a command changed the real light. |
| `tests/` | Schedule arithmetic, input validation and mode logic against a fake LED directory: `python3 -m unittest discover tests` |

## What was verified

On Ubuntu 24.04 (kernel 6.8, nouveau), by counting lit pixels in the logo
region of the webcam image:

- off, and brightness 16 / 64 / 128 / 255, each produced a distinct level
- breathe, blink, heartbeat, activity (idle and with all cores busy) and disk
  each showed the expected pattern over time
- every mode was visibly fainter under a 20% schedule
- the timer stepped a fade down minute by minute
- the boot-time restore, by replaying the device's udev `add` event

Not tested: an actual reboot, and suspend/resume.

## Limits

- Colour cannot change: the LED is blue.
- nouveau only. NVIDIA's proprietary driver does not create
  `/sys/class/leds/nvidia-logo`. It has its own `GPULogoBrightness` setting in
  `nvidia-settings`, which is untested with this card.
- Other cards: anything nouveau exposes as `nvidia-logo` should work, but only
  this model (PCI `10de:13c2`, subsystem `1458:367a`) has been tried.
- `tools/observe.py` has the logo's position in the webcam frame hard-coded
  (`ROI`); re-tune it for your own camera.

## License

MIT; see [LICENSE](LICENSE).
