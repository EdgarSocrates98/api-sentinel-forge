"""Local-API authentication — workspace token + signed integrated principals.

Two modes (docs/07, Cycle-10 hardening):

- **local**: a workspace-generated bearer token (`secrets.token_urlsafe`)
  stored at `<workspace>/token`, written with best-effort `0600` perms —
  on Windows `os.chmod` maps to the read-only attribute, so the real
  protection there is the user's NTFS ACLs; the file is never committed
  and never logged. Comparison is `hmac.compare_digest`. Precedence at
  resolve time: `API_SENTINEL_TOKEN` env > `--token-stdin` > the token
  file (generated + printed once on first serve/install).
- **integrated**: a trusted backend (Portal / configured auth service)
  propagates `X-Sentinel-Principal` *only* with a valid
  `X-Sentinel-Signature` — hex HMAC-SHA256 over
  ``principal \\n role \\n workspace \\n timestamp \\n nonce`` keyed by the
  shared secret (`API_SENTINEL_SHARED_SECRET` / `api.shared_secret`
  config, falling back to the workspace token). The timestamp must be
  within ±300 s and the nonce is single-use — self-declared identity
  headers without a valid signature are rejected, never trusted.

`ApiAuth(token=None)` means *in-process mode*: the TestClient/test seam
where HTTP auth is not exercised and the documented role-placeholder
applies. `serve` always resolves a real token — in-process mode is only
reachable by injecting `ApiContext(auth=None)` directly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from api_sentinel.domain import UnauthorizedError

if TYPE_CHECKING:
    from api_sentinel.workspace import Workspace

TOKEN_FILENAME = "token"  # noqa: S105 — a filename, not a credential
TOKEN_ENV_VAR = "API_SENTINEL_TOKEN"  # noqa: S105 — env var *name*
SHARED_SECRET_ENV_VAR = "API_SENTINEL_SHARED_SECRET"  # noqa: S105 — env var *name*
DEFAULT_MAX_SKEW_SECONDS = 300
_SIG_MAX_AGE_SECONDS = DEFAULT_MAX_SKEW_SECONDS

HDR_PRINCIPAL = "x-sentinel-principal"
HDR_ROLE = "x-sentinel-role"
HDR_WORKSPACE = "x-sentinel-workspace"
HDR_TIMESTAMP = "x-sentinel-timestamp"
HDR_NONCE = "x-sentinel-nonce"
HDR_SIGNATURE = "x-sentinel-signature"


def _refusal(
    reason: str, *, details: dict[str, Any] | None = None
) -> UnauthorizedError:
    """An authentication failure with a stable reason — the HTTP layer
    maps it to 401; the reason names the failure class, never the
    presented credential material."""
    return UnauthorizedError(
        f"authentication failed: {reason}",
        details={"reason": reason, **(details or {})},
    )


def generate_token() -> str:
    """A fresh workspace bearer token — 256 bits of URL-safe entropy."""
    return secrets.token_urlsafe(32)


def token_path(ws: Workspace) -> Path:
    return ws.root / TOKEN_FILENAME


def write_token_file(path: Path, token: str) -> bool:
    """Write the token file with best-effort restrictive perms.

    Returns True when `0600` was applied. On Windows `os.chmod` only
    maps to the read-only attribute — the caveat is documented in
    docs/07; callers report the best-effort result, never claim more.
    """
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
        return True
    except OSError:
        return False


def read_token_file(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def sign_principal(
    secret: str,
    *,
    principal: str,
    role: str,
    workspace: str,
    timestamp: str | int | float,
    nonce: str,
) -> str:
    """The integrated-mode signature — exported so trusted backends and
    tests compute the same canonical string."""
    canonical = "\n".join(
        (principal, role, workspace, str(timestamp), nonce)
    )
    return hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@dataclass
class ApiAuth:
    """Resolved auth state for one served workspace.

    `token=None` + `shared_secret=None` is *in-process mode* — the
    TestClient seam; serving always configures at least one credential.
    `_seen_nonces` is bounded by the skew window: entries expire with
    their signature timestamp, so replay detection never grows past
    what a ±300 s window can hold.
    """

    token: str | None
    workspace_id: str
    shared_secret: str | None = None
    token_role: str = "operator"  # noqa: S105 — a role name, not a secret
    token_expires_at: datetime | None = None
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS
    _seen_nonces: dict[str, float] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """False only in in-process mode (no credential configured)."""
        return bool(self.token or self.shared_secret)

    # -- local mode -------------------------------------------------

    def verify_bearer(self, presented: str, now: datetime) -> None:
        """`Authorization: Bearer <token>` — constant-time compare."""
        if self.token is None:
            raise _refusal("bearer_not_configured")
        if self.token_expires_at is not None and now >= self.token_expires_at:
            raise _refusal(
                "token_expired",
                details={"expired_at": self.token_expires_at.isoformat()},
            )
        if not hmac.compare_digest(presented, self.token):
            raise _refusal("bad_token")

    # -- integrated mode --------------------------------------------

    def verify_signature(
        self,
        *,
        principal: str,
        role: str,
        workspace: str,
        timestamp: str,
        nonce: str,
        signature: str,
        now: datetime,
        known_roles: frozenset[str],
    ) -> str:
        """Validate the signed principal contract; return the role claim.

        Every failure is a 401 typed refusal — a self-declared role
        header with no valid signature is never trusted.
        """
        secret = self.shared_secret or self.token
        if secret is None:
            raise _refusal("integrated_auth_not_configured")
        for value, name in (
            (principal, "principal"),
            (role, "role"),
            (workspace, "workspace"),
            (timestamp, "timestamp"),
            (nonce, "nonce"),
            (signature, "signature"),
        ):
            if not value:
                raise _refusal(f"missing_{name}")
        if workspace != self.workspace_id:
            raise _refusal("workspace_mismatch")
        try:
            ts = float(timestamp)
        except ValueError:
            raise _refusal("bad_timestamp") from None
        now_ts = now.timestamp()
        if abs(now_ts - ts) > self.max_skew_seconds:
            raise _refusal(
                "stale_timestamp",
                details={"max_skew_seconds": self.max_skew_seconds},
            )
        # The signature covers the literal header value — a signer must
        # sign exactly what it sends.
        expected = sign_principal(
            secret,
            principal=principal,
            role=role,
            workspace=workspace,
            timestamp=timestamp,
            nonce=nonce,
        )
        if not hmac.compare_digest(signature, expected):
            raise _refusal("bad_signature")
        self._check_nonce(nonce, ts, now_ts)
        if role not in known_roles:
            raise _refusal("unknown_role", details={"role": role})
        return role

    def _check_nonce(self, nonce: str, ts: float, now_ts: float) -> None:
        """Single-use nonces — prune by signature expiry, then refuse
        replays. The map is bounded by the skew window."""
        expired = [
            n for n, exp in self._seen_nonces.items() if exp < now_ts
        ]
        for n in expired:
            del self._seen_nonces[n]
        if nonce in self._seen_nonces:
            raise _refusal("replay")
        self._seen_nonces[nonce] = ts + self.max_skew_seconds


@dataclass(frozen=True)
class ResolvedToken:
    """What token resolution produced — the CLI reports `created`/`rotated`
    once, never the token beyond the one intentional print."""

    token: str
    path: Path | None
    created: bool = False
    rotated: bool = False
    perms_applied: bool = False


def ensure_local_token(
    ws: Workspace,
    *,
    env: Mapping[str, str] | None = None,
    stdin_token: str | None = None,
    ttl_seconds: float | None = None,
    now: datetime | None = None,
) -> ResolvedToken:
    """Resolve the local-mode token: env > stdin > token file.

    A missing file is generated once and written `0600`-best-effort;
    an expired file (only meaningful when `ttl_seconds` is configured)
    is rotated and reported. Env/stdin tokens are file-less — TTL is
    unknowable for them and never applies.
    """
    environ = os.environ if env is None else env
    env_token = environ.get(TOKEN_ENV_VAR, "").strip()
    if env_token:
        return ResolvedToken(token=env_token, path=None)
    if stdin_token and stdin_token.strip():
        return ResolvedToken(token=stdin_token.strip(), path=None)

    path = token_path(ws)
    existing = read_token_file(path)
    now = now or datetime.now(UTC)
    expired = False
    if existing is not None and ttl_seconds is not None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        expired = (now.timestamp() - mtime) > ttl_seconds
    if existing is not None and not expired:
        return ResolvedToken(token=existing, path=path)
    token = generate_token()
    applied = write_token_file(path, token)
    return ResolvedToken(
        token=token,
        path=path,
        created=existing is None,
        rotated=existing is not None,
        perms_applied=applied,
    )


def resolve_auth(
    ws: Workspace,
    config: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    stdin_token: str | None = None,
    now: datetime | None = None,
) -> tuple[ApiAuth, ResolvedToken]:
    """Build the served-workspace `ApiAuth` + the token resolution record.

    Config keys (all optional): ``api.shared_secret``,
    ``api.token_ttl_seconds``, ``api.token_role`` (default: the
    workspace's ``api.default_role`` then ``operator``).
    """
    environ = os.environ if env is None else env
    api_cfg = config.get("api") if isinstance(config, dict) else None
    api_cfg = api_cfg if isinstance(api_cfg, dict) else {}
    ttl_raw = api_cfg.get("token_ttl_seconds")
    ttl = float(ttl_raw) if ttl_raw is not None else None

    resolved = ensure_local_token(
        ws, env=environ, stdin_token=stdin_token, ttl_seconds=ttl, now=now
    )
    expires_at: datetime | None = None
    if ttl is not None and resolved.path is not None:
        try:
            mtime = resolved.path.stat().st_mtime
            expires_at = datetime.fromtimestamp(
                mtime + ttl, UTC
            ).replace(microsecond=0)
        except OSError:
            expires_at = None

    shared = (
        environ.get(SHARED_SECRET_ENV_VAR, "").strip()
        or str(api_cfg.get("shared_secret") or "").strip()
        or None
    )
    default_role = str(api_cfg.get("default_role", "operator"))
    auth = ApiAuth(
        token=resolved.token,
        workspace_id=str(ws.root.resolve()),
        shared_secret=shared,
        token_role=str(api_cfg.get("token_role") or default_role),
        token_expires_at=expires_at,
        max_skew_seconds=int(
            api_cfg.get("max_skew_seconds", DEFAULT_MAX_SKEW_SECONDS)
        ),
    )
    return auth, resolved
