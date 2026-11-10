"""Wizard catalog — every `api-sentinel` command as a guided form.

Each entry maps to the *real* Typer app: the wizard collects fields,
builds argv, and the TUI runs it through `typer.testing.CliRunner` — so
validation, redaction and honest named states are exactly the CLI's,
never reimplemented. Commands that are inherently interactive or
long-running (install TTY prompts, `serve`, `mcp`) are honest `notice`
entries — the TUI says what to run instead of faking a session.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    flag: str
    label: str
    default: str = ""
    required: bool = False
    kind: str = "text"  # text | int | float | bool | choice
    choices: tuple[str, ...] = ()
    sensitive: bool = False


@dataclass(frozen=True)
class Command:
    group: str
    label: str
    argv: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()
    notice: str = ""  # set => explain instead of running


CATALOG: tuple[Command, ...] = (
    # --- Workspace --------------------------------------------------------
    Command("Workspace", "install — initialize workspace", ("install",),
            (Field("--profile", "Profile", "local",
                   choices=("local", "aws", "opentelemetry")),
             Field("--scope", "Scope", "project",
                   choices=("project", "user")),
             Field("--yes", "Non-interactive (-y)", kind="bool"))),
    Command("Workspace", "doctor — environment health", ("doctor",)),
    Command("Workspace", "status — workspace state", ("status",)),
    Command("Workspace", "workspace — resolved root + contents", ("workspace",)),
    Command("Workspace", "version", ("version",)),
    Command("Workspace", "capabilities — declared + real status", ("capabilities",)),
    Command("Workspace", "retention — policy (dry-run default)", ("retention",),
            (Field("--apply", "Apply deletions", kind="bool"),
             Field("--observations-days", "Observations days"),
             Field("--incidents-days", "Incidents days"),
             Field("--audit-days", "Audit days"))),
    Command("Workspace", "serve — local /v1 HTTP surface",
            notice="serve is a long-running HTTP server — run "
            "`api-sentinel serve` in a terminal. The TUI does not host "
            "background daemons."),
    Command("Workspace", "mcp — MCP stdio server",
            notice="mcp serves JSON-RPC over stdio to a host process — "
            "run `api-sentinel mcp serve` under the MCP client, not in a TUI."),

    # --- Observe ----------------------------------------------------------
    Command("Observe", "services — list / register", ("services",),
            (Field("--register", "Register service name (optional)"),
             Field("--environment", "Environment tag"),
             Field("--namespace", "Namespace tag"))),
    Command("Observe", "sources — list / register", ("sources",),
            (Field("--register", "Register kind (e.g. collector, simulated)"),
             Field("--service-id", "Service id (with --register)"),
             Field("--config", "JSON config payload", sensitive=True))),
    Command("Observe", "ingest — telemetry batch file", ("ingest",),
            (Field("--file", "Batch file (jsonl/json)", required=True),
             Field("--source-id", "Source id"),
             Field("--kind", "Payload kind"))),
    Command("Observe", "deps — observed dependency edges", ("deps",),
            (Field("--record", "Record edge target service id"),
             Field("--service-id", "Filter by service id"),
             Field("--target-kind", "Target kind"),
             Field("--window-start", "Window start (ISO-8601)"),
             Field("--window-end", "Window end (ISO-8601)"))),
    Command("Observe", "correlate — correlation report", ("correlate",),
            (Field("--service-id", "Service id"),
             Field("--window-start", "Window start (ISO-8601)"),
             Field("--window-end", "Window end (ISO-8601)"))),

    # --- Detect -------------------------------------------------------------
    Command("Detect", "detect — run registered rules", ("detect",),
            (Field("--service-id", "Filter service id"),
             Field("--window-start", "Window start (ISO-8601)"),
             Field("--window-end", "Window end (ISO-8601)"),
             Field("--now", "Injected clock (ISO-8601)"))),
    Command("Detect", "incidents — list (open filter)", ("incidents",),
            (Field("--status", "Status filter",
                   choices=("", "open", "acknowledged", "resolved")),
             Field("--service-id", "Filter service id"))),
    Command("Detect", "incident show", ("incident", "show"),
            (Field("incident_id", "Incident id", required=True, kind="arg"),)),
    Command("Detect", "incident ack", ("incident", "ack"),
            (Field("incident_id", "Incident id", required=True, kind="arg"),
             Field("--actor", "Actor", "tui"),
             Field("--note", "Ack note"))),
    Command("Detect", "incident resolve", ("incident", "resolve"),
            (Field("incident_id", "Incident id", required=True, kind="arg"),
             Field("--actor", "Actor", "tui"),
             Field("--note", "Resolution note"))),
    Command("Detect", "incident reopen", ("incident", "reopen"),
            (Field("incident_id", "Incident id", required=True, kind="arg"),
             Field("--actor", "Actor", "tui"),
             Field("--note", "Reopen reason"))),
    Command("Detect", "health — per-service health", ("health",),
            (Field("--service-id", "Service id", required=True),
             Field("--stale-after-seconds", "Stale after seconds", "300"))),

    # --- Diagnose -----------------------------------------------------------
    Command("Diagnose", "investigate — bounded pack + governed session",
            ("investigate",),
            (Field("--incident-id", "Incident id", required=True),
             Field("--principal", "Principal", "cli"),
             Field("--provider", "Provider"),
             Field("--cost-limit", "Cost limit (USD)"),
             Field("--budget-bytes", "Evidence byte budget"))),
    Command("Diagnose", "handoff doctor — delegate to Forge Doctor",
            ("handoff", "doctor"),
            (Field("--service-id", "Service id", required=True),
             Field("--target", "Doctor target"),
             Field("--capability", "Capability"),
             Field("--context-root", "Context root"),
             Field("--mode", "Mode",
                   choices=("", "in_process", "handoff_file")),
             Field("--timeout", "Timeout seconds", kind="int"))),
    Command("Diagnose", "handoff ingest — inbound handoff bundle",
            ("handoff", "ingest"),
            (Field("--bundle", "Handoff bundle file", required=True),)),
    Command("Diagnose", "handoff apiforge — delegate to API Forge",
            ("handoff", "apiforge"),
            (Field("--incident-id", "Incident id", required=True),
             Field("--hypothesis-id", "Hypothesis id", required=True),
             Field("--suggested-change", "Suggested change", required=True),
             Field("--evidence-ref", "Evidence ref"))),
    Command("Diagnose", "handoff apiforge-ingest — API Forge response",
            ("handoff", "apiforge-ingest"),
            (Field("--response", "Response file", required=True),)),

    # --- SLO ----------------------------------------------------------------
    Command("SLO", "slo define — new definition", ("slo", "define"),
            (Field("--service-id", "Service id", required=True),
             Field("--target", "Target (e.g. 0.99)", required=True),
             Field("--window", "Window (e.g. 15m)", required=True),
             Field("--sli", "SLI kind", "availability",
                   choices=("availability", "latency")),
             Field("--latency-threshold-ms", "Latency threshold ms"),
             Field("--operation-id", "Operation id"),
             Field("--error-status", "Error status class"))),
    Command("SLO", "slo evaluate — windowed assessment", ("slo", "evaluate"),
            (Field("--slo-id", "SLO id", required=True),
             Field("--window-start", "Window start (ISO-8601)", required=True),
             Field("--window-end", "Window end (ISO-8601)", required=True))),
    Command("SLO", "slo list — definitions", ("slo", "list"),
            (Field("--service-id", "Filter service id"),)),

    # --- Intelligence -------------------------------------------------------
    Command("Intelligence", "changes list", ("changes", "list"),
            (Field("--service-id", "Service id", required=True),
             Field("--at-start", "From (ISO-8601)"),
             Field("--at-end", "To (ISO-8601)"))),
    Command("Intelligence", "changes record", ("changes", "record"),
            (Field("--service-id", "Service id", required=True),
             Field("--kind", "Change kind", required=True),
             Field("--at", "When (ISO-8601)", required=True),
             Field("--ref", "Reference"),
             Field("--source", "Change source"),
             Field("--detail", "Detail"))),
    Command("Intelligence", "changes import — file", ("changes", "import"),
            (Field("--file", "Changes file", required=True),
             Field("--source", "Change source"))),
    Command("Intelligence", "changes correlate", ("changes", "correlate"),
            (Field("--incident-id", "Incident id", required=True),
             Field("--window-seconds", "Correlation window (s)", kind="int"))),
    Command("Intelligence", "hypotheses add", ("hypotheses", "add"),
            (Field("--incident-id", "Incident id", required=True),
             Field("--text", "Hypothesis", required=True),
             Field("--confidence", "Confidence"),
             Field("--evidence-for", "Evidence for"),
             Field("--evidence-against", "Evidence against"),
             Field("--unknown", "Unknown factor"))),
    Command("Intelligence", "hypotheses promote", ("hypotheses", "promote"),
            (Field("--incident-id", "Incident id", required=True),
             Field("--hypothesis-id", "Hypothesis id", required=True),
             Field("--verification-id", "Verification id", required=True),
             Field("--note", "Note"))),
    Command("Intelligence", "hypotheses demote", ("hypotheses", "demote"),
            (Field("--incident-id", "Incident id", required=True),
             Field("--hypothesis-id", "Hypothesis id", required=True),
             Field("--reason", "Reason", required=True),
             Field("--evidence-against", "Evidence against"))),
    Command("Intelligence", "hypotheses verify", ("hypotheses", "verify"),
            (Field("--incident-id", "Incident id", required=True),
             Field("--hypothesis-id", "Hypothesis id", required=True),
             Field("--window-start", "Post-change start (ISO-8601)",
                   required=True),
             Field("--window-end", "Post-change end (ISO-8601)",
                   required=True),
             Field("--doctor", "Include Doctor handoff", kind="bool"),
             Field("--now", "Injected clock (ISO-8601)"))),

    # --- Integrations -------------------------------------------------------
    Command("Integrations", "integrations — every Forge's honest state",
            ("integrations",)),
    Command("Integrations", "aws collect — read-only collector", ("aws", "collect"),
            (Field("--source-id", "Source id", required=True),
             Field("--window", "Window (e.g. 15m)"),
             Field("--end", "Window end (ISO-8601)"),
             Field("--simulate", "Simulated data (labeled)", kind="bool"),
             Field("--resume", "Resume cursor", kind="bool"),
             Field("--max-calls", "Max API calls", kind="int"),
             Field("--max-estimated-cost-usd", "Cost ceiling USD"))),
    Command("Integrations", "aws discover — resource discovery", ("aws", "discover"),
            (Field("--profile", "AWS profile"),
             Field("--region", "AWS region"),
             Field("--account-id", "Account id"),
             Field("--role-arn", "Role ARN"),
             Field("--simulate", "Simulated data (labeled)", kind="bool"))),

    # --- Backup ---------------------------------------------------------------
    Command("Backup", "backup create — verifiable snapshot", ("backup", "create"),
            (Field("--dest", "Destination directory"),)),
    Command("Backup", "backup restore — restore snapshot", ("backup", "restore"),
            (Field("--from", "Backup dir", required=True),
             Field("--force", "Overwrite target", kind="bool"))),
)


def build_argv(cmd: Command, values: dict[str, str], *, ws: str | None) -> list[str]:
    """argv for CliRunner: command + field flags + workspace flag."""
    argv = list(cmd.argv)
    for f in cmd.fields:
        v = values.get(f.flag, "")
        if f.kind == "bool":
            if v == "on":
                argv.append(f.flag)
        elif f.kind == "arg":
            if v:
                argv.append(v)
        elif v:
            argv += [f.flag, v]
    if ws:
        argv += ["--workspace", ws]
    return argv


def display_argv(cmd: Command, values: dict[str, str], *, ws: str | None) -> list[str]:
    """Return a safe presentation argv while preserving execution argv."""
    argv = build_argv(cmd, values, ws=ws)
    sensitive = {
        f.flag.lower() for f in cmd.fields if f.sensitive
    }
    sensitive.update({
        "--password", "--token", "--secret", "--api-key", "--apikey",
        "--authorization", "--credential", "--bearer",
    })
    out: list[str] = []
    mask_next = False
    for item in argv:
        if mask_next:
            out.append("[REDACTED]")
            mask_next = False
            continue
        out.append(item)
        if item.lower() in sensitive:
            mask_next = True
    return out
