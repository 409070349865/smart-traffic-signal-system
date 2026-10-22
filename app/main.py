"""
FastAPI entry point — Phase 1-7 complete with:
  • /api/health
  • WebSocket /ws/traffic  (real-time state stream)
  • REST  /api/traffic/inject, /api/traffic/scenario, /api/traffic/timing
  • Static file serving of the dashboard (frontend/)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_settings, setup_logging
from app.traffic.simulator import (
    Direction, TimingConfig, TrafficSimulator, SCENARIO_PRESETS
)
from app.websocket.manager import ConnectionManager

logger = logging.getLogger(__name__)

# ────────────── singletons ───────────────
_simulator  = TrafficSimulator()
_ws_manager = ConnectionManager()

TICK_INTERVAL = 0.5          # seconds between simulation ticks & WS broadcasts
FRONTEND_DIR  = Path(__file__).resolve().parent.parent / "frontend"


# ────────────── helpers ──────────────────

def _lane_dict(lane: Any) -> dict:
    return {
        "direction":    lane.direction.value,
        "vehicle_count": lane.vehicle_count,
        "density":      lane.density,
        "t_green":      lane.t_green,
        "wait_time":    lane.wait_time,
        "phase":        lane.phase.value,
        "cars":         lane.cars,
        "buses":        lane.buses,
        "motorcycles":  lane.motorcycles,
    }


def _state_payload(state: Any) -> dict:
    return {
        "type": "state",
        "lanes": {d.value: _lane_dict(l) for d, l in state.lanes.items()},
        "active_direction":    state.active_direction.value,
        "current_phase":       state.current_phase.value,
        "phase_remaining":     round(state.phase_remaining, 1),
        "cycle_number":        state.cycle_number,
        "adaptive_avg_wait":   state.adaptive_avg_wait,
        "fixed_avg_wait":      state.fixed_avg_wait,
        "total_vehicles_processed": state.total_vehicles_processed,
        "adaptive_throughput": [round(v, 1) for v in state.adaptive_throughput],
        "fixed_throughput":    [round(v, 1) for v in state.fixed_throughput],
        "timing": {
            "t_min":     state.timing.t_min,
            "t_max":     state.timing.t_max,
            "yellow_s":  state.timing.yellow_s,
            "all_red_s": state.timing.all_red_s,
        },
        "scenarios": list(SCENARIO_PRESETS.keys()),
    }


# ────────────── background ticker ────────────────

async def _broadcast_loop() -> None:
    """Runs as a background task — ticks the simulator and pushes state."""
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


class TimingRequest(BaseModel):
    t_min: float = 10.0
    t_max: float = 60.0
    yellow_s: float = 3.0
    all_red_s: float = 2.0


# ────────────── app factory ──────────────────────

def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.app.log_level)

    application = FastAPI(
        title="Smart Traffic Signal System",
        description=settings.safety_notice,
        version="0.7.0",
    )

    # ── Static files (CSS / JS / assets) ─────────────────────────────────────
    if FRONTEND_DIR.is_dir():
        application.mount(
            "/static",
            StaticFiles(directory=str(FRONTEND_DIR)),
            name="static",
        )

    # ── Dashboard HTML ────────────────────────────────────────────────────────
    dashboard_html = FRONTEND_DIR / "index.html"

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> HTMLResponse:
        if dashboard_html.is_file():
            return HTMLResponse(dashboard_html.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Dashboard not found – run frontend build</h1>", 404)

    # ── Health ────────────────────────────────────────────────────────────────
    @application.get("/api/health", tags=["system"])
    async def health() -> dict:
        return {
            "status": "ok",
            "version": application.version,
            "ws_clients": _ws_manager.count,
            "notice": settings.safety_notice,
        }

    # ── REST control endpoints ────────────────────────────────────────────────
    @application.post("/api/traffic/inject", tags=["control"])
    async def inject_vehicles(req: InjectRequest) -> dict:
        try:
            d = Direction(req.direction.upper())
        except ValueError:
            return {"error": f"Unknown direction: {req.direction}"}
        _simulator.inject_vehicles(d, req.count)
        return {"ok": True, "direction": d.value, "count": req.count}

    @application.post("/api/traffic/scenario", tags=["control"])
    async def set_scenario(req: ScenarioRequest) -> dict:
        _simulator.apply_scenario(req.name)
        return {"ok": True, "scenario": req.name}

    @application.post("/api/traffic/timing", tags=["control"])
    async def set_timing(req: TimingRequest) -> dict:
        _simulator.update_timing(TimingConfig(
            t_min=req.t_min,
            t_max=req.t_max,
            yellow_s=req.yellow_s,
            all_red_s=req.all_red_s,
        ))
        return {"ok": True}

    @application.get("/api/traffic/state", tags=["data"])
    async def get_state() -> dict:
        state = _simulator.tick()
        return _state_payload(state)

    # ── WebSocket ─────────────────────────────────────────────────────────────
    @application.websocket("/ws/traffic")
    async def ws_traffic(ws: WebSocket) -> None:
        await _ws_manager.connect(ws)
        try:
            while True:
                # Keep connection alive; actual data is pushed by _broadcast_loop
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            _ws_manager.disconnect(ws)

    # ── Startup / shutdown ────────────────────────────────────────────────────
    @application.on_event("startup")
    async def startup() -> None:
        asyncio.create_task(_broadcast_loop())
        logger.info("Traffic simulator background task started.")

    logger.info("Application created (v%s).", application.version)
    return application


app = create_app()
