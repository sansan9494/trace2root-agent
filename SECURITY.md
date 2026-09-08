# Security Policy

## Supported scope

This repository is a local demonstration and evaluation project. The bundled
Prometheus, log, alert, notification, and restart actions are mocks and must not
be pointed at production systems without a separate security review.

## Reporting a vulnerability

Please avoid opening a public issue containing credentials, private logs, or
reproduction data from a real environment. Report the issue privately through
GitHub's security advisory feature after the repository owner enables it.

## Operational boundaries

- Keep `.env`, uploaded documents, local SQLite/Milvus data, logs, model caches,
  and evaluation checkpoints out of version control.
- Treat retrieved documents and tool output as untrusted input.
- Do not remove the L3 approval gate or enable L4 operations in a real system.
- Replace the mock allowlist, authentication, authorization, webhook validation,
  and secret storage before any production integration.
