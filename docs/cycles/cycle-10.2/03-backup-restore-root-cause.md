# Backup/Restore Root Cause

The required reproduction showed that the database backup was valid and the
restore implementation correctly refused to overwrite a live database. The
defect was in the laboratory: `s16_backup_restore` always used
`RUN / "ws-restored"`. A previous run left a database there, so a later run
was compared against stale services, sources and incidents. The apparent
restore failure was therefore a harness isolation failure, not a corrupt
backup.

The fix uses `tempfile.mkdtemp(prefix="ws-restored-", dir=RUN)` for every run.
It does not pass `--force`, does not delete user workspaces and leaves the
restore refusal semantics unchanged. The focused reproduction now reports:

```text
S16 PASS LOCAL_REAL — integrity ok + parity
verdict: PASS (1/1 pass, 0 blocked)
```

The backup implementation continues to verify manifest checksums before any
write, run SQLite integrity/foreign-key checks after restore and move live
files aside only for an explicitly authorized `--force` operation.
