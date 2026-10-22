"""Video input: recorded files, webcams and IP/RTSP cameras behind one interface.

Typical use::

    with VideoInput("sample_data/traffic.mp4") as vin:
        while True:
            status, frame = vin.read_frame()
            if status is ReadStatus.END_OF_VIDEO:
                break
            if status is ReadStatus.OK:
                ...
"""
from __future__ import annotations

import enum
import logging
import time
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np

from app.config import VideoSettings, resolve_path

logger = logging.getLogger(__name__)

Source = Union[str, int, Path]


class VideoSourceError(Exception):
    """Raised when a video source is invalid, unsupported or cannot be opened."""


class SourceType(str, enum.Enum):
    FILE = "file"
    WEBCAM = "webcam"
    STREAM = "stream"


class ReadStatus(str, enum.Enum):
    OK = "ok"
    END_OF_VIDEO = "end_of_video"      # recorded file finished (and loop disabled)
    CORRUPTED = "corrupted"            # too many consecutive unreadable/empty frames
    DISCONNECTED = "disconnected"      # live source lost and reconnect failed
    NOT_OPENED = "not_opened"          # read_frame called before open_video()


_STREAM_PREFIXES = ("rtsp://", "rtmp://", "http://", "https://", "udp://", "tcp://")
_DEFAULT_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv")


def classify_source(source: Source) -> SourceType:
    """Decide whether *source* is a webcam index, a stream URL or a file path."""
    if isinstance(source, int):
        return SourceType.WEBCAM
    text = str(source).strip()
    if text.isdigit():
        return SourceType.WEBCAM
    if text.lower().startswith(_STREAM_PREFIXES):
        return SourceType.STREAM
    return SourceType.FILE


class VideoInput:
    """Unified video source with validation, reconnect and corrupt-frame handling."""

    def __init__(
        self,
        source: Source,
        loop: bool = False,
        max_reconnect_attempts: int = 3,
        reconnect_delay_s: float = 2.0,
        allowed_extensions: Optional[list[str]] = None,
        max_consecutive_bad_frames: int = 10,
    ) -> None:
        self.source = source
        self.source_type = classify_source(source)
        self.loop = loop
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay_s = reconnect_delay_s
        self.allowed_extensions = tuple(
            e.lower() for e in (allowed_extensions or _DEFAULT_EXTENSIONS)
        )
        self.max_consecutive_bad_frames = max_consecutive_bad_frames

        self._cap: Optional[cv2.VideoCapture] = None
        self._bad_frames = 0
        self._frames_read = 0

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_settings(cls, settings: VideoSettings) -> "VideoInput":
        return cls(
            source=settings.source,
            loop=settings.loop,
            max_reconnect_attempts=settings.max_reconnect_attempts,
            reconnect_delay_s=settings.reconnect_delay_s,
            allowed_extensions=settings.allowed_extensions,
        )

    def _resolved_source(self) -> Union[str, int]:
        if self.source_type is SourceType.WEBCAM:
            return int(self.source)
        if self.source_type is SourceType.STREAM:
            return str(self.source)
        return str(resolve_path(str(self.source)))

    def _validate_file(self, path: Path) -> None:
        if path.suffix.lower() not in self.allowed_extensions:
            raise VideoSourceError(
                f"Unsupported video format '{path.suffix}'. "
                f"Allowed: {', '.join(self.allowed_extensions)}"
            )
        if not path.is_file():
            raise VideoSourceError(f"Video file not found: {self.source}")

    # --------------------------------------------------------------- lifecycle
    def open_video(self) -> None:
        """Open the source. Raises VideoSourceError with a user-friendly message."""
        self.release()
        resolved = self._resolved_source()
        if self.source_type is SourceType.FILE:
            self._validate_file(Path(resolved))

        try:
            cap = cv2.VideoCapture(resolved)
        except Exception as exc:  # OpenCV can raise on malformed URLs
            raise VideoSourceError(f"Could not open video source: {exc}") from exc

        if not cap.isOpened():
            cap.release()
            kind = {"file": "video file", "webcam": "camera", "stream": "stream"}[
                self.source_type.value
            ]
            raise VideoSourceError(f"Unable to open {kind}: {self._safe_name()}")

        self._cap = cap
        self._bad_frames = 0
        self._frames_read = 0
        w, h = self.get_resolution()
        logger.info("Opened %s source '%s' (%dx%d @ %.1f fps)",
                    self.source_type.value, self._safe_name(), w, h, self.get_fps())

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def __enter__(self) -> "VideoInput":
        self.open_video()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    # ------------------------------------------------------------------ reading
    def read_frame(self) -> tuple[ReadStatus, Optional[np.ndarray]]:
        """Return (status, frame). frame is None unless status is OK.

        Handles: end of file (optional looping), unreadable/empty frames (skipped
        until max_consecutive_bad_frames), and live-source drop-outs (auto
        reconnect).
        """
        if not self.is_opened():
            return ReadStatus.NOT_OPENED, None
        assert self._cap is not None

        while True:
            try:
                ok, frame = self._cap.read()
            except cv2.error as exc:
                logger.warning("OpenCV error while reading frame: %s", exc)
                ok, frame = False, None

            if ok and frame is not None and frame.size > 0:
                self._bad_frames = 0
                self._frames_read += 1
                return ReadStatus.OK, frame

            # ---- failed read ----
            if self.source_type is SourceType.FILE:
                if self._at_end_of_file():
                    if self.loop and self._frames_read > 0:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self._frames_read = 0
                        continue
                    return ReadStatus.END_OF_VIDEO, None
                # Not at end: genuinely corrupt frame
                self._bad_frames += 1
                logger.warning("Corrupted frame skipped (%d consecutive)", self._bad_frames)
                if self._bad_frames >= self.max_consecutive_bad_frames:
                    return ReadStatus.CORRUPTED, None
                continue

            # Live source (webcam / stream)
            self._bad_frames += 1
            if self._bad_frames < 3:      # tolerate brief glitches before reconnecting
                continue
            logger.warning("Live source lost; attempting to reconnect.")
            if self.reconnect():
                continue
            return ReadStatus.DISCONNECTED, None

    def _at_end_of_file(self) -> bool:
        assert self._cap is not None
        total = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        pos = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
        if total <= 0:               # container doesn't report length
            return True
        return pos >= total - 1

    # ---------------------------------------------------------------- reconnect
    def reconnect(self) -> bool:
        """Try to re-open the source up to max_reconnect_attempts times."""
        for attempt in range(1, self.max_reconnect_attempts + 1):
            logger.info("Reconnect attempt %d/%d", attempt, self.max_reconnect_attempts)
            try:
                self.open_video()
                return True
            except VideoSourceError as exc:
                logger.warning("Reconnect failed: %s", exc)
                if attempt < self.max_reconnect_attempts:
                    time.sleep(self.reconnect_delay_s)
        return False

    # ------------------------------------------------------------------ metadata
    def get_fps(self) -> float:
        if self._cap is None:
            return 0.0
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return fps if 0 < fps < 1000 else 25.0   # fall back when metadata is missing

    def get_resolution(self) -> tuple[int, int]:
        """(width, height) in pixels, or (0, 0) if not open."""
        if self._cap is None:
            return 0, 0
        return (int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0))

    def get_frame_count(self) -> int:
        """Total frames for recorded files; 0 for live sources/unknown."""
        if self._cap is None or self.source_type is not SourceType.FILE:
            return 0
        return max(int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0), 0)

    @property
    def is_live(self) -> bool:
        return self.source_type is not SourceType.FILE

    def _safe_name(self) -> str:
        """Source label for logs/errors; hides credentials embedded in URLs."""
        text = str(self.source)
        if "@" in text and "://" in text:
            scheme, rest = text.split("://", 1)
            return f"{scheme}://***@{rest.split('@', 1)[1]}"
        return text
