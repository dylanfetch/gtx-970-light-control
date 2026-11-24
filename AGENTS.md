# For agents

Everything in this repo drives one kernel LED: `/sys/class/leds/nvidia-logo`.
The nouveau driver creates it for NVIDIA cards whose logo light is wired to the
GPU's logo PWM output. Whether that device exists decides what you do here.

## Setting it up on someone's machine

Your human is your eyes. Wherever a step says to ask them, tell them what you
just did and ask what the light is doing.

1. **Find the device.** `ls /sys/class/leds/nvidia-logo`. If it is missing, go to
   [No nvidia-logo device](#no-nvidia-logo-device) and stop there.
2. **Confirm it is the light your human means, before installing anything.** As
   root, write `0` to `brightness` and ask whether the light went out, then
   write `255` and ask whether it came back. Done when they have said yes to
   both. sysfs reads back whatever was written, so reading it proves nothing
   about the light.
3. **Check the group.** `gpu-logo` grants LED access to `plugdev`. If
   `id -nG` lacks it, either add the user (takes effect at next login) or change
   `GROUP` in `gpu-logo` to a group they are in.
4. **Install.** `sudo ./install.sh`. Ask your human for the sudo password.
5. **Verify as the normal user.** Run `gpu-logo off`, `gpu-logo on` and
   `gpu-logo breathe`, each without sudo, asking after each one. Done when your
   human has confirmed all three.
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

## Optional: a camera instead of asking

If a webcam happens to be pointed at the card, `tools/observe.py` can answer
the "what is the light doing" questions for you. It counts lit pixels in the
logo's region of a frame, and `--series SECONDS` samples over time to show an
effect's shape. Its `ROI` is for one particular camera placement. To find
yours, capture a frame at brightness 255 and another at 0 and take the bounding
box of what changed.

## Changing the code

- The running copy is `/usr/local/bin/gpu-logo`. Edits to the repo take effect
  after `sudo ./install.sh`.
- `python3 -m unittest discover tests` covers the logic with a fake LED
  directory. It cannot tell you what the light did; for anything that touches
  the LED, ask your human or use the camera.
- LED behaviour that is easy to get wrong. `gpu-logo` handles each of these,
  with a comment at the spot:
  - Writing `0` to `brightness` while a trigger is active removes the trigger.
  - A trigger's own attributes (`pattern`, `delay_on`, ...) are created
    root-only every time the trigger is set. The udev hook regroups them on the
    resulting `change` event, so an unprivileged write has to wait for that.
  - Restoring the saved mode sets a trigger, which emits `change`. Restore on
    `add` only.
  - `disk-activity` (like the kernel's `heartbeat` and `activity` triggers)
    flashes at the LED's remembered blink brightness, left over from the
    previous effect. Set it explicitly.
  - The `pattern` trigger redraws a fade every 50 ms and spends one extra
    redraw on each fading tuple, so a tuple written as 100 ms lasts 150.
  - Writing `pattern` restarts it from its first tuple. To change a running
    pattern without a visible jump, wait until `brightness` reads the
    pattern's resting level. The driver reads that back from hardware and can
    return one count less than was written.
- The kernel's `cpu` trigger accepts being selected and then never varies on
  this hardware, and its `activity` trigger reads as a strobe. The `cpu` mode
  here draws its own heartbeat instead.
- `gpu-logo cpu-daemon` must check that its mode is still showing immediately
  before every write. It once overwrote the mode that had replaced it.
