#!/usr/bin/env python3
"""Watch the WINDFORCE logo through the webcam and measure it.

Used to verify, from the outside, that an LED command really changed the light.
Requires GStreamer (`gst-launch-1.0`) and Pillow (`PIL`).

  observe.py [out.jpg]          one frame: mean R G B of the logo region
  observe.py --series SECONDS   sample over time: prints how lit the logo is in
                                each frame, to see blinking or breathing
"""
import argparse
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

DEVICE = "/dev/video0"
FPS = 30  # expected default camera rate; actual elapsed time is printed in --series mode
WARMUP_FRAMES = 20  # lets auto-exposure settle
# Logo bounding box in a 640x360 frame (x0, y0, x1, y1). Re-tune if the webcam moves.
ROI = (296, 166, 370, 196)


def grab(pattern: str, frames: int) -> None:
    """Capture `frames` frames; `pattern` is a multifilesink location (must contain %05d)."""
    subprocess.run(
        ["gst-launch-1.0", "-q", "v4l2src", f"device={DEVICE}", f"num-buffers={frames}",
         "!", "videoconvert", "!", "jpegenc", "!", "multifilesink", f"location={pattern}"],
        check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def measure(path: Path) -> dict:
    with Image.open(path) as raw:
        img = raw.convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = ROI
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise SystemExit(
            f"observe.py: ROI {ROI} does not fit inside captured frame {w}x{h}; re-tune ROI for this camera"
        )
    px = list(img.crop(ROI).getdata())
    n = len(px)
    mean = [sum(p[i] for p in px) / n for i in range(3)]
    # Count of clearly lit pixels: steadier than the mean, which the camera's
    # auto-exposure drags around.
    lit = sum(1 for p in px if max(p) > 150)
    return {"r": mean[0], "g": mean[1], "b": mean[2], "lit": lit, "n": n, "size": (w, h)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", type=Path, help="where to keep the frame (single-frame mode)")
    ap.add_argument("--series", type=float, metavar="SECONDS")
    a = ap.parse_args()

    if a.series is None:
        with tempfile.TemporaryDirectory() as d:
            # Capture into a controlled pattern so '%' in a user path is never formatted by GStreamer.
            grab(f"{d}/f%05d.jpg", WARMUP_FRAMES)
            captured = sorted(Path(d).glob("f*.jpg"))
            if not captured:
                raise SystemExit("observe.py: no frames captured")
            if a.out is None:
                fd, tmp = tempfile.mkstemp(suffix=".jpg")
                os.close(fd)
                out = Path(tmp)
            else:
                out = a.out
            shutil.copyfile(captured[-1], out)
        m = measure(out)
        w, h = m["size"]
        print(f"{out}  {w}x{h}  R={m['r']:.1f} G={m['g']:.1f} B={m['b']:.1f}  lit_px={m['lit']}/{m['n']}")
        return

    if not math.isfinite(a.series) or a.series <= 0:
        ap.error("--series must be a positive finite number of seconds")
    sample_frames = round(a.series * FPS)
    if sample_frames < 1:
        ap.error(f"--series must be long enough for at least one frame (>= {1 / FPS:.2f}s at {FPS} fps)")

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as d:
        grab(f"{d}/f%05d.jpg", WARMUP_FRAMES + sample_frames)
        files = sorted(Path(d).glob("f*.jpg"))[WARMUP_FRAMES:]
        if not files:
            raise SystemExit("observe.py: no frames captured after warmup")
        measurements = [measure(f) for f in files]
    elapsed = time.monotonic() - t0
    lit = [m["lit"] for m in measurements]
    w, h = measurements[0]["size"]
    # One character per frame, scaled to the brightest frame seen.
    ramp = " .:-=+*#%@"
    top = max(max(lit), 1)
    print("".join(ramp[min(v * len(ramp) // (top + 1), len(ramp) - 1)] for v in lit))
    print(f"frames={len(lit)} ({w}x{h}, {elapsed:.1f}s total incl. warmup) lit_px min={min(lit)} max={max(lit)}")


if __name__ == "__main__":
    main()
