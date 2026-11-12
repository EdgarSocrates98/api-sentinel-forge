"""Path + size guards for handoff and ingest surfaces (Cycle-10 §5.2).

Every workspace-managed write derives a directory slot from an id that
*could* come from a peer's envelope — a crafted ``../`` or a pre-planted
symlink must never escape the authorized root. Every peer-supplied file
read is bounded so a hostile bundle can't exhaust memory.
"""

from __future__ import annotations

from pathlib import Path, PurePath

from api_sentinel.domain import ValidationError

#: Inbound peer payloads (handoff bundles, manifests, exports) are never
#: larger than this — a bigger file is a typed refusal, not a slow read.
MAX_INBOUND_BYTES = 32 * 1024 * 1024  # 32 MiB


def safe_child_name(value: str, *, name: str = "identifier") -> str:
    """A single path segment that can never traverse.

    Refuses ``..``, separators, drive/UNC shapes and empty values — the
    returned string is safe to join under an authorized root.
    """
    text = str(value)
    pure = PurePath(text)
    if (
        not text
        or text in (".", "..")
        or pure.name != text
        or "/" in text
        or "\\" in text
        or ":" in text
        or text.startswith(("~", "."))
    ):
        raise ValidationError(
            f"unsafe {name}: {value!r} — a single path segment is required",
            details={name: value},
        )
    return text


def resolve_under_root(
    root: Path,
    candidate: Path,
    *,
    name: str = "path",
) -> Path:
    """Canonicalize `candidate` and prove it stays under `root`.

    `Path.resolve()` collapses ``..`` *and* dereferences symlinks, so a
    pre-planted symlinked slot directory is caught the same way a literal
    ``../escape`` is.
    """
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValidationError(
            f"{name} escapes the authorized root: {candidate!r}",
            details={name: str(candidate), "root": str(root)},
        )
    return resolved


def safe_slot(root: Path, child_name: str, *, name: str = "identifier") -> Path:
    """`root / child_name` with both guards applied (segment + resolve)."""
    segment = safe_child_name(child_name, name=name)
    return resolve_under_root(root, root / segment, name=name)


def read_text_bounded(
    path: Path,
    *,
    max_bytes: int = MAX_INBOUND_BYTES,
    name: str = "file",
) -> str:
    """Bounded peer-file read — oversized input is a typed refusal."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValidationError(
            f"{name} is not readable: {path}", details={"error": str(exc)}
        ) from exc
    if size > max_bytes:
        raise ValidationError(
            f"{name} exceeds the {max_bytes}-byte inbound limit ({size} bytes): {path}",
            details={name: str(path), "size_bytes": size},
        )
    return path.read_text(encoding="utf-8")
