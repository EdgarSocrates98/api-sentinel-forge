"""Ordered, idempotent, checksummed SQL migrations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_.*\.sql$")


def discover(migrations_dir: Path | None = None) -> list[tuple[int, str, Path]]:
    root = migrations_dir or MIGRATIONS_DIR
    found = []
    for path in sorted(root.glob("*.sql")):
        m = _MIGRATION_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path.stem, path))
    return sorted(found)


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {row["version"] for row in rows}


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> list[int]:
    """Apply pending migrations; returns versions applied this call."""
    applied = applied_versions(conn)
    now_applied: list[int] = []
    for version, name, path in discover(migrations_dir):
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nCOMMIT;\n")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
            " VALUES (?, ?, ?, ?)",
            (version, name, checksum, datetime.now(UTC).isoformat()),
        )
        now_applied.append(version)
    return now_applied
