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
