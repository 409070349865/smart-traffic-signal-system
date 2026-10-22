"""
OpenCV Vehicle Detection & Motion Tracking Processor
────────────────────────────────────────────────────
Pipeline:
  1. Video frame extraction (from uploaded video or live demo)
  2. Spatial preprocessing: resize, grayscale, Gaussian blur
  3. MOG2 background subtraction with morphological opening/closing
  4. Contour extraction with area & aspect ratio filtering
  5. Centroid & motion vector tracking with velocity/direction estimation
  6. Approach/quadrant classification (North, South, East, West)
  7. High-visibility HUD overlay with bounding boxes, motion trails, speed tags,
     velocity vectors, and MOG2 foreground mask inset.
  8. Streaming via WebSocket as base64 JPEG + structured JSON telemetry.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────── Tuneable constants ─────────────────── #
MIN_CONTOUR_AREA   = 280      # px² – ignore small noise
MAX_CONTOUR_AREA   = 70_000   # px² – ignore oversized blobs (shadows)
TRACKER_MAX_DIST   = 75       # px  – max centroid distance to link tracks across frames
TRACKER_MAX_GONE   = 10       # frames a track can be absent before removal
FRAME_SKIP         = 1        # process every frame for silky motion tracking
STREAM_FPS_TARGET  = 16.0     # target streaming FPS for browser
PROCESS_WIDTH      = 640      # internal processing width
PROCESS_HEIGHT     = 360
OUTPUT_WIDTH       = 640      # frame size sent to the browser
OUTPUT_HEIGHT      = 360


# ─────────────────── Centroid & Motion Tracker ─────────────── #
@dataclass
class Track:
    tid:         int
    centroid:    Tuple[int, int]
    bbox:        Tuple[int, int, int, int]   # x, y, w, h
    history:     List[Tuple[int, int]] = field(default_factory=list)
    gone:        int = 0
    speed_px:    float = 0.0      # pixels per frame
    speed_kmh:   float = 0.0      # estimated km/h
    heading_deg: float = 0.0      # 0-360 degrees
    approach:    str = "NORTH"    # NORTH, SOUTH, EAST, WEST


class CentroidTracker:
    """Euclidean distance centroid tracker with trajectory history & velocity vectors."""

    def __init__(self) -> None:
        self._next_id = 1
        self.tracks: Dict[int, Track] = OrderedDict()

    def update(
        self, detections: List[Tuple[int, int, int, int]], fps: float = 16.0
    ) -> Dict[int, Track]:
        # Mark all existing as gone by default
        for t in self.tracks.values():
            t.gone += 1

        input_centroids = [
            (int(x + w / 2), int(y + h / 2)) for x, y, w, h in detections
        ]

        if not self.tracks:
            for i, (cx, cy) in enumerate(input_centroids):
                self._register(cx, cy, detections[i])
        else:
            track_ids   = list(self.tracks.keys())
            track_cents = [(t.centroid[0], t.centroid[1]) for t in self.tracks.values()]

            used_tracks = set()
            used_dets   = set()

            for di, (cx, cy) in enumerate(input_centroids):
                best_tid, best_dist = None, float("inf")
                best_ti = None
                for ti, tid in enumerate(track_ids):
                    if ti in used_tracks:
                        continue
                    tx, ty = track_cents[ti]
                    d = math.hypot(cx - tx, cy - ty)
                    if d < best_dist:
                        best_dist = d
                        best_tid  = tid
                        best_ti   = ti

                if best_tid is not None and best_dist < TRACKER_MAX_DIST:
                    t = self.tracks[best_tid]
                    t.history.append(t.centroid)
                    if len(t.history) > 14:
                        t.history.pop(0)

                    old_cx, old_cy = t.centroid
                    t.centroid = (cx, cy)
                    t.bbox     = detections[di]
                    t.gone     = 0

                    # Calculate instantaneous speed and heading
                    dx = cx - old_cx
                    dy = cy - old_cy
                    dist_px = math.hypot(dx, dy)
                    t.speed_px = 0.7 * t.speed_px + 0.3 * dist_px
                    # Calibration: approx 2.4 px/frame at 16fps corresponds to ~25-45 km/h
                    t.speed_kmh = round(t.speed_px * fps * 0.7, 1)

                    if dist_px > 1.0:
                        angle = math.degrees(math.atan2(dy, dx))
                        t.heading_deg = (angle + 360) % 360

                    used_tracks.add(best_ti)
                    used_dets.add(di)

            # Register unmatched new detections
            for di, bbox in enumerate(detections):
                if di not in used_dets:
                    cx, cy = input_centroids[di]
                    self._register(cx, cy, bbox)

        # Prune dead tracks
        stale = [tid for tid, t in self.tracks.items() if t.gone > TRACKER_MAX_GONE]
        for tid in stale:
            del self.tracks[tid]

        return {tid: t for tid, t in self.tracks.items() if t.gone == 0}

    def _register(self, cx: int, cy: int, bbox: Tuple[int, int, int, int]) -> None:
        t = Track(tid=self._next_id, centroid=(cx, cy), bbox=bbox)
        t.history.append((cx, cy))
        self.tracks[self._next_id] = t
        self._next_id += 1


# ─────────────────── Frame result container ─────────────────── #
@dataclass
class FrameResult:
    frame_idx:        int
    timestamp:        float
    vehicle_count:    int
    quadrant_counts:  Dict[str, int]   # NORTH, SOUTH, EAST, WEST
    tracks:           Dict[int, Track]
    tracks_data:      List[dict]
    jpeg_b64:         str              # base64-encoded annotated JPEG
    fps:              float


# ─────────────────── Video Processor ────────────────────────── #
class VideoProcessor:
    """Stateful OpenCV traffic video analysis pipeline."""

    def __init__(self) -> None:
        self._bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=250, varThreshold=45, detectShadows=True
        )
        self._tracker = CentroidTracker()
        self._running = False
        self._cap: Optional[cv2.VideoCapture] = None
        self.frame_idx = 0
        self._fps_acc: List[float] = []

    def stop(self) -> None:
        self._running = False
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    async def process_video(
        self,
        video_source: int | Path | str,
        on_frame=None,   # async callback(FrameResult)
        loop_video: bool = True,
    ) -> None:
        """Main OpenCV tracking loop. Loops video or streams live webcam until stopped."""
        self._running = True
        self._bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=250, varThreshold=45, detectShadows=True
        )
        self._tracker = CentroidTracker()
        self.frame_idx = 0

        is_cam = isinstance(video_source, int)
        cap = cv2.VideoCapture(video_source if is_cam else str(video_source))
        if not cap.isOpened():
            logger.error("Cannot open video source: %s", video_source)
            self._running = False
            return
        self._cap = cap

        frame_delay = 1.0 / STREAM_FPS_TARGET
        t_last = time.monotonic()
        skip_counter = 0

        source_label = f"Camera #{video_source}" if is_cam else Path(str(video_source)).name
        logger.info("OpenCV VideoProcessor running on %s", source_label)

        while self._running:
            ok, raw = cap.read()
            if not ok:
                if loop_video and not is_cam and self._running:
                    # Rewind to frame 0 for continuous live stream demo
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, raw = cap.read()
                    if not ok:
                        break
                else:
                    break

            skip_counter += 1
            if skip_counter % FRAME_SKIP != 0:
                await asyncio.sleep(0)
                continue

            self.frame_idx += 1

            # Real FPS tracking
            now = time.monotonic()
            elapsed = now - t_last
            t_last = now
            current_fps = 1.0 / max(elapsed, 0.001)
            self._fps_acc.append(current_fps)
            if len(self._fps_acc) > 15:
                self._fps_acc.pop(0)
            avg_fps = round(sum(self._fps_acc) / len(self._fps_acc), 1)

            # Process frame
            result = self._process_frame(raw, self.frame_idx, avg_fps)

            if on_frame:
                await on_frame(result)

            # Throttle to smooth target FPS
            sleep_time = max(0.005, frame_delay - (time.monotonic() - now))
            await asyncio.sleep(sleep_time)

        if self._cap:
            self._cap.release()
            self._cap = None
        self._running = False
        logger.info("Video processing stopped after %d frames.", self.frame_idx)

    def _process_frame(self, raw: np.ndarray, idx: int, current_fps: float) -> FrameResult:
        # 1. Resize to uniform processing size
        frame = cv2.resize(raw, (PROCESS_WIDTH, PROCESS_HEIGHT))

        # 2. Grayscale & Gaussian Blur
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        # 3. Background Subtraction
        fg_mask = self._bg_sub.apply(blur)
        # Remove shadows (MOG2 shadow pixels are gray = 127)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        # 4. Morphological noise removal & contour enhancement
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 4))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN,  kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, kernel, iterations=2)

        # 5. Find Contours
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        detections: List[Tuple[int, int, int, int]] = []
        for c in contours:
            area = cv2.contourArea(c)
            if MIN_CONTOUR_AREA <= area <= MAX_CONTOUR_AREA:
                x, y, w, h = cv2.boundingRect(c)
                aspect = w / max(h, 1)
                # Filter noise (extremely thin lines / flares)
                if 0.25 <= aspect <= 4.2:
                    detections.append((x, y, w, h))

        # 6. Centroid Tracking
        active_tracks = self._tracker.update(detections, fps=current_fps)

        # 7. Approach / Quadrant classification
        qcounts = self._classify_quadrants(active_tracks, PROCESS_WIDTH, PROCESS_HEIGHT)

        # 8. Render HUD annotations on frame
        annotated = self._annotate(frame.copy(), active_tracks, fg_mask, qcounts, current_fps)

        # 9. Encode to Base64 JPEG for browser WebSocket transmission
        _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")

        # Serialisable summary of tracks
        tracks_data = [
            {
                "id": t.tid,
                "x": t.centroid[0],
                "y": t.centroid[1],
                "w": t.bbox[2],
                "h": t.bbox[3],
                "speed_kmh": t.speed_kmh,
                "heading_deg": round(t.heading_deg, 1),
                "approach": t.approach,
            }
            for t in active_tracks.values()
        ]

        return FrameResult(
            frame_idx=idx,
            timestamp=time.time(),
            vehicle_count=len(active_tracks),
            quadrant_counts=qcounts,
            tracks=active_tracks,
            tracks_data=tracks_data,
            jpeg_b64=b64,
            fps=current_fps,
        )

    @staticmethod
    def _classify_quadrants(
        tracks: Dict[int, Track], W: int, H: int
    ) -> Dict[str, int]:
        """Classify each tracked vehicle into NORTH, SOUTH, EAST, or WEST approach."""
        counts = {"NORTH": 0, "SOUTH": 0, "EAST": 0, "WEST": 0}
        cx_mid, cy_mid = W // 2, H // 2

        for t in tracks.values():
            cx, cy = t.centroid
            dx = cx - cx_mid
            dy = cy - cy_mid

            if abs(dy) >= abs(dx):
                appr = "NORTH" if dy < 0 else "SOUTH"
            else:
                appr = "WEST" if dx < 0 else "EAST"

            t.approach = appr
            counts[appr] += 1

        return counts

    @staticmethod
    def _annotate(
        frame: np.ndarray,
        tracks: Dict[int, Track],
        mask: np.ndarray,
        qcounts: Dict[str, int],
        fps: float,
    ) -> np.ndarray:
        H, W = frame.shape[:2]
        cx_mid, cy_mid = W // 2, H // 2

        # ── Subtle approach division lines ──────────────────────── #
        overlay = frame.copy()
        cv2.line(overlay, (cx_mid, 0), (cx_mid, H), (40, 70, 110), 1)
        cv2.line(overlay, (0, cy_mid), (W, cy_mid), (40, 70, 110), 1)
        cv2.circle(overlay, (cx_mid, cy_mid), 24, (0, 229, 255), 1)
        cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

        # ── Color palette for tracked vehicles ──────────────────── #
        COLORS = [
            (0, 230, 118),   # Green
            (0, 229, 255),   # Cyan
            (124, 77, 255),  # Purple
            (255, 109, 0),   # Orange
            (255, 234, 0),   # Yellow
            (255, 64, 129),  # Pink
        ]

        # ── Draw each vehicle's tracking annotations ───────────── #
        for track in tracks.values():
            color = COLORS[track.tid % len(COLORS)]
            x, y, w, h = track.bbox

            # 1. Bounding box with corner accents
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 1, cv2.LINE_AA)
            corner_len = min(8, w // 3, h // 3)
            # Top-left corner
            cv2.line(frame, (x, y), (x + corner_len, y), color, 2)
            cv2.line(frame, (x, y), (x, y + corner_len), color, 2)
            # Bottom-right corner
            cv2.line(frame, (x + w, y + h), (x + w - corner_len, y + h), color, 2)
            cv2.line(frame, (x + w, y + h), (x + w, y + h - corner_len), color, 2)

            # 2. Motion Trail (fading historic trajectory points)
            pts = track.history[-8:]
            for i in range(1, len(pts)):
                alpha = (i / len(pts))
                tc = tuple(int(c * alpha) for c in color)
                cv2.line(frame, pts[i - 1], pts[i], tc, 2, cv2.LINE_AA)

            # 3. Centroid dot
            cv2.circle(frame, track.centroid, 3, color, -1, cv2.LINE_AA)
            cv2.circle(frame, track.centroid, 6, color, 1, cv2.LINE_AA)

            # 4. Motion Vector Arrow
            if len(track.history) >= 2:
                ox, oy = track.history[-2]
                cx2, cy2 = track.centroid
                dx, dy = cx2 - ox, cy2 - oy
                vlen = math.hypot(dx, dy)
                if vlen > 0.5:
                    scale = min(6.0, 15.0 / vlen)
                    ex = int(cx2 + dx * scale)
                    ey = int(cy2 + dy * scale)
                    cv2.arrowedLine(frame, (cx2, cy2), (ex, ey), color, 2, cv2.LINE_AA, tipLength=0.35)

            # 5. Floating ID & Speed Badge
            badge_text = f"#{track.tid} {track.speed_kmh:.0f}km/h"
            (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.36, 1)
            lx = max(2, min(W - tw - 8, x))
            ly = max(th + 6, y - 4)

            cv2.rectangle(frame, (lx - 2, ly - th - 3), (lx + tw + 4, ly + 2), (10, 15, 20), -1)
            cv2.rectangle(frame, (lx - 2, ly - th - 3), (lx + tw + 4, ly + 2), color, 1)
            cv2.putText(frame, badge_text, (lx, ly - 1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, color, 1, cv2.LINE_AA)

        # ── Foreground Mask PiP (Picture-in-Picture) Inset ──────── #
        inset_w, inset_h = 130, 75
        ix1, iy1 = W - inset_w - 8, H - inset_h - 8
        ix2, iy2 = W - 8, H - 8

        mask_rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mask_mini = cv2.resize(mask_rgb, (inset_w, inset_h))
        frame[iy1:iy2, ix1:ix2] = mask_mini

        cv2.rectangle(frame, (ix1, iy1), (ix2, iy2), (0, 229, 255), 1)
        cv2.putText(frame, "OPENCV MOG2", (ix1 + 6, iy1 + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 229, 255), 1, cv2.LINE_AA)

        # ── Approach counts HUD overlay ─────────────────────────── #
        font = cv2.FONT_HERSHEY_SIMPLEX
        _draw_hud_chip(frame, f"N: {qcounts['NORTH']}", (cx_mid - 25, 24), (0, 230, 118))
        _draw_hud_chip(frame, f"S: {qcounts['SOUTH']}", (cx_mid - 25, H - 14), (124, 77, 255))
        _draw_hud_chip(frame, f"E: {qcounts['EAST']}", (W - 75, cy_mid - 6), (0, 229, 255))
        _draw_hud_chip(frame, f"W: {qcounts['WEST']}", (12, cy_mid - 6), (255, 109, 0))

        # ── Top-Left Telemetry Banner ───────────────────────────── #
        tot = len(tracks)
        banner = f"OPENCV MOTION TRACKER | VEHICLES: {tot} | FPS: {fps:.1f}"
        (bw, bh), _ = cv2.getTextSize(banner, font, 0.38, 1)
        cv2.rectangle(frame, (8, 8), (bw + 20, bh + 16), (12, 16, 24), -1)
        cv2.rectangle(frame, (8, 8), (bw + 20, bh + 16), (0, 229, 255), 1)
        cv2.putText(frame, banner, (14, bh + 12), font, 0.38, (0, 229, 255), 1, cv2.LINE_AA)

        return frame


def _draw_hud_chip(img: np.ndarray, text: str, pos: Tuple[int, int], color: Tuple[int, int, int]):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, 0.38, 1)
    x, y = pos
    cv2.rectangle(img, (x - 4, y - th - 3), (x + tw + 4, y + 3), (12, 16, 22), -1)
    cv2.rectangle(img, (x - 4, y - th - 3), (x + tw + 4, y + 3), color, 1)
    cv2.putText(img, text, (x, y), font, 0.38, color, 1, cv2.LINE_AA)
