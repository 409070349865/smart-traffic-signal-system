"""
Database Backup & Disaster Recovery Utility for SignalSync SQLite Database.
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from app.database.db import DB_PATH

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def create_database_backup() -> Dict[str, Any]:
    """Perform a transactionally safe online SQLite database backup."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Source database file does not exist: {DB_PATH}")

    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"smart_traffic_{timestamp_str}.bak"
    backup_path = BACKUP_DIR / backup_filename

    # Use SQLite native online backup API for zero-downtime consistency
    src_conn = sqlite3.connect(str(DB_PATH))
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn, pages=100)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    file_size = backup_path.stat().st_size
    logger.info("Database backup created successfully: %s (%d bytes)", backup_filename, file_size)

    return {
        "success": True,
        "filename": backup_filename,
        "path": str(backup_path),
        "size_bytes": file_size,
        "timestamp": datetime.datetime.now().isoformat(),
    }


def list_backups() -> List[Dict[str, Any]]:
    """List all available historical backups sorted newest first."""
    backups = []
    for f in sorted(BACKUP_DIR.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True):
        backups.append({
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created_at": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return backups
