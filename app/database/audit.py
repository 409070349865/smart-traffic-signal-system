"""
Structured Activity & Security Audit Trail Logger for SignalSync.
Logs administrative, operational, and automated system events to SQLite and in-memory cache.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from app.database.db import get_connection

logger = logging.getLogger(__name__)

# Keep in-memory ring buffer of the 50 most recent audit logs for fast UI rendering
_recent_audit_logs: List[Dict[str, Any]] = []


def init_audit_table() -> None:
    """Create audit trail table in SQLite if not exists."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                ip_address TEXT
            )
        """)
        conn.commit()


def log_activity(
    username: str,
    role: str,
    action: str,
    details: str,
    ip_address: Optional[str] = "127.0.0.1",
) -> Dict[str, Any]:
    """Record an audit trail event."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": timestamp,
        "username": username,
        "role": role,
        "action": action,
        "details": details,
        "ip_address": ip_address,
    }

    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO audit_trail (timestamp, username, role, action, details, ip_address)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (timestamp, username, role, action, details, ip_address))
            conn.commit()
    except Exception as e:
        logger.error("Failed to write audit trail to SQLite: %s", e)

    _recent_audit_logs.insert(0, entry)
    if len(_recent_audit_logs) > 60:
        _recent_audit_logs.pop()

    logger.info("AUDIT | %s (%s) | %s | %s", username, role, action, details)
    return entry


def get_recent_audit_logs(limit: int = 20) -> List[Dict[str, Any]]:
    """Fetch the latest audit trail entries."""
    if _recent_audit_logs:
        return _recent_audit_logs[:limit]

    # Fallback to SQLite query
    logs = []
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?", (limit,))
            for row in cur.fetchall():
                logs.append({
                    "timestamp": row["timestamp"],
                    "username": row["username"],
                    "role": row["role"],
                    "action": row["action"],
                    "details": row["details"],
                    "ip_address": row["ip_address"],
                })
    except Exception as e:
        logger.error("Failed reading audit trail from SQLite: %s", e)

    return logs


# Initialize table on import
try:
    init_audit_table()
    # Seed initial system audit logs
    if not _recent_audit_logs:
        log_activity("system", "SYSTEM", "SYSTEM_STARTUP", "SignalSync Traffic Signal Controller 2.0 initialized with CMU hardware interlock.")
        log_activity("admin", "ADMIN", "POLICY_VERIFY", "Anti-starvation 1.65x aging multiplier and weather scaling clearance rules active.")
except Exception:
    pass
