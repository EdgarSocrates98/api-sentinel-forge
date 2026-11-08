"""Unit: unit normalization, semconv variants, kind preservation, quality assessment."""

from datetime import UTC, datetime, timedelta

from api_sentinel.domain import MeasurementKind, QualityState
from api_sentinel.ingestion.models import Measurement
from api_sentinel.ingestion.normalize import (
    attribute_variant,
    canonical_unit,
    normalize_measurement,
)
from api_sentinel.ingestion.quality import assess_quality

NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


def _hist(**kw: object) -> Measurement:
    base: dict[str, object] = {
        "kind": MeasurementKind.HISTOGRAM,
        "unit": "ms",
        "count": 120,
        "sum": 14400.0,
        "buckets": [
            {"le": 50.0, "count": 10},
            {"le": 100.0, "count": 55},
            {"le": "+Inf", "count": 120},
        ],
    }
    base.update(kw)
    return Measurement(**base)  # type: ignore[arg-type]


def test_seconds_convert_to_ms_losslessly() -> None:
    m = _hist(unit="s", sum=14.4, buckets=[
        {"le": 0.05, "count": 10}, {"le": 0.1, "count": 55}, {"le": "+Inf", "count": 120}
    ])
    out, issues = normalize_measurement(m)
    assert issues == []
    assert out.unit == "ms"
    assert out.sum == 14400.0
    assert out.buckets[0].le == 50.0 and out.buckets[1].le == 100.0
    assert out.buckets[2].le == "+Inf"
    assert any("unit:s→ms" in n for n in out.transform_notes)


def test_milliseconds_pass_through_unchanged() -> None:
    out, issues = normalize_measurement(_hist())
    assert issues == []
    assert out.unit == "ms" and out.sum == 14400.0
    assert out.transform_notes == []


def test_unknown_unit_is_invalid_never_assumed() -> None:
    _out, issues = normalize_measurement(_hist(unit="furlongs"))
    assert "unknown_unit" in issues
    quality, _ = assess_quality(
        declared=QualityState.COMPLETE, sampling_rate=None, dropped=False,
        measurement_issues=issues, window_end=NOW - timedelta(minutes=1),
        ingest_time=NOW, policy_stale_after_seconds=300,
        policy_lateness_seconds=120, temporality=None,
    )
    assert quality == QualityState.INVALID


def test_missing_unit_on_numeric_kind_is_invalid() -> None:
    m = Measurement(kind=MeasurementKind.COUNTER, unit=None, value=5.0)
    _, issues = normalize_measurement(m)
    assert "missing_unit" in issues


def test_histogram_is_never_reduced_to_a_counter() -> None:
    out, _ = normalize_measurement(_hist())
    assert out.kind == MeasurementKind.HISTOGRAM
    assert out.count == 120 and len(out.buckets) == 3
    # no synthetic request counter materializes
    assert out.value is None


def test_counter_and_gauge_keep_their_kind() -> None:
    c, _ = normalize_measurement(
        Measurement(kind=MeasurementKind.COUNTER, unit="requests", value=42.0)
    )
    g, _ = normalize_measurement(
        Measurement(kind=MeasurementKind.GAUGE, unit="requests", value=7.0)
    )
    assert c.kind == MeasurementKind.COUNTER and c.unit == "requests" and c.value == 42.0
    assert g.kind == MeasurementKind.GAUGE and g.value == 7.0


def test_canonical_units_table() -> None:
    assert canonical_unit("s") == ("ms", 1000.0)
    assert canonical_unit("seconds") == ("ms", 1000.0)
    assert canonical_unit("us") == ("ms", 0.001)
    assert canonical_unit("requests") == ("requests", 1.0)
    assert canonical_unit("1") == ("count", 1.0)
    assert canonical_unit("percent") == ("ratio", 0.01)
    assert canonical_unit("parsec") is None


def test_semconv_attribute_variants() -> None:
    assert attribute_variant("http.method") == ("http.request.method", "legacy")
    assert attribute_variant("http.status_code") == ("http.response.status_code", "legacy")
    assert attribute_variant("http.request.method") == ("http.request.method", "stable")
    assert attribute_variant("http.route") == ("http.route", "stable")
    assert attribute_variant("x.custom") == ("x.custom", "custom")


def test_assess_never_labels_unsampled_as_sampled() -> None:
    for declared in (QualityState.COMPLETE, QualityState.PARTIAL, QualityState.UNKNOWN):
        q, _ = assess_quality(
            declared=declared, sampling_rate=None, dropped=False,
            measurement_issues=[], window_end=NOW - timedelta(minutes=1),
            ingest_time=NOW, policy_stale_after_seconds=300,
            policy_lateness_seconds=120, temporality=None,
        )
        assert q != QualityState.SAMPLED
        assert q == declared  # no detection → declared claim stands


def test_assess_marks_sampling_and_dropped() -> None:
    q, notes = assess_quality(
        declared=QualityState.COMPLETE, sampling_rate=0.25, dropped=False,
        measurement_issues=[], window_end=NOW - timedelta(minutes=1),
        ingest_time=NOW, policy_stale_after_seconds=300,
        policy_lateness_seconds=120, temporality=None,
    )
    assert q == QualityState.SAMPLED and any("sampling" in n for n in notes)
    q2, _ = assess_quality(
        declared=QualityState.COMPLETE, sampling_rate=None, dropped=True,
        measurement_issues=[], window_end=NOW - timedelta(minutes=1),
        ingest_time=NOW, policy_stale_after_seconds=300,
        policy_lateness_seconds=120, temporality=None,
    )
    assert q2 == QualityState.SAMPLED


def test_assess_marks_stale_beyond_threshold_and_late_within_tolerance() -> None:
    stale, _notes = assess_quality(
        declared=QualityState.COMPLETE, sampling_rate=None, dropped=False,
        measurement_issues=[], window_end=NOW - timedelta(minutes=10),
        ingest_time=NOW, policy_stale_after_seconds=300,
        policy_lateness_seconds=120, temporality=None,
    )
    assert stale == QualityState.STALE
    late, lnotes = assess_quality(
        declared=QualityState.COMPLETE, sampling_rate=None, dropped=False,
        measurement_issues=[], window_end=NOW - timedelta(minutes=3),
        ingest_time=NOW, policy_stale_after_seconds=300,
        policy_lateness_seconds=120, temporality=None,
    )
    assert late == QualityState.COMPLETE and any("late" in n for n in lnotes)


def test_declared_quality_never_upgraded() -> None:
    q, _ = assess_quality(
        declared=QualityState.PARTIAL, sampling_rate=None, dropped=False,
        measurement_issues=[], window_end=NOW - timedelta(minutes=1),
        ingest_time=NOW, policy_stale_after_seconds=300,
        policy_lateness_seconds=120, temporality=None,
    )
    assert q == QualityState.PARTIAL  # PARTIAL never surfaces as COMPLETE
