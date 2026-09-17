import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path(os.getenv("DB_PATH", "digistore.db"))
backup_dir = Path(os.getenv("BACKUP_DIR", "backups"))
backup_dir.mkdir(parents=True, exist_ok=True)
if not source.exists():
    raise SystemExit(f"Database not found: {source}")
name = datetime.now(timezone.utc).strftime("digistore_%Y%m%d_%H%M%S_UTC.db")
destination = backup_dir / name
with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
    src.backup(dst)
print(destination)
