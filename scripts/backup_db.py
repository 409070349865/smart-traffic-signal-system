"""
CLI script to trigger an automated database backup of SignalSync SQLite database:
Usage:
    python scripts/backup_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.backup import create_database_backup, list_backups


def main() -> None:
    print("[SignalSync] Initiating automated database backup...")
    res = create_database_backup()
    print(f"[SignalSync] Backup created: {res['filename']} ({res['size_bytes']} bytes)")
    print("\nRecent Backups:")
    for b in list_backups()[:5]:
        print(f"  • {b['filename']} ({b['size_kb']} KB) - {b['created_at']}")


if __name__ == "__main__":
    main()
