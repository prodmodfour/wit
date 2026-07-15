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

When Pi operates an already configured Wit installation, `wit` is the only supported control surface. Use `wit` commands instead of raw `curl`, direct service API calls, direct database edits, direct qBittorrent control, or ad hoc Docker commands. Prefer `--json` when Pi needs machine-readable output, and do not infer service state by inspecting media or state files.

Pi must not inspect, read back, print, log, or ask the user to paste secret configuration. Do not open a real `.env` or protected Wit configuration file, dump relevant environment variables, query credentials from a service, or put credentials on a command line. Let `wit` load its configured secrets. If configuration or connectivity may be wrong, use the redacted output from `wit doctor` and ask the operator to repair configuration without sharing secret values.

### Command workflow

Use the commands in this order as the request requires:

1. Run `wit doctor` when validating initial readiness or diagnosing configuration, local-state access, authentication, or service connectivity. It is read-only; a failed check must be resolved by the operator before relying on affected operations.
2. Run `wit plan` for every new episode request. Planning uses read-only TVmaze metadata, prints the matched show and every selected episode, and saves a secret-free plan. Show the complete result and plan ID to the user. If Wit returns candidates, ask the user to choose one and rerun with `--candidate <TVMAZE-ID>`; never guess or apply an ambiguous match.
3. Wait for explicit confirmation of that displayed plan before running `wit apply <plan-id>`. The initial request to download a show, the act of creating a plan, silence, or a general standing approval is not confirmation. Confirmation must be an affirmative response given after review and tied to the displayed plan, such as accepting the interactive default-no prompt or telling Pi to apply that plan ID. For non-interactive execution, pass `--yes` only after receiving that confirmation.
4. Treat `--allow-stale` and `--allow-mismatch` as separate confirmations, not routine flags. Show the age warning or current Sonarr metadata differences and obtain explicit user approval before using the relevant override.
5. Run `wit status <plan-id>` after apply and whenever the user asks about progress or completion. Trust its Sonarr acquisition/import state and Jellyfin visibility result rather than inspecting files, queues, or databases manually. Incomplete work is not by itself an operational failure.

A plan is always required before apply. Applying may add the series to Sonarr unmonitored, monitor only mapped planned episodes, and submit a targeted search; planning and status never perform these mutations. Do not delete media, cancel queue items, trigger library scans, or perform any other undocumented mutation.

### Episode-request meanings

Translate natural-language episode requests deterministically:

* “first N” means `--first N`: the first N currently aired regular episodes across the series, ordered by season number and then episode number.
* “first N from season S” means `--season S --first N`, using the same aired regular-episode rules within that season.
* an explicit inclusive season range means `--season S --episodes START-END`.
* “all aired” means `--all-aired`: all currently aired regular episodes across the series.

These defaults exclude season zero, specials, future or otherwise unaired episodes, and episodes without an air date. Do not silently broaden a selector or substitute a different show.

### Service responsibilities

* Seerr is the human discovery and request browser; Pi does not use it for episode-level apply operations.
* Sonarr owns series, episode monitoring, targeted search, queue, download-client, and import operations. Wit talks to Sonarr rather than controlling qBittorrent directly.
* Jellyfin owns the completed-media catalogue and playback. Use `wit status` to check whether Sonarr-imported episodes are visible there; playback itself happens in Jellyfin.

Documentation rules

Update docs only when required by the selected ticket or when that ticket changes behaviour, setup, architecture, operations, security, limitations, or public-facing usage.

Prefer clear, honest limitations over pretending the project is production-ready.

## Testing and validation

Use the project’s quality gate:

```bash
scripts/quality-gate.sh
```

Tests must be deterministic and offline. Use mocked/fake service responses; never require real Jellyfin, Seerr, Sonarr, qBittorrent, TVmaze, media files, or API credentials in CI.

If project-specific validation is missing, improve it only when the selected ticket requires that change.

