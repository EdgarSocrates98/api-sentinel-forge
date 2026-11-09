"""Domain errors — typed, stable codes, JSON-serializable details."""

from __future__ import annotations

from typing import Any


class SentinelError(Exception):
    """Base error; every failure carries a stable machine-readable code."""

    code: str = "sentinel.error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationError(SentinelError):
    code = "sentinel.validation"


class NotFoundError(SentinelError):
    code = "sentinel.not_found"


class ConflictError(SentinelError):
    code = "sentinel.conflict"


class ContractError(SentinelError):
    code = "sentinel.contract"


class CapabilityUnavailableError(SentinelError):
    """Named refusal — never simulate an absent integration."""

    code = "sentinel.capability_unavailable"


class WorkspaceError(SentinelError):
    code = "sentinel.workspace"


class StorageError(SentinelError):
    code = "sentinel.storage"


class UnauthorizedError(SentinelError):
    """Authentication failure — missing, bad, expired or untrusted
    credentials. Raised before any scope check or resource lookup; the
    `reason` detail names the failure class, never the credential."""

    code = "sentinel.unauthorized"


class ForbiddenError(SentinelError):
    """Scope/role denial — raised before resource lookup, never leaks existence."""

    code = "sentinel.forbidden"


class ConcurrencyLimitError(SentinelError):
    """A bounded executor is saturated — a named refusal, never a silent queue."""

    code = "sentinel.concurrency_limit"
