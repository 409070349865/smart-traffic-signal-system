"""
FastAPI entry point — Full 8-Step Intelligent Traffic Control System
  • WebSocket /ws/traffic        (real-time signal-state stream & PCU telemetry)
  • WebSocket /ws/video          (real-time OpenCV processed video frames)
  • REST /api/traffic/*          (ambulance, gridlock, ped walk, presets, timing sliders, incidents, csv report)
  • REST /api/video/*            (upload, camera, demo, status, stop, sync)
  • Static file serving of the dashboard (frontend/)
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api.video import router as video_router, video_ws_router, register_simulator
from app.config import get_settings, setup_logging
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
        
        # Emergency & Gridlock & Pedestrian
        "ambulance_active":    state.ambulance_active,
        "ambulance_direction": state.ambulance_direction.value if state.ambulance_direction else None,
        "ambulance_countdown": round(state.ambulance_countdown, 1),
        
        "yellow_box_blocked":  state.yellow_box_blocked,
        "gridlock_alert":      state.gridlock_alert,
        
        "curb_pedestrians":    state.curb_pedestrians,
        "ped_phase_active":    state.ped_phase_active,
        "ped_countdown":       round(state.ped_countdown, 1),
        
        # Multi-Junction Green Wave
        "green_wave_offset_s": state.green_wave_offset_s,
        "corridor_platoon_pcu": state.corridor_platoon_pcu,
        "corridor_eta_s":      round(state.corridor_eta_s, 1),
        
        # Eco-Telemetry
        "fuel_saved_ml":       round(state.fuel_saved_ml, 1),
        "co2_avoided_g":       round(state.co2_avoided_g, 1),
        "value_saved_inr":     round(state.value_saved_inr, 2),
        
        # Performance Evaluation
        "adaptive_avg_wait":   state.adaptive_avg_wait,
        "fixed_avg_wait":      state.fixed_avg_wait,
        "active_queue_load":   state.active_queue_load,
        "fixed_queue_load":    state.fixed_queue_load,
        "total_vehicles_processed": state.total_vehicles_processed,
        "adaptive_throughput": [round(v, 1) for v in state.adaptive_throughput],
        "fixed_throughput":    [round(v, 1) for v in state.fixed_throughput],
        
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
    max_red_wait: float = 45.0
    spawn_rate: float = 1.0


# ────────────── app factory ──────────────────────

def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.app.log_level)

    application = FastAPI(
        title="Smart Traffic Signal System",
        description="4-Way Junction Adaptive Traffic Control with OpenCV Computer Vision",
        version="1.0.0",
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
            "version": application.version,
            "ws_clients": _ws_manager.count,
        }

    # ── Control Endpoints ────────────────────────────────────
    @application.post("/api/traffic/ambulance", tags=["control"])
    async def trigger_ambulance(req: AmbulanceRequest) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Invalid direction {req.direction}"}
        _simulator.trigger_ambulance(d)
        return {"ok": True, "ambulance_direction": d.value}

    @application.post("/api/traffic/gridlock", tags=["control"])
    async def toggle_gridlock() -> dict:
        blocked = _simulator.toggle_gridlock()
        return {"ok": True, "yellow_box_blocked": blocked}

    @application.post("/api/traffic/pedestrian/add", tags=["control"])
    async def add_pedestrians(req: PedestrianRequest) -> dict:
        count = _simulator.add_pedestrian_demand(req.count)
        return {"ok": True, "curb_pedestrians": count}

    @application.post("/api/traffic/pedestrian/trigger", tags=["control"])
    async def trigger_pedestrian_phase() -> dict:
        _simulator.trigger_pedestrian_phase()
        return {"ok": True, "ped_phase": True}

    @application.post("/api/traffic/sim_incident", tags=["control"])
    async def sim_incident(req: IncidentRequest) -> dict:
        if req.type == "stalled":
            inc = _simulator.sim_stalled_vehicle()
        else:
            inc = _simulator.sim_red_runner()
        return {"ok": True, "incident": inc}

    @application.post("/api/traffic/reset", tags=["control"])
    async def reset_queues() -> dict:
        _simulator.reset_queues()
        return {"ok": True}

    @application.post("/api/traffic/next_phase", tags=["control"])
    async def next_phase() -> dict:
        _simulator.force_next_phase()
        return {"ok": True}

    @application.post("/api/traffic/scenario", tags=["control"])
    async def set_scenario(req: ScenarioRequest) -> dict:
        _simulator.apply_scenario(req.name)
        return {"ok": True, "scenario": req.name}

    @application.post("/api/traffic/inject", tags=["control"])
    async def inject_vehicles(req: InjectRequest) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Unknown direction: {req.direction}"}
        _simulator.inject_vehicles(d, req.count)
        return {"ok": True, "direction": d.value, "count": req.count}

    @application.post("/api/traffic/thresholds", tags=["control"])
    async def set_thresholds(req: ThresholdsRequest) -> dict:
        _simulator.update_timing(TimingConfig(
            t_min=req.t_min,
            t_max=req.t_max,
            yellow_s=req.yellow_s,
            all_red_s=req.all_red_s,
            max_red_wait=req.max_red_wait,
            spawn_rate=req.spawn_rate,
        ))
        return {"ok": True}

    # Backward compatibility
    @application.post("/api/traffic/timing", tags=["control"])
    async def set_timing(req: ThresholdsRequest) -> dict:
        return await set_thresholds(req)

    @application.get("/api/traffic/state", tags=["data"])
    async def get_state() -> dict:
        state = _simulator.tick()
        return _state_payload(state)

    @application.get("/api/traffic/report_csv", tags=["reporting"])
    async def download_report_csv() -> Response:
        """Download real-time telemetry and KPI report as CSV."""
        state = _simulator.state
        csv_buffer = io.StringIO()
        csv_buffer.write("Timestamp,Cycle,Phase,Active_Direction,North_PCU,South_PCU,East_PCU,West_PCU,Adaptive_Avg_Wait_s,Fixed_Avg_Wait_s,Fuel_Saved_mL,CO2_Avoided_g,Value_Saved_INR,Cleared_Vehicles\n")
        
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        n_pcu = state.lanes[Direction.NORTH].pcu
        s_pcu = state.lanes[Direction.SOUTH].pcu
        e_pcu = state.lanes[Direction.EAST].pcu
        w_pcu = state.lanes[Direction.WEST].pcu
        
        csv_buffer.write(
            f"{now_str},{state.cycle_number},{state.current_phase.value},{state.active_direction.value},"
            f"{n_pcu},{s_pcu},{e_pcu},{w_pcu},{state.adaptive_avg_wait},{state.fixed_avg_wait},"
            f"{state.fuel_saved_ml:.1f},{state.co2_avoided_g:.1f},{state.value_saved_inr:.2f},{state.total_vehicles_processed}\n"
        )
        
        # Add summary rows
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

    @application.on_event("startup")
    async def startup() -> None:
        asyncio.create_task(_broadcast_loop())
        logger.info("Traffic simulator background task started.")

    return application


app = create_app()
