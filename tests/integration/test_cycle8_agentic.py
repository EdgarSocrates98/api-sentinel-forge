"""Cycle-8 integration tests — LAB-004 regressive deploy, end to end.

The oracle: manifest import dedups deterministically; detection over the
regressive window opens an incident whose anchor precedes the deploy's
*consequence*; correlation ranks the deploy with the `proximity≠cause`
disclaimer; the governed investigation emits a bounded pack + a named
session state; and only independent verification evidence can promote a
CORRELATED hypothesis to VERIFIED.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from api_sentinel.application.use_cases import (
    GetIncidentTimeline,
    RunDetection,
)
from api_sentinel.domain import (
    ApiService,
    ExternalRef,
    FixedClock,
    SequentialIdFactory,
    ServiceEnvironment,
    TelemetrySource,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.integrations.bridge.adapter import FileAgentSession
from api_sentinel.integrations.service import ListChanges
from api_sentinel.investigation.changes import (
    CorrelateIncidentChanges,
    ImportDeployManifest,
)
from api_sentinel.investigation.hypotheses import (
    AddHypothesis,
    DemoteHypothesis,
    PromoteHypothesis,
)
from api_sentinel.investigation.service import RunAgenticInvestigation
from api_sentinel.investigation.verification import RunVerification

NOW = datetime(2026, 1, 1, 0, 12, tzinfo=UTC)
LAB = (
    Path(__file__).resolve().parents[2]
    / "lab" / "fixtures" / "lab-004-regressive-deploy"
)
SERVICE_ID = "svc_lab_checkout"
_IDS = SequentialIdFactory()


def _redactor(text: str) -> str:
    return redact(text).text


def _seed(db: Path) -> None:
    with SqliteUnitOfWork(db) as uow:
        env = uow.environments.get_by_name("lab") or uow.environments.put(
            ServiceEnvironment(
                environment_id="env_lab", name="lab", created_at=NOW
            )
        )
        uow.services.put(
            ApiService(
                service_id=SERVICE_ID,
                service_name="checkout-api",
                environment_id=env.environment_id,
                external_refs=[
                    ExternalRef(
                        kind="code_atlas_identity",
                        ref="atlas:svc:checkout-api",
                        provenance="verified",
                    )
                ],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        source = TelemetrySource(
            source_id="src_lab_jsonl",
            kind="jsonl-file",
            service_id=SERVICE_ID,
            created_at=NOW,
        )
        uow.sources.put(source)
        uow.commit()
    result = IngestionPipeline(
        SqliteUnitOfWork(db), FixedClock(NOW), _IDS, _redactor
    ).ingest(
        source=source,
        kind="jsonl",
        content=(LAB / "observations.jsonl").read_bytes(),
    )
    assert result.accepted == 18


def _import_manifest(db: Path):
    return ImportDeployManifest(
        SqliteUnitOfWork(db), NOW
    ).execute(file=LAB / "deploy-manifest.json")


def _detect(db: Path):
    return RunDetection(
        SqliteUnitOfWork(db), FixedClock(NOW), _IDS
    ).execute(
        service_id=SERVICE_ID,
        window_start=datetime(2026, 1, 1, 0, 4, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        now=NOW,
    )


class TestManifestImport:
    def test_import_dedups_deterministically(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed(db)
        first = _import_manifest(db)
        assert first.imported == 2  # deploy + rollback
        assert first.deduplicated == 0
        second = _import_manifest(db)
        assert second.imported == 0
        assert second.deduplicated == 2  # replay is a no-op, named honestly
        changes = ListChanges(SqliteUnitOfWork(db)).execute(
            service_id=SERVICE_ID
        )
        assert len(changes) == 2
        kinds = {c.kind.value for c in changes}
        assert kinds == {"deploy", "rollback"}


class TestLab004EndToEnd:
    def test_regressive_deploy_correlates_honestly(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "s.db"
        _seed(db)
        _import_manifest(db)
        report = _detect(db)
        (incident,) = report.incidents_created
        assert incident.first_observed_at.isoformat() == (
            "2026-01-01T00:04:00+00:00"
        )

        correlation = CorrelateIncidentChanges(
            SqliteUnitOfWork(db)
        ).execute(incident_id=incident.incident_id)
        # the regressive deploy leads the candidates — ranked, not causal
        top = correlation.candidates[0]
        assert top.change.kind.value == "deploy"
        assert top.change.ref == "sha_a11ce99"
        assert top.delta_seconds == 30.0
        # the rollback lands after the anchor — excluded, honestly named
        assert any(
            c.change.kind.value == "rollback" for c in correlation.candidates
        ) is False
        assert any("after_anchor" in u for u in correlation.unknowns)
        assert "proximity" in correlation.proximity_ne_cause
        assert "not root cause" in correlation.proximity_ne_cause

    def test_investigation_pack_bounded_and_session_governed(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "s.db"
        ws = tmp_path / "ws"
        (ws / "agent_sessions").mkdir(parents=True)
        _seed(db)
        _import_manifest(db)
        incident = _detect(db).incidents_created[0]

        outcome = RunAgenticInvestigation(
            SqliteUnitOfWork(db),
            NOW,
            _IDS,
            session_port=FileAgentSession(ws / "agent_sessions"),
            workspace_root=ws,
        ).execute(
            incident_id=incident.incident_id,
            principal="edgar@local",
            workspace=str(ws),
            provider_choice="claude",
            cost_limit=2.0,
            approvals=["read:evidence"],
        )
        pack = outcome.pack
        assert pack.size_bytes <= pack.budget_bytes
        assert pack.instructions.scope["incident_id"] == (
            incident.incident_id
        )
        # the deploy lands as change-event evidence inside the pack
        change_items = [
            i for i in pack.contents if i.kind.value == "change_event"
        ]
        assert change_items, "deploy evidence expected in the pack"
        assert all(i.role == "data" for i in pack.contents)
        # disclaimer travels with the pack
        assert any("proximity" in u for u in pack.unknowns)
        # governed session attempt — delegated through the file seam
        assert outcome.session.status == "delegated"
        g = outcome.session.governance
        assert g.principal == "edgar@local"
        assert g.provider == "claude"
        assert g.cost_limit_usd == 2.0
        assert g.approvals == ["read:evidence"]
        # envelope on disk, redacted
        env = json.loads(Path(outcome.session.session_ref or "").read_text())
        assert env["instructions"]["scope"]["incident_id"]
        # journaled + audited
        with SqliteUnitOfWork(db) as uow:
            events = uow.incident_events.list(incident.incident_id)
            kinds = {e.kind.value for e in events}
            assert "session_requested" in kinds
            handoffs = uow.handoff_records.list(to_forge="forge-agent-bridge")
            assert handoffs and handoffs[0].status.value == "delegated"

    def test_unavailable_bridge_is_named_not_failed(
        self, tmp_path: Path
    ) -> None:
        from api_sentinel.integrations.bridge.adapter import (
            UnavailableAgentSession,
        )

        db = tmp_path / "s.db"
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed(db)
        incident = _detect(db).incidents_created[0]
        outcome = RunAgenticInvestigation(
            SqliteUnitOfWork(db),
            NOW,
            _IDS,
            session_port=UnavailableAgentSession(),
            workspace_root=ws,
        ).execute(
            incident_id=incident.incident_id,
            principal="edgar@local",
            workspace=str(ws),
            cost_limit=1.0,
        )
        # the deterministic investigation stands; the session is a
        # named unavailable — never a fabricated delegation
        assert outcome.session.status == "unavailable"
        assert outcome.session.governance.principal == "edgar@local"
        assert outcome.session.governance.cost_limit_usd == 1.0
        assert outcome.investigation.status.value == "COMPLETED"

    def test_verified_lifecycle_requires_independent_evidence(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "s.db"
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed(db)
        _import_manifest(db)
        incident = _detect(db).incidents_created[0]
        deploy_ref = "sha_a11ce99"
        deploy_change = next(
            c for c in ListChanges(SqliteUnitOfWork(db)).execute(
                service_id=SERVICE_ID
            )
            if c.ref == deploy_ref
        )

        # 1. hypothesis lands at CORRELATED — evidence named, never cause
        hypothesis, _ = AddHypothesis(
            SqliteUnitOfWork(db), NOW, _IDS
        ).execute(
            incident_id=incident.incident_id,
            text=(
                f"deploy {deploy_ref} (2.7.0) introduced the 503 "
                "regression — correlation, not proven cause"
            ),
            confidence="CORRELATED",
            evidence_for=[deploy_change.change_id],
            unknowns=["independent post-fix verification pending"],
        )
        assert hypothesis.confidence.value == "CORRELATED"

        # 2. promote without verification → named refusal
        import pytest

        from api_sentinel.domain.errors import ValidationError

        with pytest.raises(ValidationError):
            PromoteHypothesis(SqliteUnitOfWork(db), NOW).execute(
                incident_id=incident.incident_id,
                hypothesis_id=hypothesis.hypothesis_id,
                verification_id="ver_missing",
            )

        # 3. run the plan on the post-fix window (rolled back → clean)
        result = RunVerification(
            SqliteUnitOfWork(db),
            NOW,
            workspace_root=ws,
        ).execute(
            incident_id=incident.incident_id,
            hypothesis_id=hypothesis.hypothesis_id,
            window_start=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
            window_end=datetime(2026, 1, 1, 0, 12, tzinfo=UTC),
        )
        assert result.verdict == "VERIFIED"
        assert result.receipts, "verification must carry receipts"
        assert any(
            r.kind == "re_detect" and r.status == "supporting"
            for r in result.receipts
        )

        # 4. now promotion is allowed — journaled
        promoted, _ = PromoteHypothesis(
            SqliteUnitOfWork(db), NOW
        ).execute(
            incident_id=incident.incident_id,
            hypothesis_id=hypothesis.hypothesis_id,
            verification_id=result.verification_id,
        )
        assert promoted.confidence.value == "VERIFIED"
        assert promoted.verification_id == result.verification_id
        with SqliteUnitOfWork(db) as uow:
            events = uow.incident_events.list(incident.incident_id)
            kinds = [e.kind.value for e in events]
            assert "verification_recorded" in kinds
            assert "hypothesis_updated" in kinds

    def test_refuted_path_demotes_with_reason(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed(db)
        incident = _detect(db).incidents_created[0]
        hypothesis, _ = AddHypothesis(
            SqliteUnitOfWork(db), NOW, _IDS
        ).execute(
            incident_id=incident.incident_id,
            text="config drift caused it",
            confidence="HYPOTHESIZED",
            evidence_for=[],
        )
        demoted, _ = DemoteHypothesis(
            SqliteUnitOfWork(db), NOW
        ).execute(
            incident_id=incident.incident_id,
            hypothesis_id=hypothesis.hypothesis_id,
            reason="post-fix window shows the same errors — contradicted",
            evidence_against=["redetect:still-firing"],
        )
        assert demoted.confidence.value == "REFUTED"

    def test_timeline_surfaces_cycle8_records(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        ws = tmp_path / "ws"
        (ws / "agent_sessions").mkdir(parents=True)
        _seed(db)
        _import_manifest(db)
        incident = _detect(db).incidents_created[0]
        RunAgenticInvestigation(
            SqliteUnitOfWork(db),
            NOW,
            _IDS,
            session_port=FileAgentSession(ws / "agent_sessions"),
            workspace_root=ws,
        ).execute(
            incident_id=incident.incident_id,
            principal="edgar@local",
            workspace=str(ws),
        )
        hypothesis, _ = AddHypothesis(
            SqliteUnitOfWork(db), NOW, _IDS
        ).execute(
            incident_id=incident.incident_id,
            text="deploy hypothesis",
            confidence="CORRELATED",
        )
        RunVerification(
            SqliteUnitOfWork(db), NOW, workspace_root=ws
        ).execute(
            incident_id=incident.incident_id,
            hypothesis_id=hypothesis.hypothesis_id,
            window_start=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
            window_end=datetime(2026, 1, 1, 0, 12, tzinfo=UTC),
        )
        timeline = GetIncidentTimeline(SqliteUnitOfWork(db)).execute(
            incident_id=incident.incident_id
        )
        assert timeline.investigations, "investigation rows expected"
        assert timeline.hypotheses
        assert timeline.verifications
        assert timeline.verifications[0].verdict == "VERIFIED"
        # the timeline surfaces change events since the first signal —
        # the rollback lands inside the window; the pre-anchor deploy is
        # surfaced through `changes correlate`, a different bounded view
        change_refs = {c.kind.value for c in timeline.change_events}
        assert "rollback" in change_refs
