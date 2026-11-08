# 01 — Product Requirements Document (PRD)

## Problem

Engineers operating HTTP APIs cannot answer quickly and *credibly* whether
a service is degrading, what changed around it, and why. Existing tools
fragment the answer across dashboards, APMs, contract linters and chat
threads. In the FORGE ecosystem there is still no owner of **temporal
observation of running APIs** nor of the **operational incident
investigation process**.

## Audience

| Persona | Need |
| --- | --- |
| Beginner user | Guided install, readable states, no CLI knowledge required |
| API engineer | Contract↔runtime correlation, regression triage |
| SRE | SLIs/SLOs, error budgets, dedup'd incidents, audit |
| Adapter developer | Stable ports and versioned envelopes |
| FORGE maintainer | Clear ownership boundaries and compatibility |

## Journeys

1. **First investigation**: install → register a service → import lab
   telemetry → observe a degradation → open an incident → reach a
   hypothesis with evidence and a verifiable next step.
2. **SLO review**: define an SLO → evaluate windows → see budget burn and
   honest gaps where telemetry is missing.
3. **Handoff to specialist**: during investigation, delegate a bounded
   evidence package to Doctor API and receive findings+unknowns back as
   evidence.
4. **Portal investigation**: open `/api-operations`, pick a service, read
   state + evidences + hypotheses without touching the CLI.

## Requirements (MVP 0.1 scope)

In: guided CLI install; inventory & identity; JSONL/OTLP JSON import;
SQLite + evidence; correct HTTP metrics; basic SLOs; deterministic
detection; incidents & timeline; Doctor handoff; Code Atlas integration;
registration in The Forge; API Operations in the Portal; one real lab
degradation end to end.

Out: continuous CloudWatch, Azure Monitor, autonomous AI, fix execution,
distributed multi-cloud, payload capture, Arazzo workflows, active
security scanning, high-scale infra.

## Non-goals

The Sentinel must never duplicate: Doctor's analyzers, Atlas's structural
graph, The Forge's router, Bridge's agent runtime, Lens's agent tracing,
Data Sentinel's pipeline diagnosis, Portal's frontend.

## Acceptance

Cycle gates defined per cycle in `docs/cycles/`. MVP value gate is the
product vision's success measure: a complete, reproducible investigation
over a real lab degradation.
