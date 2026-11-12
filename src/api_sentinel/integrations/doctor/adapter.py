"""Doctor ACL — delegates through the public boundary or a handoff file.

Two modes, both optional and honest:

- ``in_process``: `forge_doctor_api` importable in this interpreter — the
  Sentinel ships the `ForgeRequest` to a dedicated child process
  (``python -m api_sentinel.integrations.doctor._worker``) that calls the
  boundary's public `DoctorBoundary.handle` seam. The child runs under a
  hard wall-clock budget: on breach it is terminated, then killed — a
  blocked Doctor can never stall the Sentinel (C101-F001). The Sentinel
  *never* reimplements the analysis.
- ``handoff_file``: emit the versioned `api-sentinel/InvestigationHandoff/v1`
  envelope plus a ForgeRequest-shaped request under `handoffs/` for a
  separately-run Doctor to consume, then ingest the returned bundle.

Either way every attempt writes a redacted receipt; an absent integration
produces a named `UNAVAILABLE`, never a fabricated bundle.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
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
from api_sentinel.infrastructure.security.redact import redact
from api_sentinel.integrations.doctor._worker import (
    EXIT_FAILED,
    EXIT_INVALID_RESPONSE,
    EXIT_OK,
    EXIT_UNAVAILABLE,
    EXIT_USAGE,
    MAX_REQUEST_BYTES,
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

# Child-process contract — the worker module is the *smallest compatible
# wrapper*: a private entrypoint that rebuilds the Doctor boundary inside
# an interpreter the parent can terminate. Credentials are stripped from
# the child environment; cwd is a validated scratch dir, never the
# caller's tree.
_WORKER_MODULE = "api_sentinel.integrations.doctor._worker"
_CHILD_ENV_KEEP = {
    "PATH",
    "SystemRoot",
    "WINDIR",
    "SystemDrive",
    "ComSpec",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
}
_TERMINATE_GRACE_SECONDS = 3.0
_KILL_GRACE_SECONDS = 3.0
_STDIO_TAIL_BYTES = 4096


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
            mode = DoctorMode.IN_PROCESS if doctor_importable() else DoctorMode.HANDOFF_FILE
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
            return self._delegate_in_process(handoff, request_dict, context_root=context_root)
        return self._delegate_file(handoff, request_dict, handoffs_dir=handoffs_dir)

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
        if not context_root.is_dir():
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="refused",
                detail=f"context_root is not a directory: {context_root}",
            )
        try:
            return self._delegate_via_process(handoff, request_dict, context_root=context_root)
        except IntegrationRefused as exc:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="refused",
                detail=str(exc.reason),
            )
        except Exception as exc:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="failed",
                detail=f"{type(exc).__name__}: {exc}",
            )

    # -- process-isolated delegation (C101-F001) -------------------------

    def _worker_argv(self, request_path: Path, out_dir: Path) -> list[str]:
        """argv for the delegation child — a list, never a shell string."""
        return [
            sys.executable,
            "-m",
            _WORKER_MODULE,
            str(request_path),
            str(out_dir),
        ]

    def _child_env(self) -> dict[str, str]:
        """Restricted environment — credentials never reach the child."""
        env = {k: v for k, v in os.environ.items() if k in _CHILD_ENV_KEEP}
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    def _terminate_child(self, proc: subprocess.Popen[bytes]) -> str:
        """terminate → grace → kill → grace; returns how it ended.

        A child that dies between the timeout check and terminate/kill is
        still a dead child — signal OSError is not a failure of cleanup.
        """
        import contextlib

        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
            return "terminated"
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(OSError):
            proc.kill()
        try:
            proc.wait(timeout=_KILL_GRACE_SECONDS)
            return "killed"
        except subprocess.TimeoutExpired:
            return "stuck"

    def _stdio_tail(self, path: Path) -> str:
        """Bounded, redacted tail of captured child output."""
        try:
            data = path.read_bytes()[-_STDIO_TAIL_BYTES:]
        except OSError:
            return ""
        if not data:
            return ""
        return redact(data.decode("utf-8", errors="replace")).text.strip()

    def _delegate_via_process(
        self,
        handoff: InvestigationHandoffV1,
        request_dict: dict[str, Any],
        *,
        context_root: Path,
    ) -> DoctorResult:
        """Delegate through the Doctor boundary inside a dedicated child.

        Hard-budget semantics: `self._timeout` bounds total child
        wall-clock (spawn + boundary + write). A breach terminates, then
        kills the child and yields a named `timed_out` — never a hang,
        never a fabricated bundle.
        """
        payload = json.dumps(
            {"request": request_dict, "context_root": str(context_root)},
            default=str,
        )
        if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise IntegrationRefused(
                DOCTOR_FORGE,
                f"doctor request exceeds {MAX_REQUEST_BYTES} bytes",
            )
        # Best-effort cleanup: on Windows a killed child's inherited
        # stdio handles can linger past wait() for a few ms — every
        # artifact is already read before teardown, so a scratch dir
        # left in the system temp is harmless, never a failure.
        with tempfile.TemporaryDirectory(
            prefix="sentinel-doctor-", ignore_cleanup_errors=True
        ) as td:
            scratch = Path(td)
            request_path = scratch / "request.json"
            out_dir = scratch / "out"
            out_dir.mkdir()
            request_path.write_text(payload, encoding="utf-8")
            stdout_path = scratch / "child.stdout.log"
            stderr_path = scratch / "child.stderr.log"
            timed_out = False
            with (
                stdout_path.open("wb") as out_fp,
                stderr_path.open("wb") as err_fp,
            ):
                proc = subprocess.Popen(  # noqa: S603 — argv list, fixed interpreter, no shell
                    self._worker_argv(request_path, out_dir),
                    cwd=scratch,
                    env=self._child_env(),
                    stdout=out_fp,
                    stderr=err_fp,
                )
                try:
                    rc = proc.wait(timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    ended = self._terminate_child(proc)
                    rc = -1
            stderr_tail = self._stdio_tail(stderr_path)
            if timed_out:
                if ended == "stuck":
                    return DoctorResult(
                        handoff_id=handoff.handoff_id,
                        mode=self._mode,
                        status="failed",
                        detail="process_termination_failure: child survived "
                        "terminate+kill escalation",
                    )
                return DoctorResult(
                    handoff_id=handoff.handoff_id,
                    mode=self._mode,
                    status="timed_out",
                    detail=f"execution_timeout: child exceeded "
                    f"{self._timeout}s wall-clock; {ended}"
                    + (f"; stderr: {stderr_tail[:200]}" if stderr_tail else ""),
                )
            return self._result_from_exit(handoff, rc, out_dir, stderr_tail)

    def _result_from_exit(
        self,
        handoff: InvestigationHandoffV1,
        rc: int,
        out_dir: Path,
        stderr_tail: str,
    ) -> DoctorResult:
        """Map worker exit + artifacts to a named result — never guess."""
        meta_detail = self._worker_meta(out_dir)
        tail = f"; stderr: {stderr_tail[:200]}" if stderr_tail else ""
        if rc == EXIT_OK:
            bundle_path = out_dir / "bundle.json"
            if not bundle_path.is_file():
                return DoctorResult(
                    handoff_id=handoff.handoff_id,
                    mode=self._mode,
                    status="failed",
                    detail="invalid_response: worker exited 0 without a bundle",
                )
            try:
                text = read_text_bounded(bundle_path, name="doctor bundle")
                ingest = ingest_doctor_bundle_json(text)
            except (ContractError, ValidationError) as exc:
                return DoctorResult(
                    handoff_id=handoff.handoff_id,
                    mode=self._mode,
                    status="failed",
                    detail=f"invalid_response: {exc}",
                )
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="completed",
                ingest=ingest,
                detail="delegated through DoctorBoundary in an isolated child process",
            )
        if rc == EXIT_UNAVAILABLE:
            return DoctorResult(
                handoff_id=handoff.handoff_id,
                mode=self._mode,
                status="unavailable",
                detail=f"{meta_detail or 'provider_unavailable'}{tail}",
            )
        reason = {
            EXIT_USAGE: "invalid_request",
            EXIT_INVALID_RESPONSE: "invalid_response",
            EXIT_FAILED: "provider_failed",
        }.get(rc, "provider_failed")
        return DoctorResult(
            handoff_id=handoff.handoff_id,
            mode=self._mode,
            status="failed",
            detail=f"{reason}: {meta_detail or f'worker exit {rc}'}{tail}",
        )

    def _worker_meta(self, out_dir: Path) -> str:
        """Read the worker's side record — bounded, parse-tolerant."""
        meta_path = out_dir / "meta.json"
        if not meta_path.is_file():
            return ""
        try:
            meta = json.loads(read_text_bounded(meta_path, name="worker meta"))
        except (ContractError, ValidationError, ValueError):
            return ""
        detail = meta.get("detail") if isinstance(meta, dict) else None
        return str(detail)[:400] if detail else ""

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
        envelope_path.write_text(handoff.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
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
        return ingest_doctor_bundle_json(text, expected_handoff_id=expected_handoff_id)

    # -- receipts -------------------------------------------------------

    def _write_receipt(
        self,
        receipts_dir: Path,
        result: DoctorResult,
        now_iso: str | None,
    ) -> None:
        from datetime import datetime

        at = datetime.fromisoformat(now_iso) if now_iso else datetime.now(UTC)
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
