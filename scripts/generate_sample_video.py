"""Generate a synthetic 4-way junction clip for smoke-testing (not real traffic)."""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np


def generate(out: Path, width: int = 640, height: int = 360, fps: int = 20,
             seconds: int = 20, seed: int = 42) -> Path:
    rng = random.Random(seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("Could not create video writer (codec unavailable).")

    cx, cy, rw = width // 2, height // 2, 60
    # Each vehicle: [x, y, dx, dy, w, h, colour]
    vehicles: list[list[float]] = []
    for frame_idx in range(fps * seconds):
        img = np.full((height, width, 3), 60, np.uint8)
        cv2.rectangle(img, (cx - rw // 2, 0), (cx + rw // 2, height), (90, 90, 90), -1)
        cv2.rectangle(img, (0, cy - rw // 2), (width, cy + rw // 2), (90, 90, 90), -1)

        if rng.random() < 0.12:
            side = rng.choice("NSEW")
            col = tuple(rng.randint(120, 255) for _ in range(3))
            v = {"N": [cx - 15, -30, 0, 3, 24, 36], "S": [cx + 5, height, 0, -3, 24, 36],
                 "W": [-40, cy + 5, 3, 0, 36, 24], "E": [width, cy - 15, -3, 0, 36, 24]}[side]
            vehicles.append(v + [col])

        for v in vehicles:
            v[0] += v[2]
            v[1] += v[3]
            x, y, w, h = int(v[0]), int(v[1]), int(v[4]), int(v[5])
            cv2.rectangle(img, (x, y), (x + w, y + h), v[6], -1)
        vehicles = [v for v in vehicles if -60 < v[0] < width + 60 and -60 < v[1] < height + 60]
        writer.write(img)
    writer.release()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="sample_data/synthetic_traffic.mp4")
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args()
    path = generate(Path(args.out), seconds=args.seconds)
    print(f"Wrote {path} ({path.stat().st_size / 1024:.0f} KiB)")
