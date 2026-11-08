"""Prefixed sortable ids — stdlib ULID (no third-party dep)."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Protocol

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(now_ms: int | None = None) -> str:
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    return _encode(ts, 10) + _encode(secrets.randbits(80), 16)


class IdFactory(Protocol):
    def new(self, prefix: str) -> str: ...


class UlidIdFactory:
    """Produces `<prefix>_<ulid>` e.g. svc_01J…; inject for tests."""

    def __init__(self, clock_now_ms: Callable[[], int] | None = None) -> None:
        self._now_ms = clock_now_ms or (lambda: int(time.time() * 1000))

    def new(self, prefix: str) -> str:
        return f"{prefix}_{new_ulid(self._now_ms())}"


class SequentialIdFactory:
    """Deterministic ids for tests: svc_0001, svc_0002, …"""

    def __init__(self) -> None:
        self._n = 0

    def new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:04d}"
