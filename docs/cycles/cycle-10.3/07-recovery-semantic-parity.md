# Recovery semantic parity

S16 now compares the backup database with the restored database by table
schema and complete row contents, checks SQLite integrity and foreign keys,
and compares every blob filename and SHA-256. Only append-only operational
audit rows are excluded from data parity, because backup/restore journals are
legitimate history changes.

Final status: PASS in the Windows run `20261111T003852Z-2bba1df4` and the
Linux federated run `20261111T004728Z-e2be3cce`: 23 tables were checked for
schema/row parity, SQLite integrity and foreign keys passed, and the blob
inventory/hash set matched (0 blobs in this fixture).
