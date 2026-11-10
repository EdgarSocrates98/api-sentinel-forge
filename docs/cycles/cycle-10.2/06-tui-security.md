# TUI Security

`RichLog` remains available for trusted static presentation, but all CLI
output, exception text, observations and provider responses are converted to
literal `rich.text.Text` renderables. This prevents Rich markup injection and
keeps terminal control content from becoming presentation instructions.

The wizard continues to build argv directly for `CliRunner`; no shell is
introduced. Required fields, choices, typed numeric fields and destructive
backup confirmation remain governed by the existing CLI. Regression coverage
includes literal markup payloads and the complete headless TUI suite.
