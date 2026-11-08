"""Content-addressed blob store — sha256-named immutable files."""

from __future__ import annotations

import hashlib
from pathlib import Path


class FileBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        ref = f"sha256:{digest}"
        path = self._root / digest
        if not path.exists():  # same content → same path, never rewritten
            path.write_bytes(content)
        return ref

    def get(self, blob_ref: str) -> bytes:
        digest = blob_ref.removeprefix("sha256:")
        return (self._root / digest).read_bytes()
