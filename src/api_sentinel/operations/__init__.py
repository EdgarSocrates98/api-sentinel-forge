"""Operations — backup/restore, retention enforcement, self-observability.

Cycle 9 production hardening (docs/10, docs/15, docs/17): the procedures the
runbook documents, implemented as deterministic functions over a workspace —
no new subsystem, no external calls.
"""

from api_sentinel.operations.backup import (
    BackupResult,
    RestoreResult,
    create_backup,
    read_manifest,
    restore_backup,
    verify_backup,
)
from api_sentinel.operations.health import sentinel_health
from api_sentinel.operations.retention import (
    RetentionPolicy,
    RetentionReport,
    run_retention,
)

__all__ = [
    "BackupResult",
    "RestoreResult",
    "RetentionPolicy",
    "RetentionReport",
    "create_backup",
    "read_manifest",
    "restore_backup",
    "run_retention",
    "sentinel_health",
    "verify_backup",
]
