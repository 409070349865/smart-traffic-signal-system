"""
Traffic simulator — 4-way intersection with real-time density estimation
and proportional green-allocation algorithm.

All timing values are in seconds; density values are 0-100 %.
No real hardware is connected; this is an academic simulation.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ────────────────────────────── enums / constants ──────────────────────────── #

class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST  = "EAST"
    WEST  = "WEST"


class SignalPhase(str, Enum):
    GREEN  = "GREEN"
    YELLOW = "YELLOW"
    RED    = "RED"


SCENARIO_PRESETS: dict[str, dict[Direction, int]] = {
    "Balanced Flow":     {d: 4 for d in Direction},
    "N-S Highway Rush":  {Direction.NORTH: 10, Direction.SOUTH: 10,
                          Direction.EAST: 2,   Direction.WEST: 2},
    "East Bottleneck":   {Direction.NORTH: 3, Direction.SOUTH: 3,
                          Direction.EAST: 12,  Direction.WEST: 1},
    "Night Low Traffic": {d: 1 for d in Direction},
}


# ─────────────────────────────── data models ───────────────────────────────── #

@dataclass
class LaneState:
    direction: Direction
    vehicle_count: int = 0
    density: float = 0.0          # 0-100 %
    t_green: float = 0.0          # allocated green seconds
    wait_time: float = 0.0        # estimated wait (s)
    phase: SignalPhase = SignalPhase.RED
    # Simulated camera "detection" counters (updated each tick)
    cars: int = 0
    buses: int = 0
    motorcycles: int = 0


@dataclass
class TimingConfig:
    t_min: float = 10.0
    t_max: float = 60.0
    yellow_s: float = 3.0
    all_red_s: float = 2.0


@dataclass
class JunctionState:
    lanes: dict[Direction, LaneState] = field(default_factory=dict)
    active_direction: Direction = Direction.NORTH
    current_phase: SignalPhase = SignalPhase.GREEN
    phase_remaining: float = 0.0       # seconds left in current phase
    cycle_number: int = 0
    # Performance metrics
    adaptive_avg_wait: float = 0.0
    fixed_avg_wait: float = 30.0       # fixed-time baseline (s)
    total_vehicles_processed: int = 0
    adaptive_throughput: list[float] = field(default_factory=list)
    fixed_throughput:    list[float] = field(default_factory=list)
    timing: TimingConfig = field(default_factory=TimingConfig)


# ──────────────────────────── allocation formula ───────────────────────────── #

def allocate_green(
    lane_counts: dict[Direction, int],
    timing: TimingConfig,
) -> dict[Direction, float]:
    """
    Proportional green allocation bounded by [t_min, t_max].

    Formula:
        raw_i   = (count_i / total_count) * cycle_budget
        cycle_budget = sum of t_min for all lanes + remaining budget split proportionally
    """
    total = sum(lane_counts.values()) or 1
    n_lanes = len(lane_counts)
    cycle_budget = timing.t_max * n_lanes          # total available green seconds

    # First pass: proportional raw
    raw = {d: max((c / total) * cycle_budget, 0.0) for d, c in lane_counts.items()}

    # Clamp to [t_min, t_max]
    clamped = {d: max(timing.t_min, min(timing.t_max, v)) for d, v in raw.items()}
    return clamped


# ─────────────────────────────── simulator ─────────────────────────────────── #

class TrafficSimulator:
    """
    Discrete-time simulation of a 4-way intersection.
    Call `tick(dt)` every dt seconds to advance state.
    """

    LANE_CAPACITY = 20           # vehicles at 100 % density
    BASE_ARRIVAL  = 0.3          # vehicles arriving per second per lane (quiet)

    def __init__(self) -> None:
        self.state = JunctionState()
        self._order: list[Direction] = list(Direction)
        self._order_idx: int = 0
        self._green_remaining: float = 0.0
        self._yellow_remaining: float = 0.0
        self._all_red_remaining: float = 0.0
        self._green_alloc: dict[Direction, float] = {}
        self._last_tick: float = time.monotonic()
        self._perf_wait_samples: list[float] = []
        self._scenario_boost: dict[Direction, int] = {d: 0 for d in Direction}

        # Initialise lanes
        for d in Direction:
            self.state.lanes[d] = LaneState(direction=d)

        # Compute first allocation
        self._recompute_allocation()
        self._start_green(self._order[0])

    # ──────────────────────── public API ──────────────────────────────────── #

    def inject_vehicles(self, direction: Direction, count: int = 6) -> None:
        """Manually inject vehicles into a lane (demand burst)."""
        lane = self.state.lanes[direction]
        lane.vehicle_count = min(lane.vehicle_count + count, self.LANE_CAPACITY)
        self._recompute_allocation()

    def apply_scenario(self, name: str) -> None:
        """Switch to a named traffic scenario."""
        if name not in SCENARIO_PRESETS:
            return
        preset = SCENARIO_PRESETS[name]
        for d, extra in preset.items():
            self._scenario_boost[d] = extra

    def update_timing(self, timing: TimingConfig) -> None:
        self.state.timing = timing
        self._recompute_allocation()

    def tick(self, dt: Optional[float] = None) -> JunctionState:
        """Advance simulation by dt seconds (auto-measured if None)."""
        now = time.monotonic()
        if dt is None:
            dt = now - self._last_tick
        self._last_tick = now

        self._simulate_arrivals(dt)
        self._advance_signal(dt)
        self._update_lane_metrics()
        self._update_performance_metrics(dt)
        return self.state

    # ──────────────────────── internals ───────────────────────────────────── #

    def _simulate_arrivals(self, dt: float) -> None:
        """Random Poisson-ish arrivals + scenario boosts."""
        for d, lane in self.state.lanes.items():
            base = self.BASE_ARRIVAL + self._scenario_boost.get(d, 0) * 0.1
            arrivals = int(random.expovariate(1 / max(base * dt, 0.01)) * base * dt
                           + random.random())
            # Active green lane discharges vehicles
            if (self.state.active_direction == d
                    and self.state.current_phase == SignalPhase.GREEN):
                discharge = max(int(random.uniform(1.5, 2.5) * dt), 0)
                arrivals -= discharge
            lane.vehicle_count = max(0, min(
                self.LANE_CAPACITY, lane.vehicle_count + arrivals
            ))

    def _advance_signal(self, dt: float) -> None:
        """Run the phase state machine."""
        if self.state.current_phase == SignalPhase.GREEN:
            self._green_remaining -= dt
            self.state.phase_remaining = max(0.0, self._green_remaining)
            if self._green_remaining <= 0:
                self._start_yellow()

        elif self.state.current_phase == SignalPhase.YELLOW:
            self._yellow_remaining -= dt
            self.state.phase_remaining = max(0.0, self._yellow_remaining)
            if self._yellow_remaining <= 0:
                self._start_all_red()

        elif self.state.current_phase == SignalPhase.RED:
            # All-red clearance interval
            self._all_red_remaining -= dt
            self.state.phase_remaining = max(0.0, self._all_red_remaining)
            if self._all_red_remaining <= 0:
                self._advance_to_next_lane()

    def _start_green(self, direction: Direction) -> None:
        self.state.active_direction = direction
        self.state.current_phase = SignalPhase.GREEN
        alloc = self._green_alloc.get(direction, self.state.timing.t_min)
        self._green_remaining = alloc
        self.state.phase_remaining = alloc
        self._update_lane_phases()

    def _start_yellow(self) -> None:
        self.state.current_phase = SignalPhase.YELLOW
        self._yellow_remaining = self.state.timing.yellow_s
        self.state.phase_remaining = self._yellow_remaining
        self._update_lane_phases()

    def _start_all_red(self) -> None:
        self.state.current_phase = SignalPhase.RED
        self._all_red_remaining = self.state.timing.all_red_s
        self.state.phase_remaining = self._all_red_remaining
        self._update_lane_phases()
        self._recompute_allocation()

    def _advance_to_next_lane(self) -> None:
        self.state.cycle_number += 1
        self._order_idx = (self._order_idx + 1) % len(self._order)
        self._start_green(self._order[self._order_idx])

    def _recompute_allocation(self) -> None:
        counts = {d: lane.vehicle_count for d, lane in self.state.lanes.items()}
        self._green_alloc = allocate_green(counts, self.state.timing)
        for d, lane in self.state.lanes.items():
            lane.t_green = round(self._green_alloc[d], 1)

    def _update_lane_phases(self) -> None:
        for d, lane in self.state.lanes.items():
            if d == self.state.active_direction:
                lane.phase = self.state.current_phase
            else:
                lane.phase = SignalPhase.RED

    def _update_lane_metrics(self) -> None:
        for d, lane in self.state.lanes.items():
            lane.density = round(lane.vehicle_count / self.LANE_CAPACITY * 100, 1)
            # Simulated vehicle-class breakdown
            total = lane.vehicle_count
            lane.cars = max(0, int(total * random.uniform(0.55, 0.70)))
            lane.buses = max(0, int(total * random.uniform(0.05, 0.15)))
            lane.motorcycles = max(0, total - lane.cars - lane.buses)
            # Wait time estimate: vehicles ahead / discharge rate ~2 veh/s during green
            others_green_time = sum(
                self._green_alloc.get(od, self.state.timing.t_min)
                for od in Direction if od != d
            )
            discharge_rate = 2.0
            lane.wait_time = round(
                min(lane.vehicle_count / discharge_rate + others_green_time, 120.0), 1
            )

    def _update_performance_metrics(self, dt: float) -> None:
        waits = [l.wait_time for l in self.state.lanes.values()]
        if waits:
            avg = sum(waits) / len(waits)
            self._perf_wait_samples.append(avg)
            if len(self._perf_wait_samples) > 60:
                self._perf_wait_samples.pop(0)
            self.state.adaptive_avg_wait = round(
                sum(self._perf_wait_samples) / len(self._perf_wait_samples), 1
            )
        # Track total vehicles processed (discharged from green lane)
        active = self.state.lanes[self.state.active_direction]
        if self.state.current_phase == SignalPhase.GREEN:
            processed = max(0, int(random.uniform(1.5, 2.5) * dt))
            self.state.total_vehicles_processed += processed

        # Build throughput history (last 20 data points)
        self.state.adaptive_throughput.append(self.state.adaptive_avg_wait)
        self.state.fixed_throughput.append(self.state.fixed_avg_wait
                                           + random.uniform(-2, 2))
        if len(self.state.adaptive_throughput) > 20:
            self.state.adaptive_throughput.pop(0)
            self.state.fixed_throughput.pop(0)
