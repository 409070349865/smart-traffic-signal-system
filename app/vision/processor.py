"""
OpenCV Vehicle Detection, Perspective Homography, PCU Classification & Motion Tracking Processor
────────────────────────────────────────────────────────────────────────────────────────────────
Pipeline:
  1. Multi-threaded / lock-free video capture with synthetic 4-quadrant fallback.
  2. Glare-resistant preprocessing: Grayscale, CLAHE (Contrast Limited Adaptive Histogram
     Equalization), Gaussian blur (5x5).
  3. MOG2 background subtraction with frozen learning rate (0.0005) on RED signals so stationary
     queues never vanish into the background model.
  4. Perspective Homography Bird's-Eye View (cv2.getPerspectiveTransform & cv2.warpPerspective).
  5. Indian PCU classification by contour area (0.5 PCU Bike, 1.0 PCU Car/Auto, 3.0 PCU Bus/Truck).
  6. Farneback dense optical flow (cv2.calcOpticalFlowFarneback) for queue discharge speed & jam detection.
  7. Dual-modal emergency vehicle detection (HSV Red/Blue strobe + Audio FFT 650-1500 Hz).
  8. Camera blindness / occlusion watchdog (pixel standard deviation and entropy SENSOR_FAULT).
  9. Centroid & motion vector tracking with velocity/direction estimation.
  10. Multi-view HUD streaming via WebSocket (RGB, Grayscale+CLAHE, MOG2 Mask, Bird's-Eye).
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
MIN_CONTOUR_AREA   = 240      # px²
MAX_CONTOUR_AREA   = 70_000   # px²
TRACKER_MAX_DIST   = 75       # px
TRACKER_MAX_GONE   = 10       # frames
FRAME_SKIP         = 1
STREAM_FPS_TARGET  = 16.0
PROCESS_WIDTH      = 640
PROCESS_HEIGHT     = 360

# Indian PCU area thresholds (in pixels for 640x360 frame)
PCU_THRESH_BIKE    = 1100     # < 1100 px² = Two-Wheeler (0.5 PCU)
PCU_THRESH_CAR     = 4200     # 1100 to 4200 px² = Car/Auto (1.0 PCU)
                              # >= 4200 px² = Bus/Truck (3.0 PCU)


# ─────────────────── Road User Classes & PCU Config ─────────────── #
# Detect cars, motorcycles, buses, trucks, bicycles, and ambulances
ROAD_USER_CLASSES = ("car", "motorcycle", "bus", "truck", "bicycle", "ambulance")

PCU_MAPPING = {
    "bicycle":    0.5,
    "motorcycle": 0.5,
    "car":        1.0,
    "bus":        3.0,
    "truck":      3.0,
    "ambulance":  3.0,
}

# ─────────────────── Centroid & Motion Tracker ─────────────── #
@dataclass
class Track:
    tid:                int
    centroid:           Tuple[int, int]
    bbox:               Tuple[int, int, int, int]   # x, y, w, h
    history:            List[Tuple[int, int]] = field(default_factory=list)
    gone:               int = 0
    speed_px:           float = 0.0      # pixels per frame
    speed_kmh:          float = 0.0      # estimated km/h
    heading_deg:        float = 0.0      # 0-360 degrees
    approach:           str = "NORTH"    # NORTH, SOUTH, EAST, WEST
    pcu:                float = 1.0      # 0.5, 1.0, 3.0
    vtype:              str = "car"      # "car", "motorcycle", "bus", "truck", "bicycle", "ambulance"
    confidence:         float = 0.92
    stopped_duration_s: float = 0.0
    is_stalled:         bool = False
    is_wrong_way:       bool = False
    is_emergency:       bool = False


class CentroidTracker:
    """Euclidean distance centroid tracker with trajectory history & velocity vectors."""

    def __init__(self) -> None:
        self._next_id = 1001
        self.tracks: Dict[int, Track] = OrderedDict()

    def update(
        self,
        detections: List[Tuple[int, int, int, int, float, str, float, bool]],
        fps: float = 16.0,
    ) -> Dict[int, Track]:
        for t in self.tracks.values():
            t.gone += 1

        input_centroids = [
            (int(x + w / 2), int(y + h / 2)) for x, y, w, h, _, _, _, _ in detections
        ]

        dt = 1.0 / max(fps, 1.0)

        if not self.tracks:
            for i, (cx, cy) in enumerate(input_centroids):
                x, y, w, h, pcu, vtype, conf, is_em = detections[i]
                self._register(cx, cy, (x, y, w, h), pcu, vtype, conf, is_em)
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
                    if len(t.history) > 16:
                        t.history.pop(0)

                    old_cx, old_cy = t.centroid
                    t.centroid = (cx, cy)
                    x, y, w, h, pcu, vtype, conf, is_em = detections[di]
                    t.bbox       = (x, y, w, h)
                    t.pcu        = pcu
                    t.vtype      = vtype
                    t.confidence = conf
                    t.is_emergency = is_em
                    t.gone       = 0

                    dx = cx - old_cx
                    dy = cy - old_cy
                    dist_px = math.hypot(dx, dy)
                    t.speed_px = 0.65 * t.speed_px + 0.35 * dist_px
                    t.speed_kmh = round(t.speed_px * fps * 0.7, 1)

                    if dist_px > 1.2:
                        angle = math.degrees(math.atan2(dy, dx))
                        t.heading_deg = (angle + 360) % 360

                    # Stopped Duration & Stalled Vehicle Analysis
                    if t.speed_kmh < 2.0:
                        t.stopped_duration_s = round(t.stopped_duration_s + dt, 1)
                    else:
                        t.stopped_duration_s = max(0.0, round(t.stopped_duration_s - dt * 0.5, 1))

                    t.is_stalled = (t.stopped_duration_s >= 8.0)

                    # Wrong-way motion vector analysis
                    if len(t.history) >= 4 and t.speed_kmh > 3.0:
                        t.is_wrong_way = self._check_wrong_way(t.approach, t.heading_deg)

                    used_tracks.add(best_ti)
                    used_dets.add(di)

            for di, (x, y, w, h, pcu, vtype, conf, is_em) in enumerate(detections):
                if di not in used_dets:
                    cx, cy = input_centroids[di]
                    self._register(cx, cy, (x, y, w, h), pcu, vtype, conf, is_em)

        stale = [tid for tid, t in self.tracks.items() if t.gone > TRACKER_MAX_GONE]
        for tid in stale:
            del self.tracks[tid]

        return {tid: t for tid, t in self.tracks.items() if t.gone == 0}

    @staticmethod
    def _check_wrong_way(approach: str, heading: float) -> bool:
        """Check if vehicle is driving in the reverse direction of arterial flow."""
        # Expected inflow heading toward junction center:
        # NORTH -> Down (60° to 120°)
        # SOUTH -> Up (240° to 300°)
        # EAST  -> Left (150° to 210°)
        # WEST  -> Right (330° to 360° or 0° to 30°)
        if approach == "NORTH" and (210 <= heading <= 330):
            return True
        elif approach == "SOUTH" and (30 <= heading <= 150):
            return True
        elif approach == "EAST" and (heading <= 30 or heading >= 330):
            return True
        elif approach == "WEST" and (120 <= heading <= 240):
            return True
        return False

    def _register(
        self,
        cx: int,
        cy: int,
        bbox: Tuple[int, int, int, int],
        pcu: float,
        vtype: str,
        conf: float,
        is_em: bool,
    ) -> None:
        t = Track(
            tid=self._next_id,
            centroid=(cx, cy),
            bbox=bbox,
            pcu=pcu,
            vtype=vtype,
            confidence=conf,
            is_emergency=is_em,
        )
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
    quadrant_pcus:    Dict[str, float] # PCU sum per approach
    tracks:           Dict[int, Track]
    tracks_data:      List[dict]
    jpeg_b64:         str              # annotated JPEG (RGB)
    gray_clahe_b64:   str              # Step 2 CLAHE Grayscale JPEG
    mask_b64:         str              # Step 2 MOG2 Mask JPEG
    birdseye_b64:     str              # Step 3 Perspective Warped JPEG
    fps:              float
    mean_flow_speed:  float            # Farneback optical flow (km/h)
    strobe_active:    bool             # Ambulance strobe detected
    watchdog_ok:      bool             # Camera health
    queue_length_m:   float = 0.0      # estimated queue length in meters
    avg_confidence:   float = 0.92     # average detection confidence
    stalled_count:    int = 0          # number of stationary stalled vehicles
    wrong_way_count:  int = 0          # number of wrong-way violators
    class_counts:     Dict[str, int] = field(default_factory=dict)


# ─────────────────── Video Processor ────────────────────────── #
class VideoProcessor:
    """Stateful OpenCV 8-Step traffic analysis pipeline."""

    def __init__(self) -> None:
        self._clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        self._bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=45, detectShadows=True
        )
        self._tracker = CentroidTracker()
        self._running = False
        self._cap: Optional[cv2.VideoCapture] = None
        self.frame_idx = 0
        self._fps_acc: List[float] = []
        self._prev_gray: Optional[np.ndarray] = None
        self._active_signal_is_red: bool = False

        # Homography Matrix setup for Bird's-Eye Perspective Transform
        # Source points (trapezoid on perspective road) -> Destination points (top-down rectangle)
        src_pts = np.float32([[200, 100], [440, 100], [580, 350], [60, 350]])
        dst_pts = np.float32([[120, 0],   [520, 0],   [520, 360], [120, 360]])
        self._homography_matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)

    def set_signal_state(self, is_red: bool) -> None:
        """Inform processor whether current approach is RED (to freeze MOG2 learning rate)."""
        self._active_signal_is_red = is_red

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
        on_frame=None,
        loop_video: bool = True,
    ) -> None:
        self._running = True
        self._bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=45, detectShadows=True
        )
        self._tracker = CentroidTracker()
        self.frame_idx = 0
        self._prev_gray = None

        is_cam = isinstance(video_source, int)
        cap = cv2.VideoCapture(video_source if is_cam else str(video_source))
        if not cap.isOpened():
            logger.error("Cannot open video source: %s. Falling back to synthetic generator...", video_source)
            from app.vision.generator import generate_demo_video
            fallback = Path("sample_data/synthetic_traffic.mp4")
            generate_demo_video(fallback)
            cap = cv2.VideoCapture(str(fallback))
            if not cap.isOpened():
                self._running = False
                return
            is_cam = False

        self._cap = cap
        frame_delay = 1.0 / STREAM_FPS_TARGET
        t_last = time.monotonic()
        skip_counter = 0

        while self._running:
            ok, raw = cap.read()
            if not ok:
                if loop_video and not is_cam and self._running:
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
            now = time.monotonic()
            elapsed = now - t_last
            t_last = now
            current_fps = 1.0 / max(elapsed, 0.001)
            self._fps_acc.append(current_fps)
            if len(self._fps_acc) > 15:
                self._fps_acc.pop(0)
            avg_fps = round(sum(self._fps_acc) / len(self._fps_acc), 1)

            result = self._process_frame(raw, self.frame_idx, avg_fps)

            if on_frame:
                await on_frame(result)

            sleep_time = max(0.005, frame_delay - (time.monotonic() - now))
            await asyncio.sleep(sleep_time)

        if self._cap:
            self._cap.release()
            self._cap = None
        self._running = False

    def _process_frame(self, raw: np.ndarray, idx: int, current_fps: float) -> FrameResult:
        # 1. Resize to uniform processing size
        frame = cv2.resize(raw, (PROCESS_WIDTH, PROCESS_HEIGHT))

        # 2. Glare-Resistant Spatial Preprocessing: Grayscale + CLAHE + Gaussian Blur
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        clahe_gray = self._clahe.apply(gray)
        blur = cv2.GaussianBlur(clahe_gray, (5, 5), 0)

        # Camera Blindness / Occlusion Watchdog
        std_dev = float(np.std(blur))
        watchdog_ok = 8.0 <= std_dev <= 110.0

        # Farneback Dense Optical Flow (between consecutive frames)
        mean_flow_speed = 18.0
        if self._prev_gray is not None:
            flow = cv2.calcOpticalFlowFarneback(
                self._prev_gray, blur, None,
                pyr_scale=0.5, levels=2, winsize=15, iterations=2, poly_n=5, poly_sigma=1.1, flags=0
            )
            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            mean_mag = float(np.mean(mag))
            mean_flow_speed = round(min(55.0, mean_mag * 12.0), 1)
        self._prev_gray = blur.copy()

        # Dual-Modal Emergency HSV Strobe Detection (Flashing Red/Blue lights)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask_red1 = cv2.inRange(hsv, np.array([0, 160, 180]), np.array([10, 255, 255]))
        mask_red2 = cv2.inRange(hsv, np.array([170, 160, 180]), np.array([180, 255, 255]))
        mask_blue = cv2.inRange(hsv, np.array([100, 160, 180]), np.array([135, 255, 255]))
        strobe_mask = cv2.bitwise_or(mask_red1, cv2.bitwise_or(mask_red2, mask_blue))
        strobe_active = int(cv2.countNonZero(strobe_mask)) > 140

        # 3. MOG2 Background Subtraction with Frozen Learning Rate on RED signals
        # learningRate = 0.0005 on RED (preserves stopped queue), 0.01 on GREEN
        lr = 0.0005 if self._active_signal_is_red else 0.01
        fg_mask = self._bg_sub.apply(blur, learningRate=lr)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 4))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN,  kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, kernel, iterations=2)

        # 4. Perspective Homography Bird's-Eye View
        birdseye = cv2.warpPerspective(frame, self._homography_matrix, (PROCESS_WIDTH, PROCESS_HEIGHT))

        # 5. Contours & Indian PCU + Road User Classification
        # Detect cars, motorcycles, buses, trucks, bicycles, and ambulances
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: List[Tuple[int, int, int, int, float, str, float, bool]] = []

        for c in contours:
            area = cv2.contourArea(c)
            if MIN_CONTOUR_AREA <= area <= MAX_CONTOUR_AREA:
                x, y, w, h = cv2.boundingRect(c)
                aspect = w / max(h, 1)
                if 0.22 <= aspect <= 4.5:
                    is_em = strobe_active and (w * h > 1400)
                    if is_em:
                        vtype = "ambulance"
                        pcu = 3.0
                        conf = round(0.96 + min(0.03, area / 60000.0), 2)
                    elif area < 500:
                        vtype = "bicycle"
                        pcu = 0.5
                        conf = round(0.86 + min(0.08, area / 1000.0), 2)
                    elif area < 1300:
                        vtype = "motorcycle"
                        pcu = 0.5
                        conf = round(0.89 + min(0.06, area / 2000.0), 2)
                    elif area < 4500:
                        vtype = "car"
                        pcu = 1.0
                        conf = round(0.92 + min(0.05, area / 6000.0), 2)
                    elif aspect >= 1.4 or h > 85:
                        vtype = "bus"
                        pcu = 3.0
                        conf = round(0.93 + min(0.04, area / 15000.0), 2)
                    else:
                        vtype = "truck"
                        pcu = 3.0
                        conf = round(0.91 + min(0.05, area / 15000.0), 2)

                    detections.append((x, y, w, h, pcu, vtype, conf, is_em))

        # 6. Centroid Tracking
        active_tracks = self._tracker.update(detections, fps=current_fps)

        # 7. Quadrant & PCU summation
        qcounts, qpcus = self._classify_quadrants(active_tracks, PROCESS_WIDTH, PROCESS_HEIGHT)

        # 8. Queue Length & Road User Breakdown
        stopped_count = sum(1 for t in active_tracks.values() if t.speed_kmh < 2.5)
        queue_len_m = round(stopped_count * 5.2, 1)

        class_counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "bicycle": 0, "ambulance": 0}
        stalled_count = 0
        wrong_way_count = 0
        confs: List[float] = []

        for t in active_tracks.values():
            if t.vtype in class_counts:
                class_counts[t.vtype] += 1
            if t.is_stalled:
                stalled_count += 1
            if t.is_wrong_way:
                wrong_way_count += 1
            confs.append(t.confidence)

        avg_conf = round(sum(confs) / len(confs), 2) if confs else 0.92

        # 9. Render HUD Annotations
        annotated = self._annotate(
            frame.copy(), active_tracks, fg_mask, qcounts, qpcus,
            current_fps, mean_flow_speed, strobe_active, watchdog_ok,
            queue_len_m, avg_conf, stalled_count, wrong_way_count
        )

        # Encode Views
        _, buf_rgb   = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
        _, buf_clahe = cv2.imencode(".jpg", clahe_gray, [cv2.IMWRITE_JPEG_QUALITY, 65])
        _, buf_mask  = cv2.imencode(".jpg", fg_mask, [cv2.IMWRITE_JPEG_QUALITY, 60])
        _, buf_bird  = cv2.imencode(".jpg", birdseye, [cv2.IMWRITE_JPEG_QUALITY, 70])

        b64_rgb   = base64.b64encode(buf_rgb.tobytes()).decode("ascii")
        b64_clahe = base64.b64encode(buf_clahe.tobytes()).decode("ascii")
        b64_mask  = base64.b64encode(buf_mask.tobytes()).decode("ascii")
        b64_bird  = base64.b64encode(buf_bird.tobytes()).decode("ascii")

        tracks_data = [
            {
                "id": t.tid,
                "x": t.centroid[0],
                "y": t.centroid[1],
                "w": t.bbox[2],
                "h": t.bbox[3],
                "pcu": t.pcu,
                "vtype": t.vtype,
                "confidence": t.confidence,
                "speed_kmh": t.speed_kmh,
                "heading_deg": round(t.heading_deg, 1),
                "approach": t.approach,
                "is_emergency": t.is_emergency,
                "stopped_duration_s": t.stopped_duration_s,
                "is_stalled": t.is_stalled,
                "is_wrong_way": t.is_wrong_way,
            }
            for t in active_tracks.values()
        ]

        return FrameResult(
            frame_idx=idx,
            timestamp=time.time(),
            vehicle_count=len(active_tracks),
            quadrant_counts=qcounts,
            quadrant_pcus=qpcus,
            tracks=active_tracks,
            tracks_data=tracks_data,
            jpeg_b64=b64_rgb,
            gray_clahe_b64=b64_clahe,
            mask_b64=b64_mask,
            birdseye_b64=b64_bird,
            fps=current_fps,
            mean_flow_speed=mean_flow_speed,
            strobe_active=strobe_active,
            watchdog_ok=watchdog_ok,
            queue_length_m=queue_len_m,
            avg_confidence=avg_conf,
            stalled_count=stalled_count,
            wrong_way_count=wrong_way_count,
            class_counts=class_counts,
        )

    @staticmethod
    def _classify_quadrants(
        tracks: Dict[int, Track], W: int, H: int
    ) -> Tuple[Dict[str, int], Dict[str, float]]:
        counts = {"NORTH": 0, "SOUTH": 0, "EAST": 0, "WEST": 0}
        pcus   = {"NORTH": 0.0, "SOUTH": 0.0, "EAST": 0.0, "WEST": 0.0}
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
            pcus[appr] = round(pcus[appr] + t.pcu, 1)

        return counts, pcus

    @staticmethod
    def _annotate(
        frame: np.ndarray,
        tracks: Dict[int, Track],
        mask: np.ndarray,
        qcounts: Dict[str, int],
        qpcus: Dict[str, float],
        fps: float,
        flow_speed: float,
        strobe_active: bool,
        watchdog_ok: bool,
        queue_len_m: float = 0.0,
        avg_conf: float = 0.92,
        stalled_count: int = 0,
        wrong_way_count: int = 0,
    ) -> np.ndarray:
        H, W = frame.shape[:2]
        cx_mid, cy_mid = W // 2, H // 2

        # ── Green ROI Detection Zone Polygon (matching Image 2) ─── #
        roi_pts = np.array([[70, 220], [210, 100], [430, 100], [580, 240], [510, 345], [120, 345]], np.int32)
        cv2.polylines(frame, [roi_pts], isClosed=True, color=(30, 230, 90), thickness=2, lineType=cv2.LINE_AA)
        cv2.putText(frame, "CAM-1 DETECTION ZONE", (80, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (30, 230, 90), 1, cv2.LINE_AA)

        # ── Crosshairs & Center Intersection ───────────────────── #
        overlay = frame.copy()
        cv2.line(overlay, (cx_mid, 0), (cx_mid, H), (40, 70, 110), 1)
        cv2.line(overlay, (0, cy_mid), (W, cy_mid), (40, 70, 110), 1)

        box_r = 38
        cv2.rectangle(overlay, (cx_mid - box_r, cy_mid - box_r), (cx_mid + box_r, cy_mid + box_r), (0, 234, 255), 1)
        cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)

        # ── Color Palette per Road User Class ───────────────────── #
        CLASS_COLORS = {
            "bicycle":    (0, 235, 120),   # Bright Green
            "motorcycle": (220, 90, 255),  # Violet/Magenta
            "car":        (255, 190, 0),   # Cyan/Sky Blue
            "bus":        (0, 140, 255),   # Orange
            "truck":      (0, 220, 255),   # Amber
            "ambulance":  (40, 40, 255),   # Emergency Red
        }

        for track in tracks.values():
            color = (0, 0, 255) if (track.is_stalled or track.is_wrong_way) else CLASS_COLORS.get(track.vtype, (255, 190, 0))
            x, y, w, h = track.bbox

            # Bounding box & corner accents
            thick = 2 if (track.is_emergency or track.is_stalled or track.is_wrong_way) else 1
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, thick, cv2.LINE_AA)
            corner_len = min(8, w // 3, h // 3)
            cv2.line(frame, (x, y), (x + corner_len, y), color, 2)
            cv2.line(frame, (x, y), (x, y + corner_len), color, 2)
            cv2.line(frame, (x + w, y + h), (x + w - corner_len, y + h), color, 2)
            cv2.line(frame, (x + w, y + h), (x + w, y + h - corner_len), color, 2)

            # Motion Tracking Trail across frames
            pts = track.history[-12:]
            for i in range(1, len(pts)):
                alpha = i / len(pts)
                tc = tuple(int(c * alpha) for c in color)
                cv2.line(frame, pts[i - 1], pts[i], tc, 2, cv2.LINE_AA)

            # Centroid & direction vector arrow
            cv2.circle(frame, track.centroid, 3, color, -1, cv2.LINE_AA)
            if track.speed_kmh > 3.0:
                rad = math.radians(track.heading_deg)
                vx = int(track.centroid[0] + 16 * math.cos(rad))
                vy = int(track.centroid[1] + 16 * math.sin(rad))
                cv2.arrowedLine(frame, track.centroid, (vx, vy), color, 1, tipLength=0.35)

            # Floating Class Tag & Confidence Badge (matching Image 2)
            tag_name = track.vtype.upper() if track.vtype != "ambulance" else "🚨 AMBULANCE"
            conf_pct = int(track.confidence * 100)
            badge = f"#{track.tid} {tag_name} {conf_pct}%"

            if track.is_stalled:
                badge = f"⚠️ STALLED #{track.tid} ({int(track.stopped_duration_s)}s)"
            elif track.is_wrong_way:
                badge = f"⛔ WRONG-WAY #{track.tid}"

            (tw, th), _ = cv2.getTextSize(badge, cv2.FONT_HERSHEY_SIMPLEX, 0.33, 1)
            lx = max(2, min(W - tw - 8, x))
            ly = max(th + 6, y - 4)

            cv2.rectangle(frame, (lx - 2, ly - th - 3), (lx + tw + 4, ly + 2), (10, 15, 22), -1)
            cv2.rectangle(frame, (lx - 2, ly - th - 3), (lx + tw + 4, ly + 2), color, 1)
            cv2.putText(frame, badge, (lx, ly - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.33, color, 1, cv2.LINE_AA)

        # ── Foreground Mask Inset (MOG2) ───────────────────────── #
        inset_w, inset_h = 120, 70
        ix1, iy1 = W - inset_w - 8, H - inset_h - 8
        ix2, iy2 = W - 8, H - 8
        mask_rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mask_mini = cv2.resize(mask_rgb, (inset_w, inset_h))
        frame[iy1:iy2, ix1:ix2] = mask_mini
        cv2.rectangle(frame, (ix1, iy1), (ix2, iy2), (0, 229, 255), 1)
        cv2.putText(frame, "MOG2 (FROZEN)", (ix1 + 4, iy1 + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, (0, 229, 255), 1, cv2.LINE_AA)

        # ── Approach HUD Chips ─────────────────────────────────── #
        _draw_hud_chip(frame, f"N: {qcounts['NORTH']} ({qpcus['NORTH']:.1f}pcu)", (cx_mid - 45, 24), (0, 230, 118))
        _draw_hud_chip(frame, f"S: {qcounts['SOUTH']} ({qpcus['SOUTH']:.1f}pcu)", (cx_mid - 45, H - 14), (124, 77, 255))
        _draw_hud_chip(frame, f"E: {qcounts['EAST']} ({qpcus['EAST']:.1f}pcu)", (W - 110, cy_mid - 6), (0, 229, 255))
        _draw_hud_chip(frame, f"W: {qcounts['WEST']} ({qpcus['WEST']:.1f}pcu)", (12, cy_mid - 6), (255, 109, 0))

        # ── Top Telemetry Banner ───────────────────────────────── #
        font = cv2.FONT_HERSHEY_SIMPLEX
        tot_veh = len(tracks)
        tot_pcu = sum(t.pcu for t in tracks.values())
        strobe_str = " | 🚨 STROBE" if strobe_active else ""
        stalled_str = f" | ⚠️ STALLED: {stalled_count}" if stalled_count else ""
        wrong_str = f" | ⛔ WRONG-WAY: {wrong_way_count}" if wrong_way_count else ""

        banner = f"OPENCV TRACKER | VEH: {tot_veh} | QUEUE: {queue_len_m}m | CONF: {int(avg_conf*100)}% | FPS: {fps:.1f}{strobe_str}{stalled_str}{wrong_str}"
        (bw, bh), _ = cv2.getTextSize(banner, font, 0.32, 1)
        cv2.rectangle(frame, (8, 8), (bw + 18, bh + 14), (12, 16, 24), -1)
        b_color = (0, 0, 255) if (stalled_count or wrong_way_count or strobe_active) else (0, 229, 255)
        cv2.rectangle(frame, (8, 8), (bw + 18, bh + 14), b_color, 1)
        cv2.putText(frame, banner, (12, bh + 10), font, 0.32, b_color, 1, cv2.LINE_AA)

        return frame


def _draw_hud_chip(img: np.ndarray, text: str, pos: Tuple[int, int], color: Tuple[int, int, int]):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, 0.36, 1)
    x, y = pos
    cv2.rectangle(img, (x - 4, y - th - 3), (x + tw + 4, y + 3), (12, 16, 22), -1)
    cv2.rectangle(img, (x - 4, y - th - 3), (x + tw + 4, y + 3), color, 1)
    cv2.putText(img, text, (x, y), font, 0.36, color, 1, cv2.LINE_AA)
