# gtx-970-rgb-control

Control the WINDFORCE logo light on a Gigabyte GTX 970 G1 Gaming
(`GV-N970G1 GAMING-4GD`, PCI `10de:13c2` subsystem `1458:367a`) from Linux.

```
gpu-logo off                 # dark
gpu-logo on                  # full brightness
gpu-logo set 30              # fixed brightness, percent
gpu-logo breathe             # fade in and out (--period SEC, --min/--max PERCENT)
gpu-logo blink               # flash (--on MS, --off MS, --level PERCENT)
gpu-logo heartbeat           # double pulse, faster under load
gpu-logo activity            # CPU load meter: faint slow flashes idle, fast and bright busy
gpu-logo disk                # flicker on disk activity
gpu-logo status
```

The mode you pick is remembered and re-applied at boot. Add `--no-save` before
the command to try something without remembering it.

## What was found

- **The light is blue only.** It is a single-colour LED, not RGB: brightness and
  effects can be controlled, colour cannot. Reviews and owner reports from the
  card's era say the same, and the only colour changes people managed were
  physical LED swaps.
- **It is NVIDIA's reference "logo LED", not a Gigabyte RGB controller.** The
  card wires the logo to the GPU's own logo-LED PWM output, the same mechanism
  as the GEFORCE GTX logo on reference cards. On Windows that was driven by the
  LED Visualizer in GeForce Experience, which NVIDIA later dropped. That is the
  likely reason the option disappeared.
- **nouveau already drives it.** The open-source driver registers the output as
  a standard kernel LED at `/sys/class/leds/nvidia-logo` (0-255). Out of the box
  only root can write it, and it resets to full brightness every boot, so it
  looks uncontrollable.

Nothing here talks to the card directly; it all goes through that kernel LED.

## How it works

| Piece | Role |
| --- | --- |
| `gpu-logo` | CLI. Fixed levels write `brightness`; effects select a kernel LED trigger (`pattern`, `timer`, `heartbeat`, `activity`, `disk-activity`), so nothing stays running in the background. |
| `udev/70-gpu-logo-led.rules` | Runs `gpu-logo udev-hook` as root when the LED appears or changes: gives group `plugdev` write access to the LED's attributes, and on first appearance re-applies the saved mode. |
| `modules-load.d/gpu-logo.conf` | Loads the trigger modules at boot. |
| `/var/lib/gpu-logo/state.json` | The saved mode. Group-writable, so it is validated strictly before root applies it. |
| `tools/observe.py` | Measures the logo through a webcam, to confirm a command changed the real light. |

## Install

```
sudo ./install.sh
```

Requires the `nouveau` driver and membership of `plugdev`. Re-run after editing
`gpu-logo`; the installed copy lives in `/usr/local/bin`.

To remove:

```
sudo rm /usr/local/bin/gpu-logo /etc/udev/rules.d/70-gpu-logo-led.rules /etc/modules-load.d/gpu-logo.conf
sudo rm -r /var/lib/gpu-logo
```

## Verified

Checked on this machine (Ubuntu 24.04, kernel 6.8, nouveau) with a webcam aimed
at the card, counting lit pixels in the logo region:

- off, and brightness 16 / 64 / 128 / 255, each produced a distinct level
- breathe, blink, heartbeat, activity (idle and with all cores busy) and disk
  each showed the expected pattern over time
- the boot-time restore, by replaying the device's udev `add` event

Not checked: an actual reboot, and suspend/resume.

## Limits

- Colour cannot change; see above.
- nouveau only. The proprietary NVIDIA driver does not provide
  `/sys/class/leds/nvidia-logo`, and this GPU needs the legacy 470 driver branch
  anyway. Whether that driver exposes the logo some other way is untested here.
- `tools/observe.py` has the logo's position in the webcam frame hard-coded
  (`ROI`); re-tune it if the camera moves.
