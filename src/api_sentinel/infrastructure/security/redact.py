"""Secret redaction — applied before persistence and at presentation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)\baws_secret_access_key\s*[=:]\s*\S+")),
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[^-]*-----END [A-Z ]*PRIVATE KEY-----", re.S
        ),
    ),
    ("bearer", re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+")),
    ("basic_auth", re.compile(r"(?i)(authorization\s*[:=]\s*basic\s+)\S+")),
    (
        "token_param",
        re.compile(r"(?i)\b(token|api[_-]?key|secret|password|passwd|pwd)\s*[=:]\s*\S+"),
    ),
]

MASK = "[REDACTED]"


@dataclass(frozen=True)
class RedactionFinding:
    kind: str
    count: int


@dataclass(frozen=True)
class RedactionResult:
    text: str
    findings: list[RedactionFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings


def redact(text: str) -> RedactionResult:
    """Mask recognized secret material; report what was masked."""
    findings: list[RedactionFinding] = []
    out = text
    for kind, pattern in _PATTERNS:
        matches = pattern.findall(out)
        if matches:
            out = pattern.sub(lambda m: (m.group(1) + MASK) if m.groups() else MASK, out)
            findings.append(RedactionFinding(kind=kind, count=len(matches)))
    return RedactionResult(text=out, findings=findings)
