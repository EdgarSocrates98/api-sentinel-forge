# Bridge Security

The HTTP adapter now validates loopback resolution, rejects URL credentials,
queries and fragments, disables ambient proxies and refuses redirects. It
requires an explicitly selected provider instead of silently using `fake`,
bounds JSON responses to 1 MiB, requires a JSON object and validates returned
session, workspace and provider identities. Timeout and missing-provider cases
remain named non-success states.

Provider execution remains Bridge-owned. A `201` response is still represented
as `delegated`, not as provider execution or completion.
