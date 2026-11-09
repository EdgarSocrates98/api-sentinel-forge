"""Forge Doctor API adapter — Sentinel evidence in, Doctor findings back.

Two honest modes (docs/06): `in_process` delegates through the optional
`forge_doctor_api` package when importable — the Sentinel never reimplements
Doctor's analysis; `handoff_file` emits the versioned
`api-sentinel/InvestigationHandoff/v1` envelope plus a ForgeRequest-shaped
request under `handoffs/` for a separately-run Doctor to consume. Doctor
findings/unknowns return verbatim, provenance intact — never reclassified.
"""

from api_sentinel.integrations.doctor.adapter import (
    DoctorACL,
    DoctorMode,
    DoctorResult,
)
from api_sentinel.integrations.doctor.translate import (
    handoff_to_doctor_request,
    ingest_doctor_bundle,
)

__all__ = [
    "DoctorACL",
    "DoctorMode",
    "DoctorResult",
    "handoff_to_doctor_request",
    "ingest_doctor_bundle",
]
