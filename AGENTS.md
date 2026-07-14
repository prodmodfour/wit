# AGENTS.md

You are working in Wit, an autonomous, ticket-driven build and a future local media-operations repository.

This file contains general rules. Project-specific requirements live in `PROJECT_BRIEF.md`.

## Required reading

Before making changes, read completely:

* `AGENTS.md`
* `PROJECT_BRIEF.md`
* `BUILD_TICKETS.md`

## Core build workflow

When invoked by the build loop:

1. Select the first `TODO` ticket in `BUILD_TICKETS.md` file order.
2. Say what you are working on now, including the selected ticket and immediate action.
3. Implement only that ticket.
4. Do not start future tickets.
5. Do not broaden scope.
6. Add or update tests/validation required by that ticket.
7. Add or update docs required by that ticket.
8. Run `scripts/quality-gate.sh`.
9. Update only the selected ticket status in `BUILD_TICKETS.md`.
10. Commit the completed ticket with a conventional commit message.
11. Leave the working tree clean.

A successful ticket must produce exactly one focused commit. Do not combine multiple tickets in one commit and do not split unrelated work into the selected ticket.

Do not add cycle notes, validation summaries, blocker notes, or other commentary to `BUILD_TICKETS.md`. It should contain ticket descriptions plus status only.

The outer build loop handles pushing and optional PR/MR creation or merging when configured. Do not create or merge PRs/MRs from inside the agent run unless a ticket explicitly asks for it.

## If blocked

If you cannot complete the ticket safely:

* print the blocker in the agent response
* leave the ticket status as not done
* do not add blocker notes to `BUILD_TICKETS.md`
* do not mark it `DONE`
* do not commit broken partial work
* leave the working tree clean if possible

## Scope control

Do not:

* start future tickets
* silently change project goals
* rewrite unrelated code
* add unnecessary dependencies
* add speculative features
* remove safety checks
* bypass quality gates
* commit generated/private files unless explicitly required
* run or configure real media downloads during development
* add indexers, trackers, provider presets, debrid integrations, or DRM workarounds

## Wit architecture boundaries

Maintain these responsibilities:

* Wit is the deterministic CLI used by Pi and operators.
* Sonarr owns series, episode, search, queue, download-client, and import operations.
* Jellyfin owns the completed-media catalogue and playback.
* Seerr is primarily the human discovery and request browser.
* qBittorrent is the default Compose download client and is controlled by Sonarr, not directly by normal Wit commands.
* TVmaze supplies read-only metadata used to make planning non-mutating.

Preserve a strict plan/apply boundary:

* planning is read-only
* plans contain no secrets
* apply requires a stored plan and explicit confirmation
* apply maps every episode before making monitoring changes
* ambiguous or inconsistent data fails safely

## Runtime/operator requests

The full runtime command contract will be added by its dedicated ticket. Until those commands exist, do not pretend that Wit can perform media operations.

Once implemented, when a user asks Pi to plan, download, or inspect shows from this repository:

* use the `wit` CLI rather than raw `curl`, direct API calls, database edits, or ad hoc Docker commands
* never read, print, or pass API credentials on a command line
* generate and show a plan before applying it
* do not apply an ambiguous plan
* use `wit status` rather than inferring completion from files manually
* do not delete media or cancel queues unless a future documented command explicitly supports it

## Safety rules

Never commit:

* real secrets
* credentials
* access tokens
* private keys
* real `.env` files
* private data
* internal hostnames
* internal URLs
* employer/client data
* media-library records
* download history
* Terraform state
* generated cloud plans
* machine-specific configuration

Do not add destructive automation unless the project brief explicitly allows it and the ticket specifically asks for it.

Do not implement arbitrary code execution, shell execution, unsafe command execution, path traversal, or user-controlled subprocess construction.

Do not expose administrative services publicly by default. Do not add source/indexer automation, DRM circumvention, credential extraction, or subscription-stream ripping.

## Documentation rules

Update docs only when required by the selected ticket or when that ticket changes behaviour, setup, architecture, operations, security, limitations, or public-facing usage.

Prefer clear, honest limitations over pretending the project is production-ready.

## Testing and validation

Use the project’s quality gate:

```bash
scripts/quality-gate.sh
```

Tests must be deterministic and offline. Use mocked/fake service responses; never require real Jellyfin, Seerr, Sonarr, qBittorrent, TVmaze, media files, or API credentials in CI.

If project-specific validation is missing, improve it only when the selected ticket requires that change.

## Commit style

Use conventional commits:

```text
chore:
feat:
fix:
test:
docs:
refactor:
ci:
build:
```

Examples:

```text
chore: bootstrap wit cli package
feat: add aired episode selection
feat: add sonarr episode search
fix: redact service authentication failures
test: cover repeat plan application
docs: document local stack recovery
ci: validate wit with uv
```

## Completion

Wit is complete only when:

* the final ticket is done
* all quality gates pass
* docs match implementation
* safety constraints are respected
* default service exposure remains local-first
* the top-level `AUTOMATION_STATUS` in `BUILD_TICKETS.md` is set to `DONE`
