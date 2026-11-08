# 08 — SLO & Detection Design

## First-class indicators

| Indicator | Operational definition |
| --- | --- |
| Request rate | total requests per interval |
| Error ratio | error-classified requests / eligible requests |
| Availability SLI | good requests / eligible requests |
| Latency SLI | requests within threshold / eligible requests |
| p50/p95/p99 | quantiles over compatible distributions |
| Timeout ratio | timeouts / eligible requests |
| Saturation | measure defined by infrastructure |
| Retry amplification | repeated calls vs logical work |
| Dependency health | evidence per dependency |
| Traffic baseline | comparison with compatible periods |
| Coverage | completeness/representativeness of measurement |

Good/bad classification varies per SLO and service. Not every source can
supply every indicator — absence is reported, not interpolated.

## SLO formulas

`SLI = good_requests / eligible_requests`
`B = N(1 − S)` — error budget in requests (N eligible, S objective).
`Budget consumed = bad_requests / N(1 − S)`.

Valid only when counters share population definition, window and coverage.

## Alerting — multiwindow multi-burn-rate

Google SRE Workbook alerting is the reference, with low-traffic caveats:
one bad request in a quiet service can burn a large budget fraction and
over-alert.

Therefore: explicit short+long window rules, minimum volume where needed,
hysteresis, per-rule cooldown, planned suppression, dedup by
service/cause, frequency caps, "no traffic" handled apart from failure,
rule-version recorded, and every alert explains the math that fired it.

Never conclude an API is down because no requests arrived — missing
traffic, missing collection and unavailability are different conditions.

## Detection engine — three levels

**Level 1 — deterministic rules (MVP).** Error ratio over limit; p95 over
latency target; source stopped sending; budget burn acceleration; timeout
growth after deploy; unexpected status on an operation.

**Level 2 — robust statistics (after reliable history).** Median+MAD,
EWMA, day/hour baselines, window comparison, persistent change detection,
seasonality — all evaluated on a known corpus; no root-cause promises.

**Level 3 — agentic investigation.** AI interprets evidence and proposes
checks; never invents anomalies nor replaces deterministic detection.
Confidence ladder: `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`;
VERIFIED only via independent tool/procedure evidence.

## Detector protocol

```python
class Detector(Protocol):
    @property
    def detector_id(self) -> str: ...
    @property
    def detector_version(self) -> str: ...
    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult: ...
```

Rules: no network inside a detector; no uninjected real clock; no direct
credentials; no implicit persistence; typed results; explicit evidence
and limitations; deterministic tests; no arbitrary user-sent rule
execution. This shape enables replay and detector-version evaluation.
