"""Doctor ACL — delegates through the public boundary or a handoff file.

Two modes, both optional and honest:

- ``in_process``: `forge_doctor_api` importable in this interpreter — the
  Sentinel builds a Doctor `ForgeRequest` through the boundary's public
  `build_request`/`DoctorBoundary` seam, bounded by `timeout_seconds`.
  The Sentinel *never* reimplements the analysis.
- ``handoff_file``: emit the versioned `api-sentinel/InvestigationHandoff/v1`
  envelope plus a ForgeRequest-shaped request under `handoffs/` for a
  separately-run Doctor to consume, then ingest the returned bundle.

Either way every attempt writes a redacted receipt; an absent integration
produces a named `UNAVAILABLE`, never a fabricated bundle.
"""

from __future__ import annotations

import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from pathlib import Path
from typing import Any

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain import ContractError, ValidationError
from api_sentinel.infrastructure.security.paths import (
    read_text_bounded,
    safe_slot,
)
from api_sentinel.integrations.doctor.translate import (
    DOCTOR_FORGE,
    DoctorIngest,
    handoff_to_doctor_request,
    ingest_doctor_bundle_json,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)
from api_sentinel.integrations.receipts import (
    DelegationReceipt,
    split_outcome,
    write_receipt,
)

DOCTOR_MODULE = "forge_doctor_api"


class DoctorMode(StrEnum):
    IN_PROCESS = "in_process"
    HANDOFF_FILE = "handoff_file"


def doctor_importable() -> bool:
    """True iff `forge_doctor_api` resolves — never import-for-side-effect."""
    return importlib.util.find_spec(DOCTOR_MODULE) is not None


@dataclass(frozen=True)
class DoctorResult:
    """What a delegation attempt produced — status, refs, ingest."""

    handoff_id: str
    mode: DoctorMode
    status: str  # delegated | completed | timed_out | refused | unavailable
    request_ref: str | None = None
    response_ref: str | None = None
    ingest: DoctorIngest | None = None
    detail: str = ""
    duration_ms: float | None = None
    correlation_id: str | None = None


class DoctorACL:
    """Anti-corruption adapter for Forge Doctor API.

    `timeout_seconds` bounds the *in-process* delegation wall-clock budget;
    a breach produces a named `timed_out` result — never a hang.
    """

    def __init__(
        self,
        mode: DoctorMode | str | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if mode is None:
            mode = (
                DoctorMode.IN_PROCESS if doctor_importable()
                else DoctorMode.HANDOFF_FILE
            )
        self._mode = DoctorMode(mode)
        self._timeout = timeout_seconds

    @property
    def mode(self) -> DoctorMode:
        return self._mode

    # -- delegation ----------------------------------------------------

    def delegate(
        self,
        handoff: InvestigationHandoffV1,
        *,
        target: str,
        capabilities: list[str] | None = None,
        context_root: Path | None = None,
        clock: str | None = None,
        receipts_dir: Path | None = None,
        handoffs_dir: Path | None = None,
        now_iso: str | None = None,
    ) -> DoctorResult:
        """Attempt a delegation; always writes a receipt, never fabricates."""
        started = time.monotonic()
        request_dict = handoff_to_doctor_request(
            handoff, target=target, capabilities=capabilities, clock=clock
        )
        result = self._dispatch(
            handoff,
            request_dict,
            context_root=context_root,
            handoffs_dir=handoffs_dir,
        )
        duration_ms = (time.monotonic() - started) * 1000.0
        result = DoctorResult(
            handoff_id=result.handoff_id,
            mode=result.mode,
            status=result.status,
            request_ref=result.request_ref,
            response_ref=result.response_ref,
            ingest=result.ingest,
            detail=result.detail,
            duration_ms=duration_ms,
            correlation_id=handoff.correlation_id or handoff.handoff_id,
        )
        if receipts_dir is not None:
            self._write_receipt(receipts_dir, result, now_iso)
        return result

    def _dispatch(
        self,
        handoff: InvestigationHandoffV1,
        request_dict: dict[str, Any],
        *,
        context_root: Path | None,
        handoffs_dir: Path | None,
    ) -> DoctorResult:
        if self._mode is DoctorMode.IN_PROCESS:
            return self._delegate_in_process(
                handoff, request_dict, context_root=context_root
            )
        return self._delegate_file(
            handoff, request_dict, handoffs_dir=handoffs_dir
        )

    def _delegate_in_process(
        self,
        handoff: InvestigationHandoffV1,
        request_dict: dict[str, Any],
        *,
        context_root: Path | None,
    ) -> DoctorResult:
        if not doctor_importable():
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="unavailable",
                detail=f"{DOCTOR_MODULE} not importable in this interpreter",
            )
        if context_root is None:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="refused",
                detail="in_process mode requires context_root (a "
                       "Doctor ProjectContext root); none provided",
            )
        try:
            return self._run_doctor_typed(
                handoff, request_dict, context_root=context_root
            )
        except IntegrationRefused as exc:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="refused",
                detail=str(exc.reason),
            )
        except FuturesTimeout:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="timed_out",
                detail=f"in-process delegation exceeded {self._timeout}s",
            )
        except Exception as exc:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="failed",
                detail=f"{type(exc).__name__}: {exc}",
            )

    def _run_doctor_typed(
        self,
        handoff: InvestigationHandoffV1,
        request_dict: dict[str, Any],
        *,
        context_root: Path,
    ) -> DoctorResult:
        """Run the real Doctor boundary under the injected timeout.

        Optional-dependency imports stay inside this method (guarded by
        `doctor_importable()`); mypy stubs are absent by design — the
        package is consumed through its public seam, never vendored.
        """
        from forge_doctor_api.handoff.boundary import DoctorBoundary

        # C10-F009: `ProjectContext` lives at `core.context` since Doctor
        # 0.3.x; `handoff.context` is the legacy location kept as a fallback
        # for older installs. Prefer the canonical path; never guess. The
        # legacy path resolves dynamically — the module exists but carries
        # no `ProjectContext` at 0.3.x, so a static import would type-error.
        try:
            from forge_doctor_api.core.context import ProjectContext

            make_context = ProjectContext.from_root
        except ImportError:  # legacy <0.3 layout
            _legacy_ctx = importlib.import_module(
                "forge_doctor_api.handoff.context"
            )
            make_context = _legacy_ctx.ProjectContext.from_root
        from forge_doctor_api.handoff.protocol import ForgeRequest

        request = ForgeRequest.parse(request_dict)
        boundary = DoctorBoundary(context=make_context(context_root))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(boundary.handle, request)
            bundle = future.result(timeout=self._timeout)
        raw: dict[str, Any] = bundle.to_dict()
        ingest = ingest_doctor_bundle_json(json.dumps(raw))
        return DoctorResult(
            handoff_id=handoff.handoff_id,
            mode=self._mode,
            status="completed",
            ingest=ingest,
            detail="delegated in-process through DoctorBoundary",
        )

    def _delegate_file(
        self,
        handoff: InvestigationHandoffV1,
        request_dict: dict[str, Any],
        *,
        handoffs_dir: Path | None,
    ) -> DoctorResult:
        if handoffs_dir is None:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="refused",
                detail="handoff_file mode requires a workspace handoffs dir",
            )
        handoffs_dir.mkdir(parents=True, exist_ok=True)
        # The slot id comes from the envelope — it must resolve under the
        # handoffs root, never outside it (../ or symlink escapes refused).
        slot = safe_slot(handoffs_dir, handoff.handoff_id, name="handoff_id")
        slot.mkdir(exist_ok=True)
        envelope_path = slot / "investigation_handoff.json"
        envelope_path.write_text(
            handoff.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
        )
        request_path = slot / "doctor_request.json"
        request_path.write_text(
            json.dumps(request_dict, indent=2, sort_keys=True), encoding="utf-8"
        )
        return DoctorResult(
            handoff_id=handoff.handoff_id,
            mode=self._mode,
            status="delegated",
            request_ref=str(request_path),
            detail=(
                "handoff emitted; a separately-run Doctor consumes "
                f"{request_path.name} and returns a bundle via "
                "'handoff doctor --ingest'"
            ),
        )

    # -- ingest ---------------------------------------------------------

    def ingest(
        self,
        bundle_path: Path,
        *,
        expected_handoff_id: str | None = None,
    ) -> DoctorIngest:
        """Read a Doctor-emitted bundle back as verbatim evidence.

        The read is bounded (`MAX_INBOUND_BYTES`); when
        `expected_handoff_id` is given a bundle that names a different
        handoff is a `ContractError` — response correlation is checked,
        never assumed.
        """
        if not bundle_path.is_file():
            raise ValidationError(f"doctor bundle not found: {bundle_path}")
        try:
            text = read_text_bounded(bundle_path, name="doctor bundle")
        except UnicodeDecodeError as exc:
            raise ContractError(f"doctor bundle unreadable: {exc}") from exc
        return ingest_doctor_bundle_json(
            text, expected_handoff_id=expected_handoff_id
        )

    # -- receipts -------------------------------------------------------

    def _write_receipt(
        self,
        receipts_dir: Path,
        result: DoctorResult,
        now_iso: str | None,
    ) -> None:
        from datetime import datetime

        at = (
            datetime.fromisoformat(now_iso)
            if now_iso
            else datetime.now(UTC)
        )
        evidence_refs: list[str] = []
        if result.request_ref:
            evidence_refs.append(result.request_ref)
        if result.response_ref:
            evidence_refs.append(result.response_ref)
        transport_state, execution_state = split_outcome(result.status)
        receipt = DelegationReceipt(
            receipt_id=f"rcpt_{result.handoff_id}",
            to_forge=DOCTOR_FORGE,
            kind="api-investigation",
            outcome=result.status,
            at=at,
            handoff_id=result.handoff_id,
            request_id=result.handoff_id,
            correlation_id=result.correlation_id or result.handoff_id,
            transport_state=transport_state,
            execution_state=execution_state,
            duration_ms=result.duration_ms,
            evidence_refs=evidence_refs,
            detail=result.detail,
        )
        write_receipt(receipts_dir, receipt)


def doctor_unavailable() -> IntegrationUnavailable:
    """The named refusal when no Doctor transport exists at all."""
    return IntegrationUnavailable(
        DOCTOR_FORGE,
        f"{DOCTOR_MODULE} is not importable and no handoff transport "
        "configured — absent integrations never simulate success",
    )
