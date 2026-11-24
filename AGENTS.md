# For agents

Everything in this repo drives one kernel LED: `/sys/class/leds/nvidia-logo`.
The nouveau driver creates it for NVIDIA cards whose logo light is wired to the
GPU's logo PWM output. Whether that device exists decides what you do here.

## Setting it up on someone's machine

1. **Find the device.** `ls /sys/class/leds/nvidia-logo`. If it is missing, go to
   [No nvidia-logo device](#no-nvidia-logo-device) and stop there.
2. **Prove it is the light your human means, before installing anything.** As
   root, write `0` and then `255` to `brightness`. Done when a person or a
   camera has seen the light go dark and come back. sysfs reads back whatever
   was written, so reading it proves nothing about the light.
3. **Check the group.** `gpu-logo` grants LED access to `plugdev`. If
   `id -nG` lacks it, either add the user (takes effect at next login) or change
   `GROUP` in `gpu-logo` to a group they are in.
4. **Install.** `sudo ./install.sh`. Ask your human for the sudo password.
5. **Verify as the normal user.** `gpu-logo off`, `gpu-logo on`,
   `gpu-logo breathe`, each without sudo. Done when each one has been seen to
   change the light.
6. **Ask what they want.** A mode (`gpu-logo --help`), and whether to dim at
   night. Schedule times are read on the system clock, which is often UTC on a
   machine nobody set up by hand: check `timedatectl` and pass `--tz` with the
   zone your human lives in.

## No nvidia-logo device

`lspci -k` shows which driver the card uses.

- **`nvidia` (proprietary driver).** This repo does not apply. That driver has a
  `GPULogoBrightness` attribute in `nvidia-settings`; nobody has tried it on
  this card. Switching to nouveau is your human's decision, and an expensive
  one: on this GPU generation nouveau cannot raise the card's clocks, so 3D
  performance drops badly.
- **`nouveau`, yet no LED.** The card's firmware does not declare a logo LED, so
  its light is driven some other way, typically a vendor RGB controller on one
  of the card's I2C buses. OpenRGB and liquidctl are where to look. The method
  in the README still applies: find out what software used to control the
  light, and that tells you which mechanism the card uses.

## Verifying with a camera

`tools/observe.py` counts lit pixels in the logo's region of a webcam frame, and
`--series SECONDS` samples over time to show an effect's shape. Its `ROI` is for
one particular camera placement. To find yours, capture a frame at brightness
255 and another at 0 and take the bounding box of what changed.

## Changing the code

- The running copy is `/usr/local/bin/gpu-logo`. Edits to the repo take effect
  after `sudo ./install.sh`.
- `python3 -m unittest discover tests` covers the logic with a fake LED
  directory. It cannot tell you what the light did; for anything that touches
  the LED, observe it.
- LED behaviour that is easy to get wrong. `gpu-logo` handles the first four,
  with a comment at each spot:
  - Writing `0` to `brightness` while a trigger is active removes the trigger.
  - A trigger's own attributes (`pattern`, `delay_on`, ...) are created
    root-only every time the trigger is set. The udev hook regroups them on the
    resulting `change` event, so an unprivileged write has to wait for that.
  - Restoring the saved mode sets a trigger, which emits `change`. Restore on
    `add` only.
  - `heartbeat`, `activity` and `disk-activity` flash at the LED's remembered
    blink brightness, left over from the previous effect. Set it explicitly.
  - The kernel's `cpu` trigger accepts being selected and then never varies on
    this hardware.
