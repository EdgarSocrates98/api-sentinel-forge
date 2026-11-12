"""Suite-wide offline guarantee (Cycle 9 gate, docs/13).

Every test runs with off-box egress blocked: `socket.socket.connect` /
`connect_ex` and `socket.create_connection` raise for any non-loopback
address. Loopback self-connects pass — on Windows the asyncio proactor
event loop builds its self-pipe via `socket.socketpair()`'s loopback
fallback, which the in-process FastAPI TestClient needs; no test may use
that allowance for real egress because the suite spawns no servers.
External egress is exactly what the offline invariant forbids.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from typing import Any, NoReturn

import pytest


def _is_loopback_host(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _offline(*_args: object, **_kwargs: object) -> NoReturn:
    raise OSError(
        "network access is blocked in tests — declare connector egress "
        "explicitly instead of letting the suite go online"
    )


def _guard_connect(sock: socket.socket, address: Any) -> None:
    # address: (host, port) for INET; anything non-loopback is egress.
    host = address[0] if isinstance(address, tuple) and address else address
    if _is_loopback_host(host):
        return None
    return _offline()


@pytest.fixture(autouse=True, scope="session")
def _block_network() -> Iterator[None]:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection

    def connect(sock: socket.socket, address: Any) -> None:
        _guard_connect(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        _guard_connect(sock, address)
        return original_connect_ex(sock, address)

    def create_connection(address: Any, *args: object, **kwargs: object) -> socket.socket:
        _guard_connect(None, address)  # type: ignore[arg-type]
        return original_create(address, *args, **kwargs)  # type: ignore[arg-type]

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create  # type: ignore[assignment]
