"""Agentic investigation — bounded packs, governed sessions, honest
hypotheses, independent verification and change intelligence (Cycle 8).

The Sentinel stays deterministic: no LLM calls in core, no agent
runtime — `AgentSessionPort` is the governed seam to the Forge Agent
Bridge; every hypothesis transition and verification verdict lands in
the append-only incident journal.
"""

from api_sentinel.investigation.agent_session import (
    STATUS_DELEGATED,
    STATUS_FAILED,
    STATUS_REFUSED,
    STATUS_UNAVAILABLE,
    AgentSessionPort,
    AgentSessionRequest,
    AgentSessionResult,
    SessionGovernance,
    build_session_request,
    session_request_id,
    session_result_summary,
)
from api_sentinel.investigation.changes import (
    PROXIMITY_DISCLAIMER,
    ChangeCorrelationReport,
    CorrelatedChange,
    CorrelateIncidentChanges,
    ImportDeployManifest,
    ManifestImportReport,
    correlate_changes,
)
from api_sentinel.investigation.context_pack import (
    DEFAULT_BUDGET_BYTES,
    ContextPack,
    PackInstructions,
    PackItem,
    PackSection,
    build_context_pack,
)
from api_sentinel.investigation.hypotheses import (
    CREATABLE_CONFIDENCE,
    AddHypothesis,
    DemoteHypothesis,
    PromoteHypothesis,
    create_hypothesis,
    demote_hypothesis,
    hypothesis_id_for,
    promote_hypothesis,
    record_verification,
)
from api_sentinel.investigation.service import (
    AgenticInvestigationOutcome,
    RunAgenticInvestigation,
)
from api_sentinel.investigation.verification import (
    VERDICT_INCONCLUSIVE,
    VERDICT_REFUTED,
    VERDICT_VERIFIED,
    DetectionReDetector,
    RunVerification,
    StepReceipt,
    VerificationExecutor,
    VerificationPlan,
    VerificationResult,
    VerificationStep,
    default_verification_plan,
    verification_id_for,
)

__all__ = [
    "CREATABLE_CONFIDENCE",
    "DEFAULT_BUDGET_BYTES",
    "PROXIMITY_DISCLAIMER",
    "STATUS_DELEGATED",
    "STATUS_FAILED",
    "STATUS_REFUSED",
    "STATUS_UNAVAILABLE",
    "VERDICT_INCONCLUSIVE",
    "VERDICT_REFUTED",
    "VERDICT_VERIFIED",
    "AddHypothesis",
    "AgentSessionPort",
    "AgentSessionRequest",
    "AgentSessionResult",
    "AgenticInvestigationOutcome",
    "ChangeCorrelationReport",
    "ContextPack",
    "CorrelateIncidentChanges",
    "CorrelatedChange",
    "DemoteHypothesis",
    "DetectionReDetector",
    "ImportDeployManifest",
    "ManifestImportReport",
    "PackInstructions",
    "PackItem",
    "PackSection",
    "PromoteHypothesis",
    "RunAgenticInvestigation",
    "RunVerification",
    "SessionGovernance",
    "StepReceipt",
    "VerificationExecutor",
    "VerificationPlan",
    "VerificationResult",
    "VerificationStep",
    "build_context_pack",
    "build_session_request",
    "correlate_changes",
    "create_hypothesis",
    "default_verification_plan",
    "demote_hypothesis",
    "hypothesis_id_for",
    "promote_hypothesis",
    "record_verification",
    "session_request_id",
    "session_result_summary",
    "verification_id_for",
]
