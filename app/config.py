"""Central configuration.

Precedence (highest first): environment variables / .env  >  configs/config.yaml
>  built-in defaults. Nothing about a specific video, ROI or timing is hard-coded
in the code base; it all flows through this module.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Union

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SAFETY_NOTICE = (
    "Academic/simulation prototype. NOT connected to real traffic lights and NOT "
    "approved for public roads. Extensive safety validation and certified "
    "traffic-control hardware are required before any real-world use."
)


class AppSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(8000, ge=1, le=65535)
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid log level: {v}")
        return v


class VideoSettings(BaseModel):
    # File path, webcam index as string ("0"), or rtsp/http(s) URL.
    source: str = "sample_data/traffic.mp4"
    loop: bool = False
    max_reconnect_attempts: int = Field(3, ge=0)
    reconnect_delay_s: float = Field(2.0, ge=0)
    allowed_extensions: list[str] = [".mp4", ".avi", ".mov", ".mkv"]


class PreprocessingSettings(BaseModel):
    frame_width: int = Field(1280, gt=0)
    frame_height: int = Field(720, gt=0)
    frame_skip: int = Field(2, ge=1, description="Process every Nth frame (1 = all).")


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///./smart_traffic.db"


class PathSettings(BaseModel):
    roi_file: str = "configs/rois.json"
    output_dir: str = "outputs"


class Settings(BaseModel):
    app: AppSettings = AppSettings()
    video: VideoSettings = VideoSettings()
    preprocessing: PreprocessingSettings = PreprocessingSettings()
    database: DatabaseSettings = DatabaseSettings()
    paths: PathSettings = PathSettings()
    safety_notice: str = SAFETY_NOTICE


# (env var name) -> (section, key, converter)
def _to_bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


_ENV_MAP: dict[str, tuple[str, str, Any]] = {
    "APP_HOST": ("app", "host", str),
    "APP_PORT": ("app", "port", int),
    "LOG_LEVEL": ("app", "log_level", str),
    "VIDEO_SOURCE": ("video", "source", str),
    "VIDEO_LOOP": ("video", "loop", _to_bool),
    "VIDEO_MAX_RECONNECT_ATTEMPTS": ("video", "max_reconnect_attempts", int),
    "VIDEO_RECONNECT_DELAY_S": ("video", "reconnect_delay_s", float),
    "FRAME_WIDTH": ("preprocessing", "frame_width", int),
    "FRAME_HEIGHT": ("preprocessing", "frame_height", int),
    "FRAME_SKIP": ("preprocessing", "frame_skip", int),
    "DATABASE_URL": ("database", "url", str),
    "ROI_FILE": ("paths", "roi_file", str),
}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        logger.warning("Config file %s not found; using defaults.", path)
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Top level of {path} must be a mapping.")
    return data


def load_settings(config_file: Union[str, Path, None] = None) -> Settings:
    """Build Settings from YAML + environment. Raises ValueError if invalid."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    cfg_path = Path(config_file or os.getenv("CONFIG_FILE", "configs/config.yaml"))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    data = _load_yaml(cfg_path)

    for env_name, (section, key, conv) in _ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        try:
            data.setdefault(section, {})[key] = conv(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_name}: {raw!r}") from exc

    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def resolve_path(path: Union[str, Path]) -> Path:
    """Resolve a config-relative path against the project root."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
