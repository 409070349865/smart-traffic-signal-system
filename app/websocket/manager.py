"""WebSocket manager — broadcasts real-time traffic state to all connected clients."""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Keeps a set of active WebSocket connections and broadcasts to all."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS client connected. Total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]
        logger.info("WS client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Broadcast JSON to every connected client; drop dead connections."""
        dead: list[WebSocket] = []
        payload = _safe_json(data)
        for ws in list(self._connections):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


def _safe_json(obj: Any) -> str:
    """JSON-serialize, replacing non-finite floats with null."""
    def default(o: Any) -> Any:
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                return None
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")

    return json.dumps(obj, default=default)
