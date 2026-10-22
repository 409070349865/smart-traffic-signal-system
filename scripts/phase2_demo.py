"""Phase 2 demo: read a video source and print stats. Add --show for a preview window.

    python scripts/phase2_demo.py --source sample_data/synthetic_traffic.mp4
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from app.config import get_settings, setup_logging  # noqa: E402
from app.vision.video_input import ReadStatus, VideoInput, VideoSourceError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="Overrides VIDEO_SOURCE")
    ap.add_argument("--show", action="store_true", help="Show preview (needs a display)")
    args = ap.parse_args()

    settings = get_settings()
    setup_logging(settings.app.log_level)
    vcfg = settings.video.model_copy(update={"source": args.source or settings.video.source})

    try:
        vin = VideoInput.from_settings(vcfg)
        vin.open_video()
    except VideoSourceError as exc:
        print(f"ERROR: {exc}\nHint: put a video in sample_data/ or run "
              "scripts/generate_sample_video.py")
        return 1

    print(f"Resolution: {vin.get_resolution()}  FPS: {vin.get_fps():.1f}  "
          f"Frames: {vin.get_frame_count() or 'live/unknown'}")
    n, t0 = 0, time.time()
    try:
        while True:
            status, frame = vin.read_frame()
            if status is not ReadStatus.OK:
                print(f"Stopped: {status.value}")
                break
            n += 1
            if args.show:
                cv2.imshow("Phase 2 preview", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        vin.release()
        cv2.destroyAllWindows()
    print(f"Read {n} frames in {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
