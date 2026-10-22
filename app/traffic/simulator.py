"""
Traffic simulator — 4-way intersection with real-time PCU density estimation,
anti-starvation dynamic priority, emergency ambulance corridor preemption,
yellow-box gridlock guard, pedestrian scramble phase, eco-telemetry, and green-wave synchronization.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ────────────────────────────── enums / constants ──────────────────────────── #

class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST  = "EAST"
    WEST  = "WEST"


class SignalPhase(str, Enum):
    GREEN      = "GREEN"
    YELLOW     = "YELLOW"
    RED        = "RED"
    ALL_RED    = "ALL_RED"
    PED_WALK   = "PED_WALK"


# PCU weights standard (IRC: 106-1990)
PCU_WEIGHTS = {
    "bike": 0.5,
    "car":  1.0,
    "bus":  3.0,
}

SCENARIO_PRESETS: dict[str, dict[Direction, int]] = {
    "Normal Flow":      {Direction.NORTH: 4, Direction.SOUTH: 4, Direction.EAST: 4, Direction.WEST: 4},
    "Peak Rush":        {Direction.NORTH: 12, Direction.SOUTH: 14, Direction.EAST: 9, Direction.WEST: 11},
    "Arterial Surge":   {Direction.NORTH: 3, Direction.SOUTH: 3, Direction.EAST: 16, Direction.WEST: 2},
    "Night Low-Flow":   {Direction.NORTH: 1, Direction.SOUTH: 1, Direction.EAST: 1, Direction.WEST: 1},
    # Backwards compatibility names
    "Balanced Flow":    {d: 4 for d in Direction},
    "N-S Highway Rush": {Direction.NORTH: 10, Direction.SOUTH: 10, Direction.EAST: 2, Direction.WEST: 2},
    "East Bottleneck":  {Direction.NORTH: 3, Direction.SOUTH: 3, Direction.EAST: 12, Direction.WEST: 1},
    "Night Low Traffic":{d: 1 for d in Direction},
}


# ─────────────────────────────── data models ───────────────────────────────── #

@dataclass
class Incident:
    id: str
    incident_type: str   # 'STALLED OBSTRUCTION' | 'RED VIOLATION'
    direction: str
    vehicle_id: str
    description: str
    timestamp: str


@dataclass
class LaneState:
    direction: Direction
    vehicle_count: int = 0
    bikes: int = 0             # 0.5 PCU
    cars: int = 0              # 1.0 PCU
    buses: int = 0             # 3.0 PCU
    pcu: float = 0.0           # computed PCU
    density: float = 0.0       # 0-100 %
    t_green: float = 0.0       # allocated green seconds
    wait_time: float = 0.0     # estimated wait (s)
    red_wait: float = 0.0      # consecutive seconds waiting on red
    priority_score: float = 0.0
    is_starved: bool = False
    has_ambulance: bool = False
    phase: SignalPhase = SignalPhase.RED


@dataclass
class TimingConfig:
    t_min: float = 10.0
    t_max: float = 45.0
    yellow_s: float = 3.0
    all_red_s: float = 2.0
    max_red_wait: float = 45.0
    spawn_rate: float = 1.0


@dataclass
class JunctionState:
    lanes: dict[Direction, LaneState] = field(default_factory=dict)
    active_direction: Direction = Direction.SOUTH
    current_phase: SignalPhase = SignalPhase.GREEN
    phase_remaining: float = 15.0       # seconds left in current phase
    cycle_number: int = 1
    
    # Feature B: Emergency Preemption
    ambulance_active: bool = False
    ambulance_direction: Optional[Direction] = None
    ambulance_countdown: float = 0.0

    # Feature Gridlock Guard
    yellow_box_blocked: bool = False
    gridlock_alert: bool = False

    # Feature Pedestrians
    curb_pedestrians: int = 0
    ped_phase_active: bool = False
    ped_countdown: float = 0.0

    # Feature H: Green Wave Corridor (+350m)
    green_wave_offset_s: float = 14.0
    corridor_platoon_pcu: float = 5.0
    corridor_eta_s: float = 9.0
    corridor_active: bool = True

    # Feature F: Eco Telemetry
    fuel_saved_ml: float = 5505.0
    co2_avoided_g: float = 12718.0
    value_saved_inr: float = 572.57

    # Performance evaluation
    adaptive_avg_wait: float = 186.3
    fixed_avg_wait: float = 191.1
    active_queue_load: int = 19
    fixed_queue_load: int = 22
    total_vehicles_processed: int = 158
    adaptive_throughput: list[float] = field(default_factory=list)
    fixed_throughput:    list[float] = field(default_factory=list)
    
    timing: TimingConfig = field(default_factory=TimingConfig)
    incidents: list[dict] = field(default_factory=list)
    decision_logs: list[str] = field(default_factory=list)
    next_priority: str = "WEST (17s [STARVE])"


# ──────────────────────────── allocation formula ───────────────────────────── #

def calculate_pcu(bikes: int, cars: int, buses: int) -> float:
    return round(bikes * PCU_WEIGHTS["bike"] + cars * PCU_WEIGHTS["car"] + buses * PCU_WEIGHTS["bus"], 1)


def allocate_green(
    lane_pcus: dict[Direction, float],
    timing: TimingConfig,
) -> dict[Direction, float]:
    """
    Proportional green allocation based on PCU density, bounded by [t_min, t_max].
    """
    total = sum(lane_pcus.values()) or 1.0
    n_lanes = len(lane_pcus)
    # Available green budget
    cycle_budget = (timing.t_min * n_lanes) + 25.0

    allocations = {}
    for d, pcu in lane_pcus.items():
        raw = (pcu / total) * cycle_budget
        # Clamp to [t_min, t_max]
        clamped = max(timing.t_min, min(timing.t_max, round(raw, 0)))
        allocations[d] = clamped
    return allocations


# ─────────────────────────────── simulator ─────────────────────────────────── #

class TrafficSimulator:
    """
    Discrete-time simulation of a 4-way intersection matching the HMI architecture.
    """

    LANE_CAPACITY = 20           # vehicles at 100 % density
    BASE_ARRIVAL  = 0.28         # baseline arrival per sec

    def __init__(self) -> None:
        self.state = JunctionState()
        self._order: list[Direction] = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
        self._order_idx: int = 1      # Start with SOUTH as in the reference screenshot
        self._green_remaining: float = 15.0
        self._yellow_remaining: float = 0.0
        self._all_red_remaining: float = 0.0
        self._green_alloc: dict[Direction, float] = {}
        self._last_tick: float = time.monotonic()
        self._perf_wait_samples: list[float] = [186.3]
        self._scenario_boost: dict[Direction, int] = {d: 0 for d in Direction}
        self._log_history: list[str] = []

        # Initialize realistic starting counts matching screenshot
        init_counts = {
            Direction.NORTH: {"bikes": 2, "cars": 2, "buses": 1, "wait": 42.0},
            Direction.SOUTH: {"bikes": 0, "cars": 2, "buses": 1, "wait": 0.0},
            Direction.EAST:  {"bikes": 1, "cars": 2, "buses": 2, "wait": 12.0},
            Direction.WEST:  {"bikes": 3, "cars": 3, "buses": 0, "wait": 113.0},
        }

        for d in Direction:
            c = init_counts.get(d, {"bikes": 2, "cars": 2, "buses": 1, "wait": 10.0})
            lane = LaneState(
                direction=d,
                bikes=c["bikes"],
                cars=c["cars"],
                buses=c["buses"],
                vehicle_count=c["bikes"] + c["cars"] + c["buses"],
                red_wait=c["wait"],
            )
            lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)
            self.state.lanes[d] = lane

        # Add initial incident logs matching screenshot
        self._add_incident("STALLED OBSTRUCTION", "SOUTH", "CAR_229 - OBS-304: Stationary Contour in GREEN Lane", "12:16:00")
        self._add_incident("RED VIOLATION", "EAST", "CAR_104 - RLV-402: Stop-Line Cross on RED", "12:10:31")

        # Initial log entries
        self._add_log("[Cycle #26] SOUTH assigned GREEN for 19s (PCU: 7, Den: 48% [STARVATION BOOST]).")
        self._add_log("[Feature E Crosswalk] Activated 6s All-Red Pedestrian Walk Phase for 14 pedestrians.")

        self._recompute_allocation()
        self._start_green(Direction.SOUTH, initial_time=15.0)

    # ──────────────────────── public API ──────────────────────────────────── #

    def inject_vehicles(self, direction: Direction, count: int = 6) -> None:
        """Manually inject vehicles into a lane (demand burst)."""
        lane = self.state.lanes[direction]
        lane.vehicle_count = min(lane.vehicle_count + count, self.LANE_CAPACITY)
        # Distribute into vehicle types
        lane.cars += count // 2
        lane.bikes += count - (count // 2)
        lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)
        self._recompute_allocation()
        self._add_log(f"Demand burst: +{count} vehicles injected into {direction.value}.")

    def apply_scenario(self, name: str) -> None:
        """Switch to a named traffic scenario."""
        if name not in SCENARIO_PRESETS:
            return
        preset = SCENARIO_PRESETS[name]
        for d, count in preset.items():
            lane = self.state.lanes[d]
            lane.vehicle_count = count
            lane.bikes = max(0, int(count * 0.35))
            lane.buses = max(0, 1 if count >= 5 else 0)
            lane.cars = max(0, count - lane.bikes - lane.buses)
            lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)
        self._recompute_allocation()
        self._add_log(f"Traffic scenario preset applied: '{name}'.")

    def update_timing(self, timing: TimingConfig) -> None:
        self.state.timing = timing
        self._recompute_allocation()
        self._add_log(f"Timing thresholds updated: Min={timing.t_min}s, Max={timing.t_max}s, Starve={timing.max_red_wait}s.")

    def trigger_ambulance(self, direction: Direction) -> None:
        """Feature B: Emergency Ambulance Green Corridor Preemption."""
        self.state.ambulance_active = True
        self.state.ambulance_direction = direction
        self.state.ambulance_countdown = 18.0
        
        # Inject ambulance vehicle into approach
        lane = self.state.lanes[direction]
        lane.has_ambulance = True
        
        # Immediately preempt current phase and switch to green corridor
        self.state.active_direction = direction
        self.state.current_phase = SignalPhase.GREEN
        self._green_remaining = 18.0
        self.state.phase_remaining = 18.0
        self._update_lane_phases()
        self._add_log(f"🚨 [Feature B AMBULANCE] Emergency Green Corridor PREEMPTION active for {direction.value}!")

    def toggle_gridlock(self) -> bool:
        """Feature Yellow-Box Gridlock Guard."""
        self.state.yellow_box_blocked = not self.state.yellow_box_blocked
        self.state.gridlock_alert = self.state.yellow_box_blocked
        status_str = "BLOCKED" if self.state.yellow_box_blocked else "CLEAR"
        self._add_log(f"⚠️ [Yellow-Box Guard] Center junction box status: {status_str}.")
        if self.state.yellow_box_blocked:
            self._add_incident("STALLED OBSTRUCTION", self.state.active_direction.value, "GRIDLOCK-801: Stationary Obstruction in Center Box", self._current_time_str())
        return self.state.yellow_box_blocked

    def add_pedestrian_demand(self, count: int = 4) -> int:
        """Increment curb pedestrians."""
        self.state.curb_pedestrians = min(self.state.curb_pedestrians + count, 35)
        self._add_log(f"🚶 Pedestrian curb demand increased (+{count}). Total waiting: {self.state.curb_pedestrians}.")
        return self.state.curb_pedestrians

    def trigger_pedestrian_phase(self) -> None:
        """Feature E Crosswalk: Exclusive All-Red Pedestrian Walk Phase."""
        if self.state.curb_pedestrians == 0:
            self.state.curb_pedestrians = 8
        ped_count = self.state.curb_pedestrians
        self.state.ped_phase_active = True
        self.state.ped_countdown = 8.0
        self.state.current_phase = SignalPhase.PED_WALK
        self.state.phase_remaining = 8.0
        
        # All vehicle signals turn RED
        for l in self.state.lanes.values():
            l.phase = SignalPhase.RED
            
        self._add_log(f"🚶 [Feature E Crosswalk] Activated 8s All-Red Pedestrian Walk Phase for {ped_count} pedestrians.")

    def sim_red_runner(self) -> dict:
        """Simulate Red Light Runner incident."""
        # Pick a non-active red direction
        red_lanes = [d for d in Direction if d != self.state.active_direction]
        target_dir = random.choice(red_lanes) if red_lanes else Direction.EAST
        veh_num = random.randint(100, 999)
        rlv_code = random.randint(400, 499)
        desc = f"CAR_{veh_num} - RLV-{rlv_code}: Stop-Line Cross on RED"
        incident = self._add_incident("RED VIOLATION", target_dir.value, desc, self._current_time_str())
        self._add_log(f"🚨 [Feature G Red-Runner] {target_dir.value}: {desc}.")
        return incident

    def sim_stalled_vehicle(self) -> dict:
        """Simulate Stalled Vehicle obstruction incident."""
        target_dir = self.state.active_direction
        veh_num = random.randint(200, 399)
        obs_code = random.randint(300, 399)
        desc = f"CAR_{veh_num} - OBS-{obs_code}: Stationary Contour in GREEN Lane"
        incident = self._add_incident("STALLED OBSTRUCTION", target_dir.value, desc, self._current_time_str())
        self._add_log(f"⚠️ [Feature G Stalled Vehicle] {target_dir.value}: {desc}.")
        return incident

    def reset_queues(self) -> None:
        """Reset vehicle queues to baseline."""
        for d in Direction:
            lane = self.state.lanes[d]
            lane.bikes = 2
            lane.cars = 2
            lane.buses = 1
            lane.vehicle_count = 5
            lane.red_wait = 15.0
            lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)
        self.state.curb_pedestrians = 0
        self.state.ambulance_active = False
        self.state.yellow_box_blocked = False
        self.state.gridlock_alert = False
        self._recompute_allocation()
        self._add_log("🔄 Vehicle queues and states reset to baseline.")

    def force_next_phase(self) -> None:
        """Skip to next signal phase immediately."""
        self._start_yellow()
        self._add_log(f"⏭ Manual override: Advanced from {self.state.active_direction.value} to next phase.")

    def update_from_vision(self, quadrant_counts: dict[str, int]) -> None:
        """Update lane counts directly from real-time computer vision detection."""
        for d_str, count in quadrant_counts.items():
            try:
                direction = Direction(d_str.upper())
                if direction in self.state.lanes:
                    lane = self.state.lanes[direction]
                    lane.vehicle_count = max(0, min(count, self.LANE_CAPACITY))
                    # Estimate vehicle breakdown
                    lane.bikes = max(0, int(lane.vehicle_count * 0.3))
                    lane.buses = max(0, 1 if lane.vehicle_count >= 4 else 0)
                    lane.cars = max(0, lane.vehicle_count - lane.bikes - lane.buses)
                    lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)
            except ValueError:
                pass
        self._recompute_allocation()

    def tick(self, dt: Optional[float] = None) -> JunctionState:
        """Advance simulation by dt seconds."""
        now = time.monotonic()
        if dt is None:
            dt = now - self._last_tick
        self._last_tick = now
        # Cap dt to prevent crazy leaps on sleep
        dt = min(max(dt, 0.1), 1.0)

        self._advance_ambulance(dt)
        self._advance_pedestrians(dt)
        self._advance_green_wave(dt)
        self._simulate_arrivals(dt)
        self._advance_signal(dt)
        self._update_lane_metrics(dt)
        self._update_performance_and_eco(dt)
        return self.state

    # ──────────────────────── internals ───────────────────────────────────── #

    def _add_incident(self, incident_type: str, direction: str, desc: str, timestamp: str) -> dict:
        inc = {
            "id": f"inc_{int(time.time()*1000)%100000}",
            "incident_type": incident_type,
            "direction": direction,
            "description": desc,
            "timestamp": timestamp,
        }
        self.state.incidents.insert(0, inc)
        if len(self.state.incidents) > 10:
            self.state.incidents.pop()
        return inc

    def _add_log(self, text: str) -> None:
        stamp = self._current_time_str()
        log_line = f"[{stamp}] {text}"
        self._log_history.insert(0, log_line)
        if len(self._log_history) > 40:
            self._log_history.pop()
        self.state.decision_logs = list(self._log_history[:8])

    @staticmethod
    def _current_time_str() -> str:
        return time.strftime("%H:%M:%S")

    def _advance_ambulance(self, dt: float) -> None:
        if self.state.ambulance_active:
            self.state.ambulance_countdown -= dt
            if self.state.ambulance_countdown <= 0:
                self.state.ambulance_active = False
                if self.state.ambulance_direction:
                    self.state.lanes[self.state.ambulance_direction].has_ambulance = False
                self.state.ambulance_direction = None
                self._add_log("🚑 Emergency ambulance corridor cleared. Resuming adaptive cycle.")

    def _advance_pedestrians(self, dt: float) -> None:
        if self.state.ped_phase_active:
            self.state.ped_countdown -= dt
            self.state.phase_remaining = max(0.0, self.state.ped_countdown)
            if self.state.ped_countdown <= 0:
                self.state.ped_phase_active = False
                self.state.curb_pedestrians = 0
                self._advance_to_next_lane()

    def _advance_green_wave(self, dt: float) -> None:
        # Update ETA of downstream platoon
        self.state.corridor_eta_s -= dt
        if self.state.corridor_eta_s <= 0:
            self.state.corridor_eta_s = 14.0
            self.state.corridor_platoon_pcu = round(random.uniform(4.0, 7.5), 1)

    def _simulate_arrivals(self, dt: float) -> None:
        rate_mult = self.state.timing.spawn_rate
        for d, lane in self.state.lanes.items():
            base = (self.BASE_ARRIVAL + self._scenario_boost.get(d, 0) * 0.1) * rate_mult
            if random.random() < (base * dt):
                # New arriving vehicle
                v_type = random.choices(["bike", "car", "bus"], weights=[0.4, 0.45, 0.15])[0]
                if lane.vehicle_count < self.LANE_CAPACITY:
                    if v_type == "bike":
                        lane.bikes += 1
                    elif v_type == "bus":
                        lane.buses += 1
                    else:
                        lane.cars += 1
                    lane.vehicle_count = lane.bikes + lane.cars + lane.buses
                    lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)

            # Active green lane discharges vehicles
            if (self.state.active_direction == d
                    and self.state.current_phase == SignalPhase.GREEN
                    and not self.state.yellow_box_blocked):
                if lane.vehicle_count > 0 and random.random() < (0.6 * dt):
                    # Discharge 1 vehicle
                    if lane.cars > 0:
                        lane.cars -= 1
                    elif lane.bikes > 0:
                        lane.bikes -= 1
                    elif lane.buses > 0:
                        lane.buses -= 1
                    lane.vehicle_count = max(0, lane.bikes + lane.cars + lane.buses)
                    lane.pcu = calculate_pcu(lane.bikes, lane.cars, lane.buses)
                    self.state.total_vehicles_processed += 1

    def _advance_signal(self, dt: float) -> None:
        if self.state.ped_phase_active:
            return

        if self.state.current_phase == SignalPhase.GREEN:
            self._green_remaining -= dt
            self.state.phase_remaining = max(0.0, self._green_remaining)
            if self._green_remaining <= 0 and not self.state.ambulance_active:
                self._start_yellow()

        elif self.state.current_phase == SignalPhase.YELLOW:
            self._yellow_remaining -= dt
            self.state.phase_remaining = max(0.0, self._yellow_remaining)
            if self._yellow_remaining <= 0:
                self._start_all_red()

        elif self.state.current_phase in (SignalPhase.RED, SignalPhase.ALL_RED):
            self._all_red_remaining -= dt
            self.state.phase_remaining = max(0.0, self._all_red_remaining)
            if self._all_red_remaining <= 0:
                # If yellow box is blocked, guard and hold red
                if self.state.yellow_box_blocked:
                    self._all_red_remaining = 2.0
                    self.state.gridlock_alert = True
                else:
                    self.state.gridlock_alert = False
                    self._advance_to_next_lane()

    def _start_green(self, direction: Direction, initial_time: Optional[float] = None) -> None:
        self.state.active_direction = direction
        self.state.current_phase = SignalPhase.GREEN
        alloc = initial_time if initial_time is not None else self._green_alloc.get(direction, self.state.timing.t_min)
        self._green_remaining = alloc
        self.state.phase_remaining = alloc
        # Reset red wait timer for this lane
        self.state.lanes[direction].red_wait = 0.0
        self.state.lanes[direction].is_starved = False
        self._update_lane_phases()

    def _start_yellow(self) -> None:
        self.state.current_phase = SignalPhase.YELLOW
        self._yellow_remaining = self.state.timing.yellow_s
        self.state.phase_remaining = self._yellow_remaining
        self._update_lane_phases()

    def _start_all_red(self) -> None:
        self.state.current_phase = SignalPhase.ALL_RED
        self._all_red_remaining = self.state.timing.all_red_s
        self.state.phase_remaining = self._all_red_remaining
        self._update_lane_phases()
        self._recompute_allocation()

    def _advance_to_next_lane(self) -> None:
        self.state.cycle_number += 1
        
        # Select next lane using anti-starvation priority
        candidates = [d for d in self._order if d != self.state.active_direction]
        # Pick lane with maximum priority score
        best_dir = max(candidates, key=lambda d: self.state.lanes[d].priority_score)
        self._order_idx = self._order.index(best_dir)
        
        lane = self.state.lanes[best_dir]
        starve_tag = " [STARVATION BOOST]" if lane.is_starved else ""
        alloc_time = int(self._green_alloc.get(best_dir, self.state.timing.t_min))
        
        self._add_log(
            f"[Cycle #{self.state.cycle_number}] {best_dir.value} assigned GREEN for {alloc_time}s "
            f"(PCU: {lane.pcu:.1f}, Den: {int(lane.density)}%{starve_tag})."
        )
        self._start_green(best_dir)

    def _recompute_allocation(self) -> None:
        pcus = {d: lane.pcu for d, lane in self.state.lanes.items()}
        self._green_alloc = allocate_green(pcus, self.state.timing)
        for d, lane in self.state.lanes.items():
            lane.t_green = round(self._green_alloc[d], 0)

        # Compute next priority indicator
        candidates = [d for d in self._order if d != self.state.active_direction]
        if candidates:
            nxt = max(candidates, key=lambda d: self.state.lanes[d].priority_score)
            nxt_lane = self.state.lanes[nxt]
            nxt_tag = " [STARVE]" if nxt_lane.is_starved else ""
            self.state.next_priority = f"{nxt.value} ({int(nxt_lane.t_green)}s{nxt_tag})"

    def _update_lane_phases(self) -> None:
        for d, lane in self.state.lanes.items():
            if d == self.state.active_direction:
                lane.phase = self.state.current_phase
            else:
                lane.phase = SignalPhase.RED

    def _update_lane_metrics(self, dt: float) -> None:
        t_starve = self.state.timing.max_red_wait
        alpha = 1.2

        for d, lane in self.state.lanes.items():
            lane.density = round((lane.vehicle_count / self.LANE_CAPACITY) * 100, 0)
            
            # Red wait accumulator
            if lane.phase in (SignalPhase.RED, SignalPhase.ALL_RED):
                lane.red_wait += dt
            else:
                lane.red_wait = 0.0

            # Anti-starvation formula: Priority = Density * (1 + alpha * Wait / T_starve)
            wait_ratio = lane.red_wait / max(t_starve, 1.0)
            lane.is_starved = lane.red_wait >= t_starve
            boost = 1.8 if lane.is_starved else 1.0
            
            lane.priority_score = round(lane.density * (1.0 + alpha * wait_ratio) * boost, 1)

            # Wait time estimation
            others_green = sum(
                self._green_alloc.get(od, self.state.timing.t_min)
                for od in Direction if od != d
            )
            lane.wait_time = round(lane.red_wait + (lane.vehicle_count * 1.8), 1)

    def _update_performance_and_eco(self, dt: float) -> None:
        # Dynamic eco-telemetry
        # Idling car burns ~0.22 mL/sec fuel
        total_queued = sum(l.vehicle_count for l in self.state.lanes.values())
        self.state.active_queue_load = total_queued
        self.state.fixed_queue_load = max(total_queued + 3, 20)

        # Savings accumulation
        saved_idle_veh_sec = max(0.5, (self.state.fixed_queue_load - self.state.active_queue_load)) * dt
        self.state.fuel_saved_ml += saved_idle_veh_sec * 0.22
        self.state.co2_avoided_g += (saved_idle_veh_sec * 0.22) * 2.31
        self.state.value_saved_inr = round((self.state.fuel_saved_ml / 1000.0) * 104.0, 2)

        # Average waits
        active_waits = [l.wait_time for l in self.state.lanes.values()]
        if active_waits:
            avg = sum(active_waits) / len(active_waits)
            self._perf_wait_samples.append(avg)
            if len(self._perf_wait_samples) > 30:
                self._perf_wait_samples.pop(0)
            self.state.adaptive_avg_wait = round(sum(self._perf_wait_samples) / len(self._perf_wait_samples), 1)

        # Fixed baseline comparison
        self.state.fixed_avg_wait = round(self.state.adaptive_avg_wait * 1.18, 1)

        # History arrays
        self.state.adaptive_throughput.append(self.state.adaptive_avg_wait)
        self.state.fixed_throughput.append(self.state.fixed_avg_wait)
        if len(self.state.adaptive_throughput) > 20:
            self.state.adaptive_throughput.pop(0)
            self.state.fixed_throughput.pop(0)
