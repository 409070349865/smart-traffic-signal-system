"""
Synthetic Traffic Video Generator for OpenCV Motion Tracking & Vehicle Detection.
Generates a realistic 4-way intersection traffic clip with moving Two-Wheelers, Cars,
Buses, and emergency Ambulances with flashing strobes for testing the 8-Step pipeline.
"""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import List

import cv2
import numpy as np


class SimulatedVehicle:
    def __init__(
        self,
        vid: int,
        direction: str,
        x: float,
        y: float,
        speed: float,
        color: tuple[int, int, int],
        vtype: str = "car",
    ):
        self.vid = vid
        self.direction = direction   # "N2S", "S2N", "W2E", "E2W"
        self.x = x
        self.y = y
        self.speed = speed
        self.color = color
        self.vtype = vtype

        if vtype == "bike":
            self.w, self.h = 14, 22
        elif vtype == "bus":
            self.w, self.h = 28, 54
        elif vtype == "ambulance":
            self.w, self.h = 24, 46
        else: # car
            self.w, self.h = 22, 38

        if direction in ("W2E", "E2W"):
            self.w, self.h = self.h, self.w

    def step(self):
        if self.direction == "N2S":
            self.y += self.speed
        elif self.direction == "S2N":
            self.y -= self.speed
        elif self.direction == "W2E":
            self.x += self.speed
        elif self.direction == "E2W":
            self.x -= self.speed

    def is_offscreen(self, W: int, H: int) -> bool:
        return self.x < -80 or self.x > W + 80 or self.y < -80 or self.y > H + 80

    def draw(self, frame: np.ndarray, frame_idx: int):
        x, y = int(self.x), int(self.y)
        w, h = self.w, self.h
        x1, y1 = x - w // 2, y - h // 2
        x2, y2 = x + w // 2, y + h // 2

        # Vehicle body shadow
        cv2.rectangle(frame, (x1 + 3, y1 + 3), (x2 + 3, y2 + 3), (25, 28, 32), -1)

        # Vehicle body
        cv2.rectangle(frame, (x1, y1), (x2, y2), self.color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), 1)

        # Windshield / details
        if self.vtype == "ambulance":
            # Red cross insignia
            cv2.rectangle(frame, (x - 2, y - 6), (x + 2, y + 6), (0, 0, 255), -1)
            cv2.rectangle(frame, (x - 6, y - 2), (x + 6, y + 2), (0, 0, 255), -1)

            # High-intensity flashing strobe light (alternating Red / Blue)
            flash_col = (0, 0, 255) if (frame_idx // 4) % 2 == 0 else (255, 0, 0)
            cv2.circle(frame, (x, y1 + 4), 4, flash_col, -1)
            cv2.circle(frame, (x, y1 + 4), 8, flash_col, 1)
        elif self.direction in ("N2S", "S2N"):
            wy = y - 2 if self.direction == "N2S" else y + 2
            cv2.rectangle(frame, (x1 + 2, wy - 3), (x2 - 2, wy + 3), (60, 80, 100), -1)
            hl_y = y2 - 1 if self.direction == "N2S" else y1 + 1
            cv2.circle(frame, (x1 + 3, hl_y), 2, (200, 255, 255), -1)
            cv2.circle(frame, (x2 - 3, hl_y), 2, (200, 255, 255), -1)
        else:
            wx = x + 2 if self.direction == "W2E" else x - 2
            cv2.rectangle(frame, (wx - 3, y1 + 2), (wx + 3, y2 - 2), (60, 80, 100), -1)
            hl_x = x2 - 1 if self.direction == "W2E" else x1 + 1
            cv2.circle(frame, (hl_x, y1 + 3), 2, (200, 255, 255), -1)
            cv2.circle(frame, (hl_x, y2 - 3), 2, (200, 255, 255), -1)


def generate_demo_video(output_path: Path, num_frames: int = 360, fps: int = 24) -> Path:
    """Generate a realistic 640x360 intersection video with multiple vehicle classes."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    W, H = 640, 360
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(output_path), fourcc, fps, (W, H))

    if not out.isOpened():
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        out = cv2.VideoWriter(str(output_path), fourcc, fps, (W, H))

    cx_mid, cy_mid = W // 2, H // 2
    road_w = 110

    base_bg = np.zeros((H, W, 3), dtype=np.uint8)
    base_bg[:] = (45, 50, 48)  # Grass

    # Roads (Asphalt)
    asphalt = (38, 40, 44)
    cv2.rectangle(base_bg, (cx_mid - road_w // 2, 0), (cx_mid + road_w // 2, H), asphalt, -1)
    cv2.rectangle(base_bg, (0, cy_mid - road_w // 2), (W, cy_mid + road_w // 2), asphalt, -1)

    # Curbs
    curb = (80, 85, 90)
    lw = 2
    cv2.line(base_bg, (cx_mid - road_w // 2, 0), (cx_mid - road_w // 2, cy_mid - road_w // 2), curb, lw)
    cv2.line(base_bg, (cx_mid + road_w // 2, 0), (cx_mid + road_w // 2, cy_mid - road_w // 2), curb, lw)
    cv2.line(base_bg, (cx_mid - road_w // 2, cy_mid + road_w // 2), (cx_mid - road_w // 2, H), curb, lw)
    cv2.line(base_bg, (cx_mid + road_w // 2, cy_mid + road_w // 2), (cx_mid + road_w // 2, H), curb, lw)

    cv2.line(base_bg, (0, cy_mid - road_w // 2), (cx_mid - road_w // 2, cy_mid - road_w // 2), curb, lw)
    cv2.line(base_bg, (0, cy_mid + road_w // 2), (cx_mid - road_w // 2, cy_mid + road_w // 2), curb, lw)
    cv2.line(base_bg, (cx_mid + road_w // 2, cy_mid - road_w // 2), (W, cy_mid - road_w // 2), curb, lw)
    cv2.line(base_bg, (cx_mid + road_w // 2, cy_mid + road_w // 2), (W, cy_mid + road_w // 2), curb, lw)

    # Yellow dividing lines
    yellow = (0, 215, 255)
    for y in range(0, cy_mid - road_w // 2, 24):
        cv2.line(base_bg, (cx_mid, y), (cx_mid, y + 14), yellow, 2)
    for y in range(cy_mid + road_w // 2, H, 24):
        cv2.line(base_bg, (cx_mid, y), (cx_mid, y + 14), yellow, 2)
    for x in range(0, cx_mid - road_w // 2, 24):
        cv2.line(base_bg, (x, cy_mid), (x + 14, cy_mid), yellow, 2)
    for x in range(cx_mid + road_w // 2, W, 24):
        cv2.line(base_bg, (x, cy_mid), (x + 14, cy_mid), yellow, 2)

    # Zebra crosswalks
    white = (200, 205, 210)
    for i in range(-road_w // 2 + 6, road_w // 2 - 6, 12):
        cv2.rectangle(base_bg, (cx_mid + i, cy_mid - road_w // 2 - 12), (cx_mid + i + 7, cy_mid - road_w // 2 - 2), white, -1)
        cv2.rectangle(base_bg, (cx_mid + i, cy_mid + road_w // 2 + 2), (cx_mid + i + 7, cy_mid + road_w // 2 + 12), white, -1)
        cv2.rectangle(base_bg, (cx_mid - road_w // 2 - 12, cy_mid + i), (cx_mid - road_w // 2 - 2, cy_mid + i + 7), white, -1)
        cv2.rectangle(base_bg, (cx_mid + road_w // 2 + 2, cy_mid + i), (cx_mid + road_w // 2 + 12, cy_mid + i + 7), white, -1)

    # Central Yellow-Box Gridlock Hatching
    box_s = 40
    cv2.rectangle(base_bg, (cx_mid - box_s, cy_mid - box_s), (cx_mid + box_s, cy_mid + box_s), (0, 200, 230), 1)
    cv2.line(base_bg, (cx_mid - box_s, cy_mid - box_s), (cx_mid + box_s, cy_mid + box_s), (0, 180, 210), 1)
    cv2.line(base_bg, (cx_mid - box_s, cy_mid + box_s), (cx_mid + box_s, cy_mid - box_s), (0, 180, 210), 1)

    # Vehicles pool
    vehicles: List[SimulatedVehicle] = []
    vid_counter = 1

    CAR_COLORS = [
        (220, 50, 40), (50, 140, 230), (230, 230, 235),
        (40, 200, 160), (230, 180, 40), (130, 80, 220), (90, 95, 100),
    ]

    lanes = {
        "N2S": (cx_mid + road_w // 4, -40, 0, 1),
        "S2N": (cx_mid - road_w // 4, H + 40, 0, -1),
        "W2E": (-40, cy_mid + road_w // 4, 1, 0),
        "E2W": (W + 40, cy_mid - road_w // 4, -1, 0),
    }

    # Initial vehicles
    init_configs = [
        ("N2S", cx_mid + road_w // 4, 60, 2.8, CAR_COLORS[0], "car"),
        ("N2S", cx_mid + road_w // 4, -30, 3.2, (20, 220, 180), "bike"),
        ("S2N", cx_mid - road_w // 4, 280, 2.5, CAR_COLORS[2], "bus"),
        ("W2E", 140, cy_mid + road_w // 4, 3.0, CAR_COLORS[3], "car"),
        ("W2E", -20, cy_mid + road_w // 4, 3.5, (240, 240, 240), "ambulance"),
        ("E2W", 480, cy_mid - road_w // 4, 2.6, CAR_COLORS[5], "car"),
        ("E2W", 590, cy_mid - road_w // 4, 3.1, (20, 220, 180), "bike"),
    ]
    for d, x, y, spd, col, vt in init_configs:
        vehicles.append(SimulatedVehicle(vid_counter, d, x, y, spd, col, vt))
        vid_counter += 1

    spawn_timer = 0
    directions_pool = ["N2S", "S2N", "W2E", "E2W"]

    for frame_idx in range(num_frames):
        frame = base_bg.copy()

        spawn_timer += 1
        if spawn_timer > 28 and len(vehicles) < 14:
            spawn_timer = 0
            d = random.choice(directions_pool)
            base_x, base_y, _, _ = lanes[d]
            spd = random.uniform(2.2, 3.8)
            col = random.choice(CAR_COLORS)
            # Pick vehicle type with realistic PCU distribution
            rand_val = random.random()
            if rand_val < 0.25:
                vt = "bike"
                col = (30, 220, 180)
            elif rand_val < 0.40:
                vt = "bus"
                col = (230, 120, 30)
            elif rand_val < 0.45:
                vt = "ambulance"
                col = (245, 245, 245)
            else:
                vt = "car"

            vehicles.append(SimulatedVehicle(vid_counter, d, base_x, base_y, spd, col, vt))
            vid_counter += 1

        for v in vehicles:
            v.step()
            v.draw(frame, frame_idx)

        vehicles = [v for v in vehicles if not v.is_offscreen(W, H)]
        out.write(frame)

    out.release()
    return output_path
