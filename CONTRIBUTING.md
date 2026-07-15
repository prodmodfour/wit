# Contributing to Wit

Thank you for considering a contribution to Wit.

Wit is being built through a small, ordered, ticket-driven workflow. Contributions should preserve its local-first safety model and clear boundaries between the CLI, Sonarr, Jellyfin, Seerr, and the download client.

## Before contributing

Read:

* [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md)
* [`BUILD_TICKETS.md`](BUILD_TICKETS.md)
* [`AGENTS.md`](AGENTS.md)
* [`SECURITY.md`](SECURITY.md)

For changes not represented by an existing ticket, open an issue before implementing substantial work. Keep each pull request and commit focused on one independently reviewable change.

## Safety boundaries

Contributions must not include:

* real credentials, API keys, cookies, private URLs, or media-library data
* indexer, tracker, provider, or debrid presets
* DRM bypasses or subscription-service download mechanisms
* public-by-default administrative interfaces
* destructive media deletion
* arbitrary shell execution from user input
* CI that contacts real media services

Use only synthetic metadata and mocked HTTP responses in tests.

## Local validation

Run:

```bash
scripts/quality-gate.sh
```

The gate checks shell syntax and regression tests, secret/private-file guardrails, Ruff formatting and linting, mypy, pytest, the package environment, and Docker Compose configuration without starting services.

## Commit and pull-request style

Use a conventional commit prefix such as `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `build:`, or `ci:`.

A good change:

* has one clear purpose
* includes focused tests
* updates relevant documentation
* preserves plan-before-apply behaviour
* leaves no generated, private, or machine-specific files
* passes the complete quality gate

## Reporting problems

Open a GitHub issue for ordinary bugs or proposals. For suspected vulnerabilities, follow [`SECURITY.md`](SECURITY.md) and do not disclose secrets or exploit details publicly.
