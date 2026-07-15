# BUILD_TICKETS.md

AUTOMATION_STATUS: NOT_DONE

Ticket statuses:

* TODO — not done
* DONE — done

The build loop must select the first TODO ticket in file order.

Every successful ticket below is intentionally sized for one focused conventional commit. Implement one ticket only, run `scripts/quality-gate.sh`, update only that ticket’s status, and create exactly one commit. Do not combine adjacent tickets.

---

## 000 — Bootstrap the Python CLI package

Status: DONE

Create the installable Python package without media-service business logic.

Required:

* add `pyproject.toml` for Python 3.12+, uv, Typer, httpx, Pydantic, pytest, Ruff, and mypy
* add the `src/wit/` package and a `wit` console entry point
* support `wit --help` and `wit --version`
* add one CLI smoke test
* generate and commit `uv.lock`
* extend `.gitignore` only where the Python package requires it

Do not add service clients or Compose services in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused bootstrap commit.

---

## 001 — Adapt CI to the Wit toolchain

Status: DONE

Replace the scaffold-only CI checks with project validation.

Required:

* install the project’s supported Python version and uv in GitHub Actions
* run the repository quality gate
* verify `TEMPLATE_CUSTOMISED: true`
* preserve shell regression tests and secret/private-file guardrails
* use read-only default workflow permissions
* avoid release, deployment, or real-service network operations

Run `scripts/quality-gate.sh` locally.

Commit as one focused CI commit.

---

## 002 — Define the Compose environment and storage contract

Status: DONE

Add the service-neutral Docker Compose foundation.

Required:

* add `compose.yml` with the Wit project name, an internal service network, and no application services yet
* add `.env.example` containing documented placeholders/defaults only
* define one host data root with separate config, downloads, and television-library subpaths
* define timezone, UID, GID, and local port variables without machine-specific values
* document the path contract briefly in `README.md`
* ensure `docker compose config` succeeds without a real `.env`

Do not add Jellyfin, Seerr, Sonarr, or qBittorrent in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused Compose-foundation commit.

---

## 003 — Add the qBittorrent Compose service

Status: DONE

Add qBittorrent as the default download client.

Required:

* add only the qBittorrent service to `compose.yml`
* persist qBittorrent configuration and downloads under the documented data root
* make the Web UI port configurable and bound to localhost by default
* use the configured UID, GID, and timezone
* avoid default credentials in committed files
* add a short setup note describing first-login credential handling

Do not configure indexers, feeds, trackers, or content sources.

Run `scripts/quality-gate.sh`.

Commit as one focused service commit.

---

## 004 — Add the Sonarr Compose service

Status: DONE

Add Sonarr without configuring acquisition sources.

Required:

* add only the Sonarr service to `compose.yml`
* persist Sonarr configuration under the documented data root
* mount downloads and the television library with consistent container paths
* make the administrative port configurable and bound to localhost by default
* use the configured UID, GID, and timezone
* document the expected qBittorrent download-client connection at a high level

Do not add an API key, root-folder mutation script, or indexer configuration.

Run `scripts/quality-gate.sh`.

Commit as one focused service commit.

---

## 005 — Add the Jellyfin Compose service

Status: DONE

Add Jellyfin as the completed-library browser and player.

Required:

* add only the Jellyfin service to `compose.yml`
* persist Jellyfin configuration and cache separately
* mount the television library read-only unless a documented Jellyfin requirement proves otherwise
* make the web port configurable and locally bound by default
* document optional hardware-transcoding devices without enabling machine-specific devices
* do not expose discovery or host-network modes by default

Run `scripts/quality-gate.sh`.

Commit as one focused service commit.

---

## 006 — Add the Seerr Compose service

Status: DONE

Add Seerr as the human discovery and request interface.

Required:

* add only the current Seerr service and persistent configuration mount
* make the web port configurable and locally bound by default
* document that Seerr connects to Sonarr and Jellyfin after their first-run setup
* do not commit login credentials, API keys, or preconfigured users
* do not make the CLI depend on Seerr for episode-level apply operations

Run `scripts/quality-gate.sh`.

Commit as one focused service commit.

---

## 007 — Harden and health-check the Compose stack

Status: DONE

Apply a single coherent operational-hardening pass after all services exist.

Required:

* add health checks where supported without embedding credentials in commands
* add dependency conditions only where they improve deterministic startup
* add conservative restart policies
* add `no-new-privileges` or an equivalent safe setting where compatible
* constrain image versions and document the update policy
* verify administrative ports remain localhost-bound by default
* add a Compose configuration regression check that does not start containers

Do not expose services publicly or add a reverse proxy.

Run `scripts/quality-gate.sh`.

Commit as one focused hardening commit.

---

## 008 — Add a safe host bootstrap script

Status: DONE

Create the non-destructive local setup helper.

Required:

* add a script that creates the documented config, cache, downloads, and television-library directories
* validate UID, GID, and target paths before use
* refuse unsafe roots such as `/` and empty paths
* optionally copy `.env.example` to an untracked `.env` without filling secrets
* set restrictive permissions on the generated `.env`
* add shell regression tests using a temporary directory

The script must not use `sudo`, start containers, delete data, or configure media sources.

Run `scripts/quality-gate.sh`.

Commit as one focused bootstrap-script commit.

---

## 009 — Add secure Wit configuration models

Status: DONE

Implement typed runtime configuration for the CLI.

Required:

* model Sonarr, Jellyfin, Seerr, and TVmaze URLs plus required API credentials
* model Sonarr root-folder and quality-profile defaults needed by apply
* read values from environment or an explicit protected config location, never CLI secret arguments
* validate schemes, timeouts, positive IDs, and local state paths
* redact secret values from representations and validation errors
* add unit tests for valid, missing, malformed, and redacted configuration

Do not make network requests in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused configuration commit.

---

## 010 — Add the shared HTTP transport

Status: DONE

Implement one typed, testable transport used by service clients.

Required:

* support bounded connect/read timeouts and cancellation-safe cleanup
* support query parameters, JSON bodies, and per-service authentication headers
* translate connection, timeout, status, and malformed-JSON failures into redacted Wit errors
* never include authentication values in logs or exception strings
* inject/mock the transport in tests
* cover success and representative failures with unit tests

Do not add service-specific endpoints in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused transport commit.

---

## 011 — Add service health clients

Status: DONE

Implement minimal read-only health/version clients.

Required:

* query Sonarr health/system status using its API-key header
* query Jellyfin system information using its API-key header
* query Seerr’s unauthenticated or configured health endpoint as supported
* return typed, normalised service-health results
* distinguish unavailable, unauthorised, unhealthy, and healthy states
* add mocked contract tests for each service

Do not add title search, requests, queue operations, or mutations.

Run `scripts/quality-gate.sh`.

Commit as one focused client commit.

---

## 012 — Add the `wit doctor` command

Status: DONE

Expose configuration and connectivity diagnostics.

Required:

* validate non-secret configuration before contacting services
* report Sonarr, Jellyfin, and Seerr health independently
* report whether required local data paths exist and have appropriate access
* provide actionable failures without printing credentials
* return a non-zero exit code when required checks fail
* add CLI tests for healthy, partial, and failed diagnostics

Do not mutate services or create host directories.

Run `scripts/quality-gate.sh`.

Commit as one focused command commit.

---

## 013 — Add the TVmaze metadata client

Status: DONE

Implement read-only show and episode metadata retrieval for planning.

Required:

* search shows by user-supplied title
* fetch regular and special episodes distinctly
* expose air date/time and TVDB/IMDb external IDs when available
* use the shared transport and typed response models
* handle missing external IDs and incomplete air-date data explicitly
* add mocked tests for search, episodes, empty results, and malformed responses

Do not call Sonarr or write plans in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused metadata-client commit.

---

## 014 — Add deterministic show matching

Status: DONE

Implement title-match ranking without terminal or network dependencies.

Required:

* normalise harmless case, punctuation, and whitespace differences
* prefer exact title and known alias matches
* use year only as a deterministic disambiguator, not a silent guess
* return a single match only when confidence rules are satisfied
* otherwise return ordered candidates and require user selection
* add tests for exact, alias, remake/year, ambiguous, and no-match cases

Do not add interactive prompting or service mutations.

Run `scripts/quality-gate.sh`.

Commit as one focused matching commit.

---

## 015 — Add Sonarr library-default and lookup operations

Status: TODO

Implement the read-only Sonarr operations needed before adding a series.

Required:

* list root folders and quality profiles
* find an existing series by TVDB ID
* perform Sonarr series lookup by TVDB ID for a not-yet-added show
* validate configured root-folder and quality-profile selections against Sonarr
* return typed models with only fields required by Wit
* add mocked tests for found, missing, and invalid-default cases

Do not add a series in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused Sonarr-client commit.

---

## 016 — Add the unmonitored Sonarr series operation

Status: TODO

Implement the narrowly scoped series-add mutation used during apply.

Required:

* add a resolved TV series with configured root folder and quality profile
* add it with series, seasons, and episodes unmonitored and without automatic search
* reject a plan lacking a stable TVDB identity
* handle an already-existing series as an idempotent result
* add mocked request/response tests, including payload assertions

Do not monitor episodes or trigger searches in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused mutation-client commit.

---

## 017 — Add Sonarr episode listing and coordinate mapping

Status: TODO

Implement read-only episode retrieval and stable plan mapping.

Required:

* list episodes for a Sonarr series
* retain episode ID, season number, episode number, title, air status, monitored state, and file state
* map a planned `(season, episode)` coordinate to exactly one Sonarr episode ID
* fail safely on missing or duplicate coordinates
* add tests for normal, missing, duplicate, special, and unaired records

Do not alter monitoring state.

Run `scripts/quality-gate.sh`.

Commit as one focused episode-client commit.

---

## 018 — Add episode-selection rules

Status: TODO

Implement pure domain logic for selecting planned episodes.

Required:

* support first `N` aired regular episodes across the series
* support first `N` aired regular episodes within a specified season
* support an inclusive episode range within a specified season
* support all currently aired regular episodes
* exclude season zero, specials, future, and undated episodes by default
* use a supplied clock so tests are deterministic
* reject zero/negative counts, reversed ranges, empty selections, and conflicting selectors
* add thorough boundary tests

Do not perform I/O in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused domain-logic commit.

---

## 019 — Add the versioned download-plan model

Status: TODO

Define the immutable, secret-free plan exchanged between planning and apply.

Required:

* include schema version, plan ID, creation time, show title/year, TVmaze ID, TVDB ID, selector summary, and selected episode coordinates/titles
* include no API keys, cookies, service headers, absolute media paths, or raw service responses
* provide deterministic human-readable rendering with episode count
* provide JSON serialisation and strict deserialisation
* add tests for round trips, invalid schemas, and accidental secret-field rejection

Do not persist plans in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused model commit.

---

## 020 — Add the XDG plan store

Status: TODO

Persist and retrieve plans safely.

Required:

* store plans under `${XDG_STATE_HOME:-~/.local/state}/wit/plans/`
* create directories and files with restrictive permissions
* use atomic replacement to avoid partial plans
* reject unsafe plan IDs, symlinks, traversal, corrupt JSON, and unsupported schema versions
* support listing and loading plans without exposing unrelated files
* add temporary-directory tests for permissions and failure cases

Do not add CLI commands in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused persistence commit.

---

## 021 — Add the read-only `wit plan` command

Status: TODO

Wire metadata search, matching, selection, rendering, and persistence into a read-only command.

Required:

* accept a show query and exactly one supported selector
* support explicit candidate selection when a title is ambiguous
* require a TVDB identity needed for later Sonarr mapping
* print the matched show and every selected episode before saving
* save the plan and print its plan ID
* guarantee that the command makes no Sonarr mutation
* add CLI tests with mocked metadata and fixed time

Do not apply or search downloads in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused command commit.

---

## 022 — Add Sonarr episode-monitoring operations

Status: TODO

Implement the monitoring mutation independently from orchestration.

Required:

* mark only an explicit list of Sonarr episode IDs as monitored
* avoid broad season or series monitoring calls
* validate that the ID list is non-empty and unique
* parse and verify Sonarr’s response
* add mocked payload and failure tests

Do not trigger episode searches in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused mutation-client commit.

---

## 023 — Add targeted Sonarr episode-search operations

Status: TODO

Implement targeted search command submission and status parsing.

Required:

* submit `EpisodeSearch` only for an explicit, non-empty, unique episode-ID list
* return the Sonarr command ID and initial state
* support read-only command-status polling with a bounded policy
* treat rejection, timeout, and failed commands as explicit errors
* add mocked payload, polling, timeout, and failure tests

Do not orchestrate complete apply behaviour in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused Sonarr-command commit.

---

## 024 — Add Sonarr queue inspection

Status: TODO

Implement the read-only queue data needed for safety and status.

Required:

* retrieve paginated queue records without silently truncating results
* map queue items to series and episode IDs where Sonarr provides them
* normalise queued, downloading, completed/importing, warning, and failed states
* expose enough data to identify an already-queued planned episode
* add tests for pagination, mixed states, missing episode IDs, and API failures

Do not cancel or remove queue items.

Run `scripts/quality-gate.sh`.

Commit as one focused queue-client commit.

---

## 025 — Add `wit apply` orchestration

Status: TODO

Apply one stored plan through Sonarr with explicit confirmation.

Required:

* load and validate the stored plan
* require interactive confirmation or `--yes` for non-interactive use
* find or add the series unmonitored
* re-fetch Sonarr episodes and map every planned coordinate before any monitoring mutation
* abort without partial mutation if any coordinate cannot be mapped
* monitor the mapped episodes and submit one targeted search
* print a concise result including the Sonarr command ID
* add orchestration tests for existing and newly added series

Do not add idempotent skip behaviour beyond existing-series handling; that is the next ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused orchestration commit.

---

## 026 — Make apply idempotent and stale-safe

Status: TODO

Add safeguards around repeat or outdated plans.

Required:

* skip episodes that already have files
* skip episodes already present in Sonarr’s active queue
* avoid resubmitting a search when no actionable episodes remain
* reject plans older than a documented default unless explicitly overridden
* re-confirm when current Sonarr titles or coordinates materially disagree with the stored plan
* report applied, skipped-file, skipped-queue, and rejected counts separately
* add tests for repeat apply, mixed states, stale override, and no-op apply

Do not add queue cancellation or media deletion.

Run `scripts/quality-gate.sh`.

Commit as one focused safety commit.

---

## 027 — Add Sonarr-backed request status

Status: TODO

Create the application service that reports progress for a stored plan.

Required:

* map plan coordinates to current Sonarr episodes
* combine file state, monitoring state, queue state, and command state where available
* classify each episode as planned, queued, downloading, imported, missing, warning, or failed
* preserve per-episode error details without exposing credentials or raw payloads
* add deterministic service tests for mixed plans

Do not query Jellyfin or add CLI rendering in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused status-service commit.

---

## 028 — Add Jellyfin library availability lookup

Status: TODO

Implement read-only confirmation that imported episodes are visible to viewers.

Required:

* find a series by stable external ID where Jellyfin supports it, with a documented title/year fallback
* list matching season and episode numbers in the Jellyfin library
* distinguish absent series, absent episode, and unavailable Jellyfin
* keep lookup read-only and bounded
* add mocked tests for external-ID match, fallback match, missing data, and duplicate candidates

Do not trigger a library scan in this ticket.

Run `scripts/quality-gate.sh`.

Commit as one focused Jellyfin-client commit.

---

## 029 — Add the `wit status` command

Status: TODO

Expose combined plan progress for operators and Pi.

Required:

* accept a plan ID
* show Sonarr acquisition/import state for every planned episode
* show Jellyfin visibility for episodes Sonarr reports as imported
* tolerate Jellyfin being unavailable while clearly reporting degraded status
* return a non-zero exit code only for operational failure, not merely incomplete downloads
* add CLI tests for active, complete, degraded, and failed plans

Do not mutate Sonarr or Jellyfin.

Run `scripts/quality-gate.sh`.

Commit as one focused command commit.

---

## 030 — Add stable machine-readable output

Status: TODO

Make the CLI reliable for Pi and other automation without changing domain behaviour.

Required:

* add a consistent `--json` mode to `doctor`, `plan`, `apply`, and `status`
* define versioned output envelopes with command, success state, data, warnings, and errors
* keep human-readable output as the default
* ensure JSON mode writes no progress or decoration to standard output
* document exit-code semantics
* add snapshot or structural tests for each command’s JSON output

Do not add new service operations.

Run `scripts/quality-gate.sh`.

Commit as one focused output-contract commit.

---

## 031 — Add end-to-end planning contract tests

Status: TODO

Test the complete read-only planning path with deterministic fake HTTP services.

Required:

* cover exact title, ambiguous title with explicit candidate, first-N, season range, and all-aired flows
* prove specials and future episodes remain excluded by default
* prove no Sonarr mutation endpoint is called during planning
* verify persisted plans contain no credentials or raw authentication headers
* run entirely offline in CI

Do not change planning features except for defects exposed by these tests.

Run `scripts/quality-gate.sh`.

Commit as one focused integration-test commit.

---

## 032 — Add end-to-end apply and status contract tests

Status: TODO

Test the mutating path against deterministic fake Sonarr and Jellyfin services.

Required:

* cover add-unmonitored, coordinate mapping, monitor, targeted search, queue, import, and Jellyfin visibility
* cover repeat apply and mixed downloaded/queued/missing episodes
* assert exact mutation order and fail-before-mutation behaviour on mapping errors
* assert credentials never appear in captured CLI output or plan state
* run entirely offline in CI

Do not add new apply or status features except for defects exposed by these tests.

Run `scripts/quality-gate.sh`.

Commit as one focused integration-test commit.

---

## 033 — Add runtime Pi instructions to `AGENTS.md`

Status: TODO

Complete the non-build-loop operating instructions after the CLI contract is stable.

Required:

* tell Pi to use `wit` commands instead of raw API calls or direct database edits
* document plan-before-apply behaviour and what counts as explicit confirmation
* define the default meanings of first-N and all-aired requests
* explain when to use `doctor`, `plan`, `apply`, and `status`
* prohibit reading or printing secret configuration
* explain that Seerr is the human browser, Sonarr owns acquisition, and Jellyfin owns playback
* keep all autonomous-build rules intact

Run `scripts/quality-gate.sh`.

Commit as one focused agent-instructions commit.

---

## 034 — Complete installation and configuration documentation

Status: TODO

Turn `README.md` and configuration documentation into an accurate first-run guide.

Required:

* document prerequisites and the host bootstrap flow
* document Compose startup and first-run setup order for qBittorrent, Sonarr, Jellyfin, and Seerr
* document how to configure authorised sources manually without recommending specific unauthorised sources
* document all non-secret Wit environment settings and where secrets belong
* document common `doctor`, `plan`, `apply`, and `status` examples
* clearly label implemented limitations and local-network assumptions

Run `scripts/quality-gate.sh`.

Commit as one focused documentation commit.

---

## 035 — Complete architecture, operations, security, and troubleshooting docs

Status: TODO

Add the maintainer/operator reference documentation.

Required:

* add `docs/architecture.md` with component and data-flow boundaries
* add `docs/operations.md` with startup, shutdown, backup, restore, update, and recovery procedures
* add `docs/security.md` with threat model, secret handling, network exposure, state-file safety, and responsible-use boundaries
* add `docs/troubleshooting.md` for permissions, API authentication, paths, unhealthy services, stalled queues, and missing Jellyfin items
* remove or rewrite template-only customisation text that no longer applies to Wit
* cross-link the documents from `README.md`

Do not add deployment automation or destructive recovery commands.

Run `scripts/quality-gate.sh`.

Commit as one focused documentation-set commit.

---

## 099 — Final autonomous review and completion marker

Status: TODO

Perform a final repository review without adding new product scope.

Check:

* every project-brief success criterion is implemented or honestly documented as a limitation
* all earlier tickets are complete
* CLI help, examples, docs, and implementation agree
* tests, linting, formatting, type checks, build, Compose validation, shell regressions, and safety scans pass
* CI is green
* no secrets, private data, machine-specific paths, generated runtime state, or real media metadata are committed
* default service exposure remains local-first
* no source/indexer automation, DRM bypass, or destructive media operations were introduced
* the repository is clear and safe for public use

Run the full `scripts/quality-gate.sh`.

If everything is complete, set the top-level status to:

AUTOMATION_STATUS: DONE

Commit the final review as one focused commit.
