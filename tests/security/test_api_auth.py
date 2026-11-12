"""C10-F007 negative battery — real authentication on /v1.

Two credential channels are exercised end-to-end through `TestClient`:

- **local bearer**: `Authorization: Bearer <workspace token>` — the
  `token_role` on the resolved `ApiAuth` maps to scopes; a
  self-declared `X-Sentinel-Role` header is ignored entirely.
- **integrated signed principal**: `X-Sentinel-Principal` +
  `X-Sentinel-Signature` (HMAC over
  principal\nrole\nworkspace\ntimestamp\nnonce, ±300 s skew, single-use
  nonce) — self-declared identity headers without a valid signature are
  rejected, never trusted.

The in-process seam (`ApiContext(auth=None)`) is the documented test
mode and is asserted to reject *unknown* roles — but every test here
configures a real credential, the mode `serve` always runs in.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_sentinel.api import create_app
from api_sentinel.api.deps import ApiContext
from api_sentinel.detection import Severity
from api_sentinel.domain import (
    ApiService,
    FixedClock,
    ServiceEnvironment,
    UlidIdFactory,
)
from api_sentinel.incidents import Incident, IncidentStatus
from api_sentinel.infrastructure.security.auth import (
    ApiAuth,
    ensure_local_token,
    sign_principal,
)
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
NOW_TS = str(NOW.timestamp())
TOKEN = "test-token-" + "a" * 32
SECRET = "test-shared-secret-" + "b" * 16
INCIDENT = "inc_authlab"


def _seed(ws_root: Path) -> Workspace:
    ws = ensure_workspace(Workspace(root=ws_root, scope="project"))
    with SqliteUnitOfWork(ws.db_path) as uow:
        env = uow.environments.put(
            ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW)
        )
        uow.services.put(
            ApiService(
                service_id="svc_authlab",
                service_name="payments-api",
                environment_id=env.environment_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        uow.incidents.put(
            Incident(
                incident_id=INCIDENT,
                title="auth lab incident",
                severity=Severity.SEV3,
                status=IncidentStatus.DETECTED,
                service_id="svc_authlab",
                group_key="svc_authlab|service|availability",
                first_observed_at=NOW,
                detected_at=NOW,
            )
        )
        uow.commit()
    return ws


def _ctx(
    ws_root: Path,
    *,
    role: str = "operator",
    shared_secret: str | None = SECRET,
    token_expires_at: datetime | None = None,
    cors_origins: list[str] | None = None,
) -> ApiContext:
    ws = Workspace(root=ws_root, scope="project")
    return ApiContext(
        workspace=ws,
        config={},
        clock=FixedClock(NOW),
        ids=UlidIdFactory(),
        redactor=lambda t: t,
        structural=None,
        structural_reason="no atlas export configured — structural context unavailable",
        atlas_manifest=None,
        default_role="operator",
        auth=ApiAuth(
            token=TOKEN,
            workspace_id=str(ws.root.resolve()),
            shared_secret=shared_secret,
            token_role=role,
            token_expires_at=token_expires_at,
        ),
        cors_origins=cors_origins or [],
    )


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    _seed(root)
    return root


@pytest.fixture
def client(ws_root: Path) -> TestClient:
    return TestClient(create_app(context=_ctx(ws_root)))


def _signed_headers(
    ws: Workspace,
    *,
    secret: str = SECRET,
    principal: str = "portal:pm-42",
    role: str = "admin",
    timestamp: str = NOW_TS,
    nonce: str = "nonce-1",
    workspace: str | None = None,
) -> dict[str, str]:
    workspace = workspace if workspace is not None else str(ws.root.resolve())
    return {
        "X-Sentinel-Principal": principal,
        "X-Sentinel-Role": role,
        "X-Sentinel-Workspace": workspace,
        "X-Sentinel-Timestamp": timestamp,
        "X-Sentinel-Nonce": nonce,
        "X-Sentinel-Signature": sign_principal(
            secret,
            principal=principal,
            role=role,
            workspace=workspace,
            timestamp=timestamp,
            nonce=nonce,
        ),
    }


class TestLocalBearer:
    def test_no_credentials_is_401(self, client: TestClient) -> None:
        r = client.get("/v1/services")
        assert r.status_code == 401
        problem = r.json()
        assert problem["code"] == "sentinel.unauthorized"
        assert problem["status"] == 401
        assert r.headers["www-authenticate"].startswith("Bearer")

    def test_bad_token_is_401(self, client: TestClient) -> None:
        r = client.get("/v1/services", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        assert r.json()["code"] == "sentinel.unauthorized"

    def test_wrong_scheme_is_401(self, client: TestClient) -> None:
        r = client.get("/v1/services", headers={"Authorization": f"Basic {TOKEN}"})
        assert r.status_code == 401

    def test_expired_token_is_401(self, ws_root: Path) -> None:
        ctx = _ctx(ws_root, token_expires_at=NOW - timedelta(seconds=1))
        with TestClient(create_app(context=ctx)) as c:
            r = c.get(
                "/v1/services",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert r.status_code == 401
            assert r.json()["details"]["reason"] == "token_expired"

    def test_valid_token_reads_and_mutates(self, client: TestClient) -> None:
        assert (
            client.get(
                "/v1/services",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ).status_code
            == 200
        )
        r = client.post(
            f"/v1/incidents/{INCIDENT}/investigations",
            json={"note": "token auth"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert r.status_code in (200, 201), r.text

    def test_self_declared_role_without_credentials_is_401(self, client: TestClient) -> None:
        """The C10-F007 exploit: `X-Sentinel-Role: admin` alone grants
        nothing — it is a 401, never an admin session."""
        r = client.get("/v1/services", headers={"X-Sentinel-Role": "admin"})
        assert r.status_code == 401
        r = client.post(
            f"/v1/incidents/{INCIDENT}/investigations",
            json={},
            headers={"X-Sentinel-Role": "admin"},
        )
        assert r.status_code == 401

    def test_role_header_never_escalates_a_token(self, ws_root: Path) -> None:
        """A viewer-scoped token stays a viewer even when it also
        declares `X-Sentinel-Role: admin` — the header is ignored."""
        ctx = _ctx(ws_root, role="viewer")
        with TestClient(create_app(context=ctx)) as c:
            r = c.post(
                f"/v1/incidents/{INCIDENT}/investigations",
                json={},
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "X-Sentinel-Role": "admin",
                },
            )
            assert r.status_code == 403
            assert r.json()["details"]["role"] == "viewer"

    def test_missing_resource_with_valid_token_is_404(self, client: TestClient) -> None:
        """Authn/authz pass, then the lookup fails — 404, not 401/403."""
        r = client.get(
            "/v1/services/svc_ghost",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert r.status_code == 404

    def test_forwarded_headers_establish_nothing(self, client: TestClient) -> None:
        """Proxy-injected identity headers are never consulted."""
        r = client.get(
            "/v1/services",
            headers={
                "X-Forwarded-User": "admin",
                "X-Real-Ip": "127.0.0.1",
                "Forwarded": "for=127.0.0.1",
            },
        )
        assert r.status_code == 401

    def test_health_stays_open(self, client: TestClient) -> None:
        assert client.get("/v1/health").status_code == 200


class TestIntegratedSignature:
    def test_signed_principal_authenticates(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        with TestClient(create_app(context=_ctx(ws_root))) as c:
            r = c.get(
                "/v1/services",
                headers=_signed_headers(ws, role="viewer", nonce="nonce-0"),
            )
            assert r.status_code == 200

    def _client_with(self, ws_root: Path) -> TestClient:
        return TestClient(create_app(context=_ctx(ws_root)))

    def test_signed_viewer_reads_not_mutates(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        with self._client_with(ws_root) as c:
            r = c.get(
                "/v1/services",
                headers=_signed_headers(ws, role="viewer"),
            )
            assert r.status_code == 200
            denied = c.post(
                f"/v1/incidents/{INCIDENT}/investigations",
                json={},
                headers=_signed_headers(ws, role="viewer", nonce="nonce-2"),
            )
            assert denied.status_code == 403

    def test_signed_admin_mutates(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        with self._client_with(ws_root) as c:
            r = c.post(
                f"/v1/incidents/{INCIDENT}/investigations",
                json={"note": "signed"},
                headers=_signed_headers(ws, role="admin", nonce="nonce-3"),
            )
            assert r.status_code in (200, 201), r.text

    def test_forged_signature_is_401(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        headers = _signed_headers(ws, role="admin", nonce="nonce-4")
        headers["X-Sentinel-Signature"] = "0" * 64
        with self._client_with(ws_root) as c:
            r = c.get("/v1/services", headers=headers)
            assert r.status_code == 401
            assert r.json()["details"]["reason"] == "bad_signature"

    def test_stale_timestamp_is_401(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        old_ts = str(NOW.timestamp() - 1000)
        headers = _signed_headers(ws, role="admin", timestamp=old_ts, nonce="nonce-5")
        with self._client_with(ws_root) as c:
            r = c.get("/v1/services", headers=headers)
            assert r.status_code == 401
            assert r.json()["details"]["reason"] == "stale_timestamp"

    def test_nonce_replay_is_401(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        headers = _signed_headers(ws, role="admin", nonce="nonce-6")
        with self._client_with(ws_root) as c:
            assert c.get("/v1/services", headers=headers).status_code == 200
            replayed = c.get("/v1/services", headers=headers)
            assert replayed.status_code == 401
            assert replayed.json()["details"]["reason"] == "replay"

    def test_cross_workspace_principal_is_401(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        headers = _signed_headers(ws, role="admin", nonce="nonce-7", workspace="/other/workspace")
        with self._client_with(ws_root) as c:
            r = c.get("/v1/services", headers=headers)
            assert r.status_code == 401
            assert r.json()["details"]["reason"] == "workspace_mismatch"

    def test_signed_unknown_role_is_401(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        headers = _signed_headers(ws, role="superadmin", nonce="nonce-8")
        with self._client_with(ws_root) as c:
            r = c.get("/v1/services", headers=headers)
            assert r.status_code == 401
            assert r.json()["details"]["reason"] == "unknown_role"

    def test_principal_without_signature_is_401(self, client: TestClient) -> None:
        r = client.get(
            "/v1/services",
            headers={"X-Sentinel-Principal": "portal:pm-42"},
        )
        assert r.status_code == 401
        assert r.json()["details"]["reason"].startswith("missing_")


class TestDenialBeforeLookup:
    def test_denied_scope_never_reveals_existence(self, ws_root: Path) -> None:
        ws = Workspace(root=ws_root, scope="project")
        headers = _signed_headers(ws, role="viewer", nonce="nonce-9")
        with TestClient(create_app(context=_ctx(ws_root))) as c:
            real = c.post(
                f"/v1/incidents/{INCIDENT}/investigations",
                json={},
                headers=headers,
            )
            ghost = c.post(
                "/v1/incidents/inc_ghost/investigations",
                json={},
                headers=_signed_headers(ws, role="viewer", nonce="nonce-10"),
            )
            assert real.status_code == ghost.status_code == 403
            assert real.json()["code"] == ghost.json()["code"]
            for body in (real.json(), ghost.json()):
                text = str(body).lower()
                assert "ghost" not in text
                assert "not found" not in text


class TestCors:
    def test_default_denies_cross_origin(self, client: TestClient) -> None:
        r = client.options(
            "/v1/services",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}

    def test_allowlisted_origin_only(self, ws_root: Path) -> None:
        ctx = _ctx(ws_root, cors_origins=["https://portal.local"])
        with TestClient(create_app(context=ctx)) as c:
            allowed = c.options(
                "/v1/services",
                headers={
                    "Origin": "https://portal.local",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert allowed.headers.get("access-control-allow-origin") == "https://portal.local"
            denied = c.options(
                "/v1/services",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert denied.headers.get("access-control-allow-origin") != "https://evil.example"
            # credentials stay off — a wildcard can never ride along
            assert allowed.headers.get("access-control-allow-credentials") != "true"


class TestServedPathAlwaysAuths:
    def test_workspace_app_resolves_real_auth(self, tmp_path: Path) -> None:
        """create_app(workspace=…) — the production path — never runs
        credential-less: no headers → 401."""
        ws_root = tmp_path / "ws"
        _seed(ws_root)
        with TestClient(create_app(workspace=ws_root)) as c:
            assert c.get("/v1/services").status_code == 401
            token = (ws_root / "token").read_text().strip()
            assert token
            assert (
                c.get(
                    "/v1/services",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code
                == 200
            )

    def test_in_process_seam_still_denies_unknown_roles(self, ws_root: Path) -> None:
        """The documented test seam (`auth=None`) maps the role header —
        an unknown role is a 403, never an implicit grant."""
        ctx = _ctx(ws_root)
        ctx.auth = None
        with TestClient(create_app(context=ctx)) as c:
            assert (
                c.get(
                    "/v1/services",
                    headers={"X-Sentinel-Role": "viewer"},
                ).status_code
                == 200
            )
            denied = c.get(
                "/v1/services",
                headers={"X-Sentinel-Role": "intruder"},
            )
            assert denied.status_code == 403


class TestTokenResolution:
    def test_token_file_created_once(self, tmp_path: Path) -> None:
        ws = ensure_workspace(Workspace(root=tmp_path / "ws", scope="project"))
        first = ensure_local_token(ws, env={})
        second = ensure_local_token(ws, env={})
        assert first.created and first.path is not None
        assert first.path.read_text().strip() == first.token
        assert second.token == first.token and not second.created

    def test_env_wins_over_file(self, tmp_path: Path) -> None:
        ws = ensure_workspace(Workspace(root=tmp_path / "ws", scope="project"))
        env_cred = "env-token-123"
        resolved = ensure_local_token(ws, env={"API_SENTINEL_TOKEN": env_cred})
        assert resolved.token == env_cred
        assert resolved.path is None and not resolved.created
        assert not (ws.root / "token").exists()

    def test_stdin_wins_over_file_but_not_env(self, tmp_path: Path) -> None:
        ws = ensure_workspace(Workspace(root=tmp_path / "ws", scope="project"))
        stdin_cred = "stdin-tok"
        resolved = ensure_local_token(ws, env={}, stdin_token=stdin_cred)
        assert resolved.token == stdin_cred
        env_cred = "env-tok"
        resolved = ensure_local_token(
            ws,
            env={"API_SENTINEL_TOKEN": env_cred},
            stdin_token=stdin_cred,
        )
        assert resolved.token == env_cred

    def test_expired_file_rotates(self, tmp_path: Path) -> None:
        ws = ensure_workspace(Workspace(root=tmp_path / "ws", scope="project"))
        first = ensure_local_token(ws, env={})
        assert first.path is not None
        # age the file beyond the configured TTL
        old = NOW.timestamp() - 10_000
        os.utime(first.path, (old, old))
        rotated = ensure_local_token(
            ws,
            env={},
            ttl_seconds=3600.0,
            now=datetime.fromtimestamp(old + 7_200, tz=UTC),
        )
        assert rotated.rotated and rotated.token != first.token
        assert first.path.read_text().strip() == rotated.token
