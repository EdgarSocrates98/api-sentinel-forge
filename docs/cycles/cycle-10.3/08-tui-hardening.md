# TUI hardening

Worker command execution now schedules all RichLog writes through Textual's
app thread. Execution argv remains intact, while display argv masks fields
marked sensitive and common credential flags. Untrusted output is rendered as
plain Rich `Text` and passes the shared redaction policy.

Regression coverage includes sensitive argv and concurrent command output.
