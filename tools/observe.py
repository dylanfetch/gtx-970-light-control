#!/usr/bin/env python3
"""Watch the WINDFORCE logo through the webcam and measure it.

Used to verify, from the outside, that an LED command really changed the light.

  observe.py [out.jpg]          one frame: mean R G B of the logo region
  observe.py --series SECONDS   sample over time: prints how lit the logo is in
                                each frame, to see blinking or breathing
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

DEVICE = "/dev/video0"
FPS = 30  # what the camera delivers at its default mode
WARMUP_FRAMES = 20  # lets auto-exposure settle
# Logo bounding box in the 640x360 frame (x0, y0, x1, y1). Re-tune if the webcam moves.
ROI = (296, 166, 370, 196)


def grab(pattern: str, frames: int) -> None:
    """Capture `frames` frames; `pattern` is a multifilesink location (may contain %05d)."""
    subprocess.run(
        ["gst-launch-1.0", "-q", "v4l2src", f"device={DEVICE}", f"num-buffers={frames}",
         "!", "videoconvert", "!", "jpegenc", "!", "multifilesink", f"location={pattern}"],
        check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def measure(path: Path) -> dict:
    px = list(Image.open(path).convert("RGB").crop(ROI).getdata())
    n = len(px)
    mean = [sum(p[i] for p in px) / n for i in range(3)]
    # Count of clearly lit pixels: steadier than the mean, which the camera's
    # auto-exposure drags around.
    lit = sum(1 for p in px if max(p) > 150)
    return {"r": mean[0], "g": mean[1], "b": mean[2], "lit": lit, "n": n}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", type=Path, help="where to keep the frame (single-frame mode)")
    ap.add_argument("--series", type=float, metavar="SECONDS")
    a = ap.parse_args()

    if a.series is None:
        out = a.out or Path(tempfile.mkstemp(suffix=".jpg")[1])
        grab(str(out), WARMUP_FRAMES)  # same file each frame, so the last one wins
        m = measure(out)
        print(f"{out}  R={m['r']:.1f} G={m['g']:.1f} B={m['b']:.1f}  lit_px={m['lit']}/{m['n']}")
        return

    with tempfile.TemporaryDirectory() as d:
        grab(f"{d}/f%05d.jpg", WARMUP_FRAMES + round(a.series * FPS))
        lit = [measure(f)["lit"] for f in sorted(Path(d).glob("f*.jpg"))[WARMUP_FRAMES:]]
    # One character per frame, scaled to the brightest frame seen.
    ramp = " .:-=+*#%@"
    top = max(max(lit), 1)
    print("".join(ramp[min(v * len(ramp) // (top + 1), len(ramp) - 1)] for v in lit))
    print(f"frames={len(lit)} lit_px min={min(lit)} max={max(lit)}")


if __name__ == "__main__":
    main()
