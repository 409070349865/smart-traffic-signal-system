"""
SQLite Database Layer for SignalSync Traffic Telemetry, Incidents, and Rush-Hour Learning.
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import os
logger = logging.getLogger(__name__)

_default_db = Path(__file__).resolve().parent.parent.parent / "smart_traffic.db"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(_default_db)))
try:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize SQLite database tables for telemetry, incidents, and hourly PCU learning."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Cycle telemetry log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cycle_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cycle_number INTEGER NOT NULL,
                active_direction TEXT NOT NULL,
                phase TEXT NOT NULL,
                allocated_green REAL NOT NULL,
                north_pcu REAL NOT NULL,
                south_pcu REAL NOT NULL,
                east_pcu REAL NOT NULL,
                west_pcu REAL NOT NULL,
                adaptive_avg_wait REAL NOT NULL,
                fixed_avg_wait REAL NOT NULL,
                fuel_saved_ml REAL NOT NULL,
                co2_avoided_g REAL NOT NULL,
                value_saved_inr REAL NOT NULL,
                total_vehicles_processed INTEGER NOT NULL
            )
        """)

        # 2. Safety & violation incidents
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incident_records (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                description TEXT NOT NULL
            )
        """)

        # 3. Rush-hour hourly PCU learning table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hourly_pcu_learning (
                hour INTEGER PRIMARY KEY,
                sample_count INTEGER DEFAULT 0,
                avg_pcu REAL DEFAULT 0.0,
                is_rush_hour INTEGER DEFAULT 0,
                boosted_t_min REAL DEFAULT 10.0,
                last_updated TEXT
            )
        """)

        # Seed 24 hours if empty
        cursor.execute("SELECT COUNT(*) FROM hourly_pcu_learning")
        if cursor.fetchone()[0] == 0:
            for h in range(24):
                # Peak rush hours typically 8-10 AM and 5-8 PM
                is_peak = 1 if (8 <= h <= 10 or 17 <= h <= 20) else 0
                avg_pcu = 18.5 if is_peak else 6.0
                t_min = 15.0 if is_peak else 10.0
                cursor.execute("""
                    INSERT INTO hourly_pcu_learning (hour, sample_count, avg_pcu, is_rush_hour, boosted_t_min, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (h, 10, avg_pcu, is_peak, t_min, datetime.datetime.now().isoformat()))

        conn.commit()
    logger.info("Database initialized successfully at %s", DB_PATH)


def record_cycle(
    cycle_number: int,
    active_direction: str,
    phase: str,
    allocated_green: float,
    north_pcu: float,
    south_pcu: float,
    east_pcu: float,
    west_pcu: float,
    adaptive_avg_wait: float,
    fixed_avg_wait: float,
    fuel_saved_ml: float,
    co2_avoided_g: float,
    value_saved_inr: float,
    total_vehicles_processed: int,
) -> None:
    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO cycle_telemetry (
                    timestamp, cycle_number, active_direction, phase, allocated_green,
                    north_pcu, south_pcu, east_pcu, west_pcu,
                    adaptive_avg_wait, fixed_avg_wait,
                    fuel_saved_ml, co2_avoided_g, value_saved_inr, total_vehicles_processed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_str, cycle_number, active_direction, phase, allocated_green,
                north_pcu, south_pcu, east_pcu, west_pcu,
                adaptive_avg_wait, fixed_avg_wait,
                fuel_saved_ml, co2_avoided_g, value_saved_inr, total_vehicles_processed
            ))
            conn.commit()
    except Exception as e:
        logger.error("Failed to record cycle to SQLite: %s", e)


def record_incident(incident_id: str, incident_type: str, direction: str, description: str, timestamp: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO incident_records (id, timestamp, incident_type, direction, description)
                VALUES (?, ?, ?, ?, ?)
            """, (incident_id, timestamp, incident_type, direction, description))
            conn.commit()
    except Exception as e:
        logger.error("Failed to record incident to SQLite: %s", e)


def get_rush_hour_learning_for_current_hour() -> Dict[str, Any]:
    """Retrieve learned rush hour metrics and minimum green recommendation."""
    current_hour = datetime.datetime.now().hour
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM hourly_pcu_learning WHERE hour = ?", (current_hour,))
            row = cur.fetchone()
            if row:
                return {
                    "hour": row["hour"],
                    "avg_pcu": row["avg_pcu"],
                    "is_rush_hour": bool(row["is_rush_hour"]),
                    "boosted_t_min": row["boosted_t_min"],
                }
    except Exception as e:
        logger.error("Failed to read rush hour learning: %s", e)

    is_peak = (8 <= current_hour <= 10 or 17 <= current_hour <= 20)
    return {
        "hour": current_hour,
        "avg_pcu": 16.0 if is_peak else 6.0,
        "is_rush_hour": is_peak,
        "boosted_t_min": 15.0 if is_peak else 10.0,
    }


def get_db_summary() -> Dict[str, Any]:
    """Summary of database records."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM cycle_telemetry")
            cycles = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM incident_records")
            incidents = cur.fetchone()[0]
            cur.execute("SELECT AVG(adaptive_avg_wait), AVG(fixed_avg_wait) FROM cycle_telemetry")
            waits = cur.fetchone()
            return {
                "total_cycles_logged": cycles,
                "total_incidents_logged": incidents,
                "avg_adaptive_wait": round(waits[0] or 0.0, 1),
                "avg_fixed_wait": round(waits[1] or 0.0, 1),
            }
    except Exception as e:
        logger.error("Failed to fetch db summary: %s", e)
        return {"total_cycles_logged": 0, "total_incidents_logged": 0}
