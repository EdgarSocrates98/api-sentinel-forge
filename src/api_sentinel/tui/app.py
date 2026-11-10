"""API Sentinel TUI — a real Textual shell over the governed CLI.

Design rules inherited from the core invariants:

- Panels read through `data.py` loaders; an absent workspace/database is
  a *named* `unavailable` note, never fabricated rows.
- Commands run through the real Typer app (`CliRunner`) — the TUI never
  reimplements validation, redaction or state machines.
- Long-running/interactive commands (`serve`, `mcp`) are honest notices.
"""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)
from typer.testing import CliRunner

from api_sentinel.cli import app as cli_app
from api_sentinel.workspace import Workspace

from .catalog import CATALOG, Command, Field, build_argv
from .data import (
    PanelData,
    load_audit,
    load_health,
    load_incidents,
    load_integrations,
    load_services,
    load_slos,
)

CSS = """
#nav { width: 26; border-right: solid $primary; }
#nav ListItem { padding: 0 1; }
.panel { padding: 1 2; }
Static.title { text-style: bold; color: $accent; padding-bottom: 1; }
Static.note { color: $warning; padding: 1 0; }
DataTable { height: 1fr; }
#out { height: 1fr; border: solid $secondary; }
WizardScreen { align: center middle; }
#wizard { width: 72; max-height: 90%; border: solid $accent;
          background: $surface; padding: 1 2; }
#wizfields { height: auto; max-height: 60%; }
.fieldrow { height: auto; }
.fieldrow Label { width: 34; padding-top: 1; }
.fieldrow Input, .fieldrow Select { width: 1fr; }
#wizbtns { padding-top: 1; height: auto; }
Button { margin-right: 1; }
"""

NAV = [
    ("Dashboard", "dash"),
    ("Services & Sources", "services"),
    ("Incidents", "incidents"),
    ("SLO", "slos"),
    ("Health", "health"),
    ("Integrations", "integrations"),
    ("Commands ⌘", "commands"),
]


def _table(data: PanelData) -> DataTable[str]:
    t: DataTable[str] = DataTable(zebra_stripes=True, cursor_type="row")
    if data.columns:
        t.add_columns(*data.columns)
    for row in data.rows:
        t.add_row(*row)
    return t


class WizardScreen(ModalScreen[dict[str, str] | None]):
    """One guided form per command — fields from the catalog."""

    def __init__(self, cmd: Command) -> None:
        super().__init__()
        self._cmd = cmd

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard"):
            yield Static(self._cmd.label, classes="title")
            if self._cmd.notice:
                yield Static(f"[i]{self._cmd.notice}[/i]", classes="note")
            with VerticalScroll(id="wizfields"):
                for f in self._cmd.fields:
                    with Horizontal(classes="fieldrow"):
                        yield Label(
                            f.label + (" *" if f.required else "")
                        )
                        if f.kind == "bool":
                            yield Checkbox(
                                f.label, value=False, id=f"f{f.flag}"
                            )
                        elif f.choices:
                            yield Select(
                                [(c, c) for c in f.choices if c],
                                value=(f.default or Select.BLANK),
                                id=f"f{f.flag}",
                                allow_blank=not f.required,
                            )
                        else:
                            yield Input(
                                value=f.default,
                                placeholder=f.flag,
                                id=f"f{f.flag}",
                            )
            with Horizontal(id="wizbtns"):
                yield Button("Run", variant="success", id="run")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        missing = [
            f.label
            for f in self._cmd.fields
            if f.required and not self._value(f)
        ]
        if missing:
            self.app.push_screen(
                NoticeScreen(
                    f"Required field(s) missing: {', '.join(missing)}"
                )
            )
            return
        self.dismiss({f.flag: self._value(f) for f in self._cmd.fields})

    def _value(self, f: Field) -> str:
        try:
            w = self.query_one(f"#f{f.flag}")
        except Exception:
            return ""
        if isinstance(w, Checkbox):
            return "on" if w.value else ""
        if isinstance(w, Select):
            return "" if w.value is Select.BLANK else str(w.value)
        return str(getattr(w, "value", "")).strip()


class NoticeScreen(ModalScreen[None]):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard"):
            yield Static(self._text, classes="note")
            yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class SentinelApp(App[None]):
    TITLE = "API Sentinel"
    SUB_TITLE = "observe · correlate · diagnose · verify"
    CSS = CSS
    BINDINGS = [  # noqa: RUF012 - textual expects a mutable class attr
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "commands", "Commands"),
    ]

    def __init__(self, ws: Workspace, ws_arg: str | None = None) -> None:
        super().__init__()
        self.ws = ws
        self.ws_arg = ws_arg
        self._incident_sel: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="nav"):
                yield ListView(
                    *[ListItem(Static(n), id=k) for n, k in NAV],
                    id="navlist",
                )
            with Vertical(id="body"):
                yield self._panel("dash")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#navlist", ListView).index = 0

    # -- panels -------------------------------------------------------------

    def _panel(self, key: str) -> Vertical:
        children: list[Static | DataTable[str] | Horizontal] = []
        if key == "dash":
            children += [
                Static("Dashboard", classes="title"),
                Static(
                    f"workspace: {self.ws.root}\ndatabase:  {self.ws.db_path}"
                ),
                Static("Recent audit events", classes="title"),
                _table(load_audit(self.ws)),
            ]
        elif key == "services":
            children += [
                Static("Services & Sources", classes="title"),
                _table(load_services(self.ws)),
            ]
        elif key == "incidents":
            children += [
                Static(
                    "Incidents — lifecycle via Commands > incident …",
                    classes="title",
                ),
                _table(load_incidents(self.ws)),
            ]
        elif key == "slos":
            children += [
                Static("SLO definitions + latest assessment", classes="title"),
                _table(load_slos(self.ws)),
            ]
        elif key == "health":
            children += [
                Static("Service health (honest unknowns)", classes="title"),
                _table(load_health(self.ws)),
            ]
        elif key == "integrations":
            children += [
                Static("Integrations — honest states only", classes="title"),
                _table(load_integrations(self.ws)),
            ]
        elif key == "commands":
            children += [
                Static("Command wizard — pick a command", classes="title"),
                Horizontal(
                    ListView(
                        *[
                            ListItem(
                                Static(f"[{c.group}] {c.label}"), id=f"cmd{i}"
                            )
                            for i, c in enumerate(CATALOG)
                        ],
                        id="cmdlist",
                    ),
                    RichLog(id="out", markup=True),
                ),
            ]
        return Vertical(*children, id="body_inner", classes="panel")

    # -- events -------------------------------------------------------------

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "navlist":
            await self._refresh(event.item.id or "dash")
        elif event.list_view.id == "cmdlist":
            idx = int((event.item.id or "cmd0")[3:])
            self._open_wizard(CATALOG[idx])

    def _open_wizard(self, cmd: Command) -> None:
        if cmd.notice:
            self._logline(f"[yellow]{cmd.label}[/]\n{cmd.notice}")
            return
        if not cmd.fields:
            self._run_command(cmd, {})
            return
        def _on_wizard_done(values: dict[str, str] | None) -> None:
            if values is not None:
                self._run_command(cmd, values)

        self.push_screen(WizardScreen(cmd), _on_wizard_done)

    def _logline(self, text: str) -> None:
        import contextlib

        # off the commands panel there is no log sink — drop silently.
        with contextlib.suppress(Exception):
            self.query_one("#out", RichLog).write(text)

    @work(thread=True)
    def _run_command(self, cmd: Command, values: dict[str, str]) -> None:
        argv = build_argv(cmd, values, ws=self.ws_arg)
        self._logline(f"[bold cyan]$ api-sentinel {' '.join(argv)}[/]")
        result = CliRunner().invoke(cli_app, argv)
        out = result.output.strip() or "(no output)"
        status = "[green]ok[/]" if result.exit_code == 0 else (
            f"[red]exit {result.exit_code}[/]"
        )
        self._logline(f"{status}\n{out}")
        if result.exit_code != 0 and result.exception:
            self._logline(f"[red]{type(result.exception).__name__}: "
                    f"{result.exception}[/]")
        # Refresh data panels the command may have touched — but never the
        # commands panel itself: rebuilding it would wipe the output log.
        if getattr(self, "_current", "dash") != "commands":
            self.call_from_thread(
                lambda: self.run_worker(
                    self._refresh_current(), exclusive=True, group="refresh"
                )
            )

    # -- actions --------------------------------------------------------------

    async def action_refresh(self) -> None:
        await self._refresh_current()

    async def action_commands(self) -> None:
        self.query_one("#navlist", ListView).index = len(NAV) - 1
        await self._refresh("commands")

    async def _refresh_current(self) -> None:
        await self._refresh(getattr(self, "_current", "dash"))

    async def _refresh(self, key: str) -> None:
        body = self.query_one("#body")
        await body.remove_children()
        body.mount(self._panel(key))
        self._current = key


def run_tui(ws: Workspace, ws_arg: str | None = None) -> int:
    SentinelApp(ws, ws_arg).run()
    return 0
