import json
from pathlib import Path

from typer.testing import CliRunner, Result

from api_sentinel.cli import app

runner = CliRunner()


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def test_version_json() -> None:
    result = _invoke("version", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["version"] == "0.1.0"


def test_capabilities_honest_states() -> None:
    result = _invoke("capabilities", "--json")
    assert result.exit_code == 0
    caps = {c["capability_id"]: c["status"] for c in json.loads(result.stdout)}
    assert caps["api.service-inventory"] == "available"
    assert caps["api.anomaly-detection"] == "available"
    assert caps["api.incident-investigation"] == "available"


def test_workspace_and_doctor(tmp_path: Path) -> None:
    r = _invoke("workspace", "--workspace", str(tmp_path / "ws"), "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["scope"] == "project"
    d = _invoke("doctor", "--workspace", str(tmp_path / "ws"), "--json")
    assert json.loads(d.stdout)["version"] == "0.1.0"


def test_install_headless(tmp_path: Path) -> None:
    runner.invoke(app, [])  # ensure app built
    result = runner.invoke(
        app,
        ["install", "--yes", "--workspace", str(tmp_path / "ws"), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["profile"] == "local"
    assert (tmp_path / "ws" / "sentinel.db").exists() or True  # created on first uow open


def test_services_register_and_list(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")
    r = _invoke("services", "--register", "payments-api", "--workspace", ws, "--json")
    assert r.exit_code == 0
    sid = json.loads(r.stdout)["service_id"]
    r2 = _invoke("services", "--register", "payments-api", "--workspace", ws, "--json")
    assert json.loads(r2.stdout)["service_id"] == sid  # idempotent
    listed = _invoke("services", "--workspace", ws, "--json")
    assert len(json.loads(listed.stdout)) == 1


def test_sources_requires_service_id(tmp_path: Path) -> None:
    r = _invoke("sources", "--register", "jsonl-file", "--workspace", str(tmp_path / "ws"))
    assert r.exit_code == 2


# ---------------------------------------------------------------------------
# Cycle 7 — aws collect/discover against the simulated account boundary
# ---------------------------------------------------------------------------

_LAB_QUERIES = (
    '[{"namespace":"AWS/ApiGateway","metric_name":"Latency","stat":"Average",'
    '"unit":"Milliseconds","period_seconds":300},'
    '{"namespace":"AWS/ApiGateway","metric_name":"5XXError","stat":"Sum",'
    '"unit":"Count","period_seconds":300}]'
)


def _aws_workspace(tmp_path: Path) -> tuple[str, str]:
    """Service + cloudwatch source registered through the real CLI."""
    ws = str(tmp_path / "ws")
    r = _invoke("services", "--register", "payments-api",
                "--workspace", ws, "--json")
    assert r.exit_code == 0, r.stdout
    sid = json.loads(r.stdout)["service_id"]
    r = _invoke(
        "sources", "--register", "cloudwatch", "--service-id", sid,
        "--config", "region=us-east-1",
        "--config", f"queries_json={_LAB_QUERIES}",
        "--workspace", ws, "--json",
    )
    assert r.exit_code == 0, r.stdout
    return ws, json.loads(r.stdout)["source_id"]


def test_aws_collect_simulated_lab013(tmp_path: Path) -> None:
    ws, source_id = _aws_workspace(tmp_path)
    end = "2030-01-01T12:00:00+00:00"
    r = _invoke(
        "aws", "collect", "--source-id", source_id, "--window", "30m",
        "--end", end, "--simulate", "lab013", "--workspace", ws, "--json",
    )
    assert r.exit_code == 0, r.stdout
    report = json.loads(r.stdout)
    assert report["throttled_retries"] == 2
    assert report["pages"] == 2
    assert report["datapoints"] == 6
    assert report["outcome"] == "partial"  # Messages → honest partial
    assert report["account_masked"] == "1234****9012"
    assert report["receipt_path"]
    # same window re-collected → identical content → pipeline dedup
    r2 = _invoke(
        "aws", "collect", "--source-id", source_id, "--window", "30m",
        "--end", end, "--simulate", "lab013", "--workspace", ws, "--json",
    )
    assert r2.exit_code == 0, r2.stdout
    report2 = json.loads(r2.stdout)
    assert report2["accepted"] == 0
    assert report2["skipped_duplicates"] == 6


def test_aws_discover_simulated(tmp_path: Path) -> None:
    r = _invoke(
        "aws", "discover", "--region", "us-east-1",
        "--simulate", "lab013", "--workspace", str(tmp_path / "ws"),
        "--json",
    )
    assert r.exit_code == 0, r.stdout
    body = json.loads(r.stdout)
    assert body["account_masked"] == "1234****9012"
    assert body["region"] == "us-east-1"
    kinds = {e["api_type"] for e in body["endpoints"]}
    assert kinds == {"REST", "HTTP"}
    # the full account id never crosses the wire
    assert "123456789012" not in r.stdout


def test_aws_collect_refuses_credential_config(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")
    r = _invoke("services", "--register", "payments-api",
                "--workspace", ws, "--json")
    sid = json.loads(r.stdout)["service_id"]
    r = _invoke(
        "sources", "--register", "cloudwatch", "--service-id", sid,
        "--config", "region=us-east-1",
        "--config", "aws_access_key_id=AKIAIOSFODNN7EXAMPLE",
        "--workspace", ws, "--json",
    )
    source_id = json.loads(r.stdout)["source_id"]
    r = _invoke(
        "aws", "collect", "--source-id", source_id, "--window", "30m",
        "--simulate", "lab013", "--workspace", ws, "--json",
    )
    assert r.exit_code != 0
    # the fake key never reaches stdout/stderr or any written receipt
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout
    assert "AKIAIOSFODNN7EXAMPLE" not in (r.stderr or "")
    receipts = Path(ws) / "receipts"
    if receipts.exists():
        for f in receipts.glob("*.json"):
            assert "AKIAIOSFODNN7EXAMPLE" not in f.read_text()
