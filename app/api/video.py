"""
Video API router — upload, process, and stream traffic video with OpenCV vehicle detection and motion tracking.

Endpoints:
  POST /api/video/upload       → save uploaded video & start processing
  POST /api/video/demo         → generate & start built-in synthetic traffic demo video
  GET  /api/video/status       → current processing status
  POST /api/video/stop         → stop processing
  POST /api/video/sync         → toggle syncing detected vehicle counts into traffic simulator
  WS   /ws/video               → real-time base64 JPEG frame stream & telemetry
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["video"])
video_ws_router = APIRouter()

# ─── Shared state ────────────────────────────────────────── #
_processor: Optional[Any] = None
_process_task: Optional[asyncio.Task] = None
_video_ws_clients: list[WebSocket] = []
_last_frame_result: Optional[Any] = None
_sync_to_signal: bool = True

_status = {
    "running":         False,
    "source":          None,
    "frame_idx":       0,
    "vehicle_count":   0,
    "fps":             0.0,
    "quadrant_counts": {"NORTH": 0, "SOUTH": 0, "EAST": 0, "WEST": 0},
    "sync_to_signal":  True,
}

_upload_dir = Path(tempfile.gettempdir()) / "signalsync_uploads"
_upload_dir.mkdir(exist_ok=True)
_demo_video_path = _upload_dir / "demo_traffic.mp4"


# ─── Traffic simulator hook ──────────────────────────────── #
_simulator_ref: Optional[Any] = None

def register_simulator(simulator: Any) -> None:
    global _simulator_ref
    _simulator_ref = simulator


# ─── Broadcast helper ────────────────────────────────────── #
async def _broadcast_frame(result: Any) -> None:
    """Called by VideoProcessor for each processed frame."""
    global _last_frame_result
    _last_frame_result = result

    # Update state dictionary
    _status["running"]         = True
    _status["frame_idx"]       = result.frame_idx
    _status["vehicle_count"]   = result.vehicle_count
    _status["fps"]             = result.fps
    _status["quadrant_counts"] = result.quadrant_counts

    # If linked to signal, push counts to the traffic simulator
    if _sync_to_signal and _simulator_ref is not None:
        try:
            _simulator_ref.update_from_vision(result.quadrant_counts)
        except Exception as e:
            logger.debug("Failed updating simulator from vision: %s", e)

    if not _video_ws_clients:
        return

    payload = {
        "type":            "video_frame",
        "frame_idx":       result.frame_idx,
        "vehicle_count":   result.vehicle_count,
        "quadrant_counts": result.quadrant_counts,
        "fps":             result.fps,
        "tracks":          getattr(result, "tracks_data", []),
        "jpeg_b64":        result.jpeg_b64,
    }
    data = json.dumps(payload)
    dead = []
    for ws in list(_video_ws_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)

    for ws in dead:
        if ws in _video_ws_clients:
            _video_ws_clients.remove(ws)


async def _run_processor(video_source: Any, source_name: str) -> None:
    """Background task that runs the OpenCV pipeline on video file or camera."""
    global _processor, _status
    from app.vision.processor import VideoProcessor
    _processor = VideoProcessor()
    _status["source"]  = source_name
    _status["running"] = True
    logger.info("Starting video processing: %s", source_name)
    try:
        await _processor.process_video(video_source, on_frame=_broadcast_frame, loop_video=True)
    except asyncio.CancelledError:
        logger.info("Video processing task was cancelled.")
    except Exception as e:
        logger.exception("Error during video processing: %s", e)
    finally:
        _status["running"] = False
        _status["source"]  = None
        logger.info("Video processing finished.")


def _stop_current() -> None:
    global _processor, _process_task
    if _processor:
        _processor.stop()
    if _process_task and not _process_task.done():
        _process_task.cancel()
    _processor     = None
    _process_task  = None
    _status["running"] = False


# ─── REST endpoints ──────────────────────────────────────── #
@router.post("/upload")
async def upload_video(file: UploadFile = File(...)) -> dict:
    """Upload a traffic video file and start OpenCV processing."""
    if not file.filename:
        return {"error": "No filename"}

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        return {"error": f"Unsupported format: {suffix}. Please upload MP4, AVI, MOV, MKV, or WEBM."}

    # Stop any existing video processing
    _stop_current()

    save_path = _upload_dir / f"upload_{int(time.time())}{suffix}"
    with save_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info("Uploaded video saved: %s (%d bytes)", save_path.name, save_path.stat().st_size)

    global _process_task
    _process_task = asyncio.create_task(_run_processor(save_path, file.filename))

    return {"ok": True, "filename": file.filename, "saved_as": save_path.name}


@router.post("/camera")
async def start_camera(camera_index: int = 0) -> dict:
    """Initialize live webcam feed and real-time OpenCV tracking."""
    _stop_current()
    global _process_task
    _process_task = asyncio.create_task(
        _run_processor(camera_index, f"Live Web Camera (Dev #{camera_index})")
    )
    return {"ok": True, "source": f"Live Camera #{camera_index}"}


@router.post("/demo")
async def start_demo_video() -> dict:
    """Generate (if needed) and run synthetic traffic intersection video."""
    _stop_current()

    from app.vision.generator import generate_demo_video
    generate_demo_video(_demo_video_path)

    global _process_task
    _process_task = asyncio.create_task(
        _run_processor(_demo_video_path, "Demo Intersection Feed (Synthetic)")
    )
    return {"ok": True, "source": "Demo Intersection Feed"}


@router.get("/status")
async def video_status() -> dict:
    _status["sync_to_signal"] = _sync_to_signal
    return _status


@router.post("/stop")
async def stop_video() -> dict:
    _stop_current()
    return {"ok": True}


class SyncRequest(BaseModel):
    enabled: bool = True


@router.post("/sync")
async def set_sync(req: SyncRequest) -> dict:
    global _sync_to_signal
    _sync_to_signal = req.enabled
    _status["sync_to_signal"] = _sync_to_signal
    return {"ok": True, "sync_to_signal": _sync_to_signal}


# ─── WebSocket endpoint ───────────────────────────────────── #
@video_ws_router.websocket("/ws/video")
async def ws_video(ws: WebSocket) -> None:
    await ws.accept()
    _video_ws_clients.append(ws)
    logger.info("Video WS client connected. Total: %d", len(_video_ws_clients))

    # Send last frame immediately so client doesn't see a black box
    if _last_frame_result:
        try:
            r = _last_frame_result
            await ws.send_text(json.dumps({
                "type":            "video_frame",
                "frame_idx":       r.frame_idx,
                "vehicle_count":   r.vehicle_count,
                "quadrant_counts": r.quadrant_counts,
                "fps":             r.fps,
                "tracks":          getattr(r, "tracks_data", []),
                "jpeg_b64":        r.jpeg_b64,
            }))
        except Exception:
            pass

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _video_ws_clients:
            _video_ws_clients.remove(ws)
        logger.info("Video WS client disconnected. Total: %d", len(_video_ws_clients))
