"""
FastAPI entry point — Full 8-Step Intelligent Traffic Control System
  Group SignalSync:
    Atharav Arun Futane (2023BCS033)
    Jitesh Sitaram Gaikwad (2023BCS083)

  • WebSocket /ws/traffic        (real-time signal-state stream & PCU telemetry)
  • WebSocket /ws/video          (real-time OpenCV processed video frames: RGB, CLAHE, Mask, Bird's-Eye)
  • REST /api/traffic/*          (ambulance, gridlock, ped walk, presets, timing sliders, weather, watchdog, siren, csv report)
  • REST /api/video/*            (upload, camera, demo, status, stop, sync)
  • Static file serving of the dashboard (frontend/)
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, Security, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api.video import router as video_router, video_ws_router, register_simulator
from app.auth.security import (
    Role, User, authenticate_user, create_access_token, get_current_user, require_role, USERS_DB
)
from app.config import get_settings, setup_logging
from app.database.audit import log_activity, get_recent_audit_logs
from app.database.backup import create_database_backup, list_backups
from app.database.db import get_db_summary
from app.traffic.simulator import (
    Direction, TimingConfig, TrafficSimulator, SCENARIO_PRESETS
)
from app.websocket.manager import ConnectionManager

logger = logging.getLogger(__name__)

# ────────────── singletons ───────────────
_simulator  = TrafficSimulator()
_ws_manager = ConnectionManager()
register_simulator(_simulator)

TICK_INTERVAL = 0.4          # seconds between simulation ticks & WS broadcasts
_broadcast_task: asyncio.Task | None = None
FRONTEND_DIR  = Path(__file__).resolve().parent.parent / "frontend"


# ────────────── helpers ──────────────────

def _lane_dict(lane: Any) -> dict:
    return {
        "direction":      lane.direction.value,
        "vehicle_count":  lane.vehicle_count,
        "bikes":          lane.bikes,
        "cars":           lane.cars,
        "buses":          lane.buses,
        "pcu":            lane.pcu,
        "density":        lane.density,
        "t_green":        lane.t_green,
        "wait_time":      lane.wait_time,
        "red_wait":       round(lane.red_wait, 1),
        "priority_score": lane.priority_score,
        "is_starved":     lane.is_starved,
        "has_ambulance":  lane.has_ambulance,
        "sensor_fault":   lane.sensor_fault,
        "phase":          lane.phase.value,
    }


def _state_payload(state: Any) -> dict:
    return {
        "type": "state",
        "lanes": {d.value: _lane_dict(l) for d, l in state.lanes.items()},
        "active_direction":    state.active_direction.value,
        "current_phase":       state.current_phase.value,
        "phase_remaining":     round(state.phase_remaining, 1),
        "cycle_number":        state.cycle_number,

        # Conflict Monitor Unit (CMU) Hardware Interlock
        "cmu_status":          state.cmu_status,
        "cmu_interlock_active": state.cmu_interlock_active,
        "cmu_fault":           state.cmu_fault,

        # Weather-Adaptive Clearance Scaling
        "weather":             state.weather,
        "effective_yellow":    round(state.effective_yellow, 1),
        "effective_all_red":   round(state.effective_all_red, 1),
        
        # Dual-Modal Emergency Preemption
        "ambulance_active":    state.ambulance_active,
        "ambulance_direction": state.ambulance_direction.value if state.ambulance_direction else None,
        "ambulance_countdown": round(state.ambulance_countdown, 1),
        "strobe_detected":     state.strobe_detected,
        "audio_siren_detected": state.audio_siren_detected,
        "audio_siren_freq":    round(state.audio_siren_freq, 1),
        
        # Yellow-Box Spillback Guard
        "yellow_box_blocked":  state.yellow_box_blocked,
        "gridlock_alert":      state.gridlock_alert,
        
        # Pedestrian Crosswalk Demand
        "curb_pedestrians":    state.curb_pedestrians,
        "ped_phase_active":    state.ped_phase_active,
        "ped_countdown":       round(state.ped_countdown, 1),

        # Farneback Optical-Flow Queue Discharge
        "optical_flow_speed":  round(state.optical_flow_speed, 1),
        "downstream_blocked":  state.downstream_blocked,
        "early_discharge_triggered": state.early_discharge_triggered,
        
        # Multi-Junction Green Wave (350m / 40 km/h = 31.5s offset)
        "corridor_distance_m": state.corridor_distance_m,
        "corridor_speed_kmh":  state.corridor_speed_kmh,
        "green_wave_offset_s": round(state.green_wave_offset_s, 1),
        "corridor_platoon_pcu": state.corridor_platoon_pcu,
        "corridor_eta_s":      round(state.corridor_eta_s, 1),
        
        # Eco-Telemetry
        "fuel_saved_ml":       round(state.fuel_saved_ml, 1),
        "co2_avoided_g":       round(state.co2_avoided_g, 1),
        "value_saved_inr":     round(state.value_saved_inr, 2),
        
        # Performance Evaluation & Overview KPIs
        "adaptive_avg_wait":   state.adaptive_avg_wait,
        "fixed_avg_wait":      state.fixed_avg_wait,
        "active_queue_load":   state.active_queue_load,
        "fixed_queue_load":    state.fixed_queue_load,
        "total_vehicles_processed": state.total_vehicles_processed,
        "total_vehicles_detected": state.total_vehicles_processed + sum(l.vehicle_count for l in state.lanes.values()),
        "avg_wait_time_s":     round(state.adaptive_avg_wait, 1),
        "congestion_level_pct": round(sum(l.density for l in state.lanes.values()) / max(len(state.lanes), 1), 1),
        "avg_queue_length_m":  round(sum(l.vehicle_count for l in state.lanes.values()) * 5.2 / max(len(state.lanes), 1), 1),
        "hourly_throughput":   state.hourly_throughput,
        "emergency_response_time_s": state.emergency_response_time_s,
        "adaptive_throughput": [round(v, 1) for v in state.adaptive_throughput],
        "fixed_throughput":    [round(v, 1) for v in state.fixed_throughput],

        # SQLite Rush-Hour Learning
        "rush_hour_learning_active": state.rush_hour_learning_active,
        "is_rush_hour":        state.is_rush_hour,
        "learned_t_min_boost": state.learned_t_min_boost,
        
        "incidents":           state.incidents,
        "decision_logs":       state.decision_logs,
        "next_priority":       state.next_priority,
        
        "timing": {
            "t_min":        state.timing.t_min,
            "t_max":        state.timing.t_max,
            "yellow_s":     state.timing.yellow_s,
            "all_red_s":    state.timing.all_red_s,
            "max_red_wait": state.timing.max_red_wait,
            "spawn_rate":   state.timing.spawn_rate,
        },
        "scenarios": list(SCENARIO_PRESETS.keys()),
        "authors": [
            {"name": "Atharav Arun Futane", "roll": "2023BCS033"},
            {"name": "Jitesh Sitaram Gaikwad", "roll": "2023BCS083"}
        ]
    }


# ────────────── background ticker ────────────────

async def _broadcast_loop() -> None:
    """Ticks the traffic simulator and broadcasts state to clients."""
    while True:
        state = _simulator.tick()
        if _ws_manager.count > 0:
            await _ws_manager.broadcast(_state_payload(state))
        await asyncio.sleep(TICK_INTERVAL)


# ────────────── request bodies ───────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class InjectRequest(BaseModel):
    direction: str
    count: int = 6


class ScenarioRequest(BaseModel):
    name: str


class AmbulanceRequest(BaseModel):
    direction: str


class PedestrianRequest(BaseModel):
    count: int = 4


class IncidentRequest(BaseModel):
    type: str = "red_runner"   # "red_runner" | "stalled"


class ThresholdsRequest(BaseModel):
    t_min: float = 10.0
    t_max: float = 45.0
    yellow_s: float = 3.0
    all_red_s: float = 2.0
    max_red_wait: float = 60.0
    spawn_rate: float = 1.0


class WeatherRequest(BaseModel):
    weather: str = "dry"       # "dry" | "rainy"


class SensorFaultRequest(BaseModel):
    direction: str
    fault: bool = True


class SirenRequest(BaseModel):
    direction: str


# ────────────── app factory ──────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _broadcast_task
    if _broadcast_task is None or _broadcast_task.done():
        _broadcast_task = asyncio.create_task(_broadcast_loop())
    logger.info("SignalSync (Atharav Futane & Jitesh Gaikwad) traffic simulator background task started.")
    try:
        yield
    finally:
        if _broadcast_task and not _broadcast_task.done():
            _broadcast_task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.app.log_level)

    application = FastAPI(
        title="Smart Traffic Signal System (SignalSync)",
        description="4-Way Junction Adaptive Traffic Signal Control with OpenCV Computer Vision — Atharav Arun Futane (2023BCS033) & Jitesh Sitaram Gaikwad (2023BCS083)",
        version="2.0.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    application.include_router(video_router)
    application.include_router(video_ws_router)

    # Static assets
    if FRONTEND_DIR.is_dir():
        application.mount(
            "/static",
            StaticFiles(directory=str(FRONTEND_DIR)),
            name="static",
        )

    dashboard_html = FRONTEND_DIR / "index.html"

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> HTMLResponse:
        if dashboard_html.is_file():
            return HTMLResponse(dashboard_html.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Dashboard not found</h1>", 404)

    @application.get("/api/health", tags=["system"])
    async def health() -> dict:
        return {
            "status": "ok",
            "system": "SignalSync",
            "notice": "Academic prototype for research and simulation.",
            "version": application.version,
            "ws_clients": _ws_manager.count,
            "group": "Group SignalSync",
            "authors": [
                {"name": "Atharav Arun Futane", "roll": "2023BCS033"},
                {"name": "Jitesh Sitaram Gaikwad", "roll": "2023BCS083"}
            ]
        }

    # ── Authentication & Identity ────────────────────────────
    @application.post("/api/auth/login", tags=["auth"])
    async def login(req: LoginRequest) -> dict:
        user = authenticate_user(req.username, req.password)
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid username or password"
            )
        token = create_access_token({"sub": user.username, "role": user.role})
        log_activity(user.username, user.role, "AUTH_LOGIN", f"User {user.username} successfully logged in")
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "username": user.username,
                "role": user.role,
                "full_name": user.full_name,
            }
        }

    @application.get("/api/auth/me", tags=["auth"])
    async def get_me(current_user: User = Depends(get_current_user)) -> dict:
        return {
            "username": current_user.username,
            "role": current_user.role,
            "full_name": current_user.full_name,
        }

    # ── System Maintenance & Disaster Recovery ───────────────
    @application.post("/api/system/backup", tags=["system"])
    async def trigger_backup(current_user: User = Depends(require_role([Role.ADMIN]))) -> dict:
        res = create_database_backup()
        log_activity(current_user.username, current_user.role, "DB_BACKUP_CREATED", f"Backup created: {res['filename']} ({res['size_bytes']} bytes)")
        return res

    @application.get("/api/system/backups", tags=["system"])
    async def get_backups(current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))) -> list:
        return list_backups()

    @application.get("/api/system/audit", tags=["system"])
    async def get_audit_trail(limit: int = 30, current_user: User = Depends(get_current_user)) -> list:
        return get_recent_audit_logs(limit)

    # ── Control Endpoints (Protected with RBAC & Audited) ─────
    @application.post("/api/traffic/ambulance", tags=["control"])
    async def trigger_ambulance(
        req: AmbulanceRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Invalid direction {req.direction}"}
        _simulator.trigger_ambulance(d)
        log_activity(current_user.username, current_user.role, "AMBULANCE_PREEMPTION", f"Dispatched corridor preemption for {d.value}")
        return {"ok": True, "ambulance_direction": d.value}

    @application.post("/api/traffic/siren", tags=["control"])
    async def trigger_siren_audio(
        req: SirenRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Invalid direction {req.direction}"}
        _simulator.trigger_ambulance(d, is_audio_fft=True)
        log_activity(current_user.username, current_user.role, "SIREN_AUDIO_FFT", f"Triggered audio siren acoustic preemption for {d.value}")
        return {"ok": True, "ambulance_direction": d.value, "mode": "AUDIO FFT (650-1500Hz)"}

    @application.post("/api/traffic/weather", tags=["control"])
    async def set_weather(
        req: WeatherRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        w = _simulator.set_weather(req.weather)
        log_activity(current_user.username, current_user.role, "WEATHER_CHANGE", f"Switched weather condition to {w}")
        return {"ok": True, "weather": w, "effective_yellow": _simulator.state.effective_yellow, "effective_all_red": _simulator.state.effective_all_red}

    @application.post("/api/traffic/sensor_fault", tags=["control"])
    async def set_sensor_fault(
        req: SensorFaultRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Invalid direction {req.direction}"}
        _simulator.set_sensor_fault(d, req.fault)
        log_activity(current_user.username, current_user.role, "WATCHDOG_FAULT", f"Toggled sensor fault on {d.value} to {req.fault}")
        return {"ok": True, "direction": d.value, "sensor_fault": req.fault}

    @application.post("/api/traffic/optical_flow_cutoff", tags=["control"])
    async def optical_flow_cutoff(
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        _simulator.trigger_optical_flow_early_cutoff()
        log_activity(current_user.username, current_user.role, "OPTICAL_FLOW_CUTOFF", "Triggered Farneback early green discharge cutoff")
        return {"ok": True}

    @application.post("/api/traffic/gridlock", tags=["control"])
    async def toggle_gridlock(
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        blocked = _simulator.toggle_gridlock()
        log_activity(current_user.username, current_user.role, "YELLOWBOX_GRIDLOCK", f"Yellow-box spillback obstruction set to {blocked}")
        return {"ok": True, "yellow_box_blocked": blocked}

    @application.post("/api/traffic/pedestrian/add", tags=["control"])
    async def add_pedestrians(
        req: PedestrianRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        count = _simulator.add_pedestrian_demand(req.count)
        log_activity(current_user.username, current_user.role, "PEDESTRIAN_DEMAND", f"Added {req.count} curb pedestrians, total={count}")
        return {"ok": True, "curb_pedestrians": count}

    @application.post("/api/traffic/pedestrian/trigger", tags=["control"])
    async def trigger_pedestrian_phase(
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        _simulator.trigger_pedestrian_phase()
        log_activity(current_user.username, current_user.role, "PEDESTRIAN_PHASE", "Triggered dedicated 12s All-Red Pedestrian Walk Phase")
        return {"ok": True, "ped_phase": True}

    @application.post("/api/traffic/sim_incident", tags=["control"])
    async def sim_incident(
        req: IncidentRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        if req.type == "stalled":
            inc = _simulator.sim_stalled_vehicle()
            log_activity(current_user.username, current_user.role, "SIM_INCIDENT", f"Simulated stalled vehicle incident #{inc.get('id', '')}")
        else:
            inc = _simulator.sim_red_runner()
            log_activity(current_user.username, current_user.role, "SIM_INCIDENT", f"Simulated red-light runner violation #{inc.get('id', '')}")
        return {"ok": True, "incident": inc}

    @application.post("/api/traffic/reset", tags=["control"])
    async def reset_queues(
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        _simulator.reset_queues()
        log_activity(current_user.username, current_user.role, "RESET_QUEUES", "Flushed all lane queues to baseline")
        return {"ok": True}

    @application.post("/api/traffic/next_phase", tags=["control"])
    async def next_phase(
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        _simulator.force_next_phase()
        log_activity(current_user.username, current_user.role, "FORCE_PHASE", "Manually forced advance to next signal phase")
        return {"ok": True}

    @application.post("/api/traffic/scenario", tags=["control"])
    async def set_scenario(
        req: ScenarioRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        _simulator.apply_scenario(req.name)
        log_activity(current_user.username, current_user.role, "SCENARIO_PRESET", f"Activated traffic preset scenario: {req.name}")
        return {"ok": True, "scenario": req.name}

    @application.post("/api/traffic/inject", tags=["control"])
    async def inject_vehicles(
        req: InjectRequest,
        current_user: User = Depends(require_role([Role.ADMIN, Role.OPERATOR]))
    ) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Unknown direction: {req.direction}"}
        _simulator.inject_vehicles(d, req.count)
        log_activity(current_user.username, current_user.role, "INJECT_VEHICLES", f"Injected {req.count} vehicles into {d.value}")
        return {"ok": True, "direction": d.value, "count": req.count}

    @application.post("/api/traffic/thresholds", tags=["control"])
    async def set_thresholds(
        req: ThresholdsRequest,
        current_user: User = Depends(require_role([Role.ADMIN]))
    ) -> dict:
        _simulator.update_timing(TimingConfig(
            t_min=req.t_min,
            t_max=req.t_max,
            yellow_s=req.yellow_s,
            all_red_s=req.all_red_s,
            max_red_wait=req.max_red_wait,
            spawn_rate=req.spawn_rate,
        ))
        log_activity(current_user.username, current_user.role, "TIMING_UPDATE", f"Admin updated timing thresholds: T_min={req.t_min}s, T_max={req.t_max}s, SpawnRate={req.spawn_rate}x")
        return {"ok": True}

    @application.post("/api/traffic/timing", tags=["control"])
    async def set_timing(
        req: ThresholdsRequest,
        current_user: User = Depends(require_role([Role.ADMIN]))
    ) -> dict:
        return await set_thresholds(req, current_user=current_user)

    @application.get("/api/traffic/state", tags=["data"])
    async def get_state() -> dict:
        state = _simulator.state
        return _state_payload(state)

    @application.get("/api/traffic/vehicles", tags=["tracking"])
    async def get_tracked_vehicles() -> dict:
        state = _simulator.state
        return {
            "center": [18.5204, 73.8567],
            "junction_name": "SignalSync Smart Arterial Crossing",
            "lanes": {d.value: _lane_dict(l) for d, l in state.lanes.items()},
            "active_direction": state.active_direction.value,
            "current_phase": state.current_phase.value,
            "ambulance_active": state.ambulance_active,
            "ambulance_direction": state.ambulance_direction.value if state.ambulance_direction else None,
        }

    @application.get("/api/traffic/db_summary", tags=["reporting"])
    async def get_db_stats() -> dict:
        return get_db_summary()

    @application.get("/api/traffic/report_csv", tags=["reporting"])
    async def download_report_csv() -> Response:
        """Download real-time telemetry and KPI report as CSV."""
        state = _simulator.state
        csv_buffer = io.StringIO()
        csv_buffer.write("Timestamp,Cycle,Phase,Active_Direction,Weather,CMU_Status,North_PCU,South_PCU,East_PCU,West_PCU,Adaptive_Avg_Wait_s,Fixed_Avg_Wait_s,Fuel_Saved_mL,CO2_Avoided_g,Value_Saved_INR,Cleared_Vehicles\n")
        
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        n_pcu = state.lanes[Direction.NORTH].pcu
        s_pcu = state.lanes[Direction.SOUTH].pcu
        e_pcu = state.lanes[Direction.EAST].pcu
        w_pcu = state.lanes[Direction.WEST].pcu
        
        csv_buffer.write(
            f"{now_str},{state.cycle_number},{state.current_phase.value},{state.active_direction.value},{state.weather},"
            f"\"{state.cmu_status}\",{n_pcu},{s_pcu},{e_pcu},{w_pcu},{state.adaptive_avg_wait},{state.fixed_avg_wait},"
            f"{state.fuel_saved_ml:.1f},{state.co2_avoided_g:.1f},{state.value_saved_inr:.2f},{state.total_vehicles_processed}\n"
        )
        
        content = csv_buffer.getvalue()
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=SignalSync_Traffic_Report_{int(time.time())}.csv"}
        )

    # ── WebSocket ─────────────────────────────────────────────
    @application.websocket("/ws/traffic")
    async def ws_traffic(ws: WebSocket) -> None:
        await _ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            _ws_manager.disconnect(ws)

    return application


app = create_app()
