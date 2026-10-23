"""
Automated Tests for SQLite Database Online Backup & Disaster Recovery.
"""
from pathlib import Path
import pytest

from app.database.backup import BACKUP_DIR, create_database_backup, list_backups


def test_database_backup_creation():
    res = create_database_backup()
    assert res["success"] is True
    assert "filename" in res
    assert res["size_bytes"] > 0
    backup_file = BACKUP_DIR / res["filename"]
    assert backup_file.is_file()


def test_list_backups():
    backups = list_backups()
    assert isinstance(backups, list)
    assert len(backups) >= 1
    assert "filename" in backups[0]
    assert "created_at" in backups[0]
