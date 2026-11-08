# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Current development line |

This project is pre-1.0; only the latest minor line receives fixes.

## Reporting a vulnerability

Please do not open public issues for security problems.
Report privately to the maintainer (repository owner) with:

- affected version/commit,
- reproduction steps,
- impact assessment,
- whether a fix or workaround is known.

Expect acknowledgement within 7 days. Do not disclose publicly until a
fix lands or 90 days pass, whichever comes first.

## Security posture in short

- Local-first: the core runs offline; external inputs are opt-in.
- All persisted text crosses secret redaction; evidence is hashed.
- No arbitrary command execution endpoints exist by design — see
  `docs/07-security-threat-model.md` and ADR-013/ADR-014.
