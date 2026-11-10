# Bridge HTTP hardening

Bridge error bodies are read with the same 1 MiB bounded policy as success
responses. Invalid or missing `Content-Length`, invalid UTF-8, non-object JSON,
HTML, and secret-bearing messages fall back to a bounded redacted detail.

Governance, authentication, provider selection, receipts, and workspace
ownership remain Bridge-owned.
