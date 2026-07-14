# PROJECT_BRIEF.md

TEMPLATE_CUSTOMISED: true

## Project name

Wit (command name: `wit`)

## Project type

A self-hosted media-operations CLI plus a Docker Compose application stack.

## Project goal

Build a safe, local-first way for a user or Pi Coding Agent to request and manage an organised television library using natural language without depending on Stremio.

Wit must provide:

* a small deterministic CLI that Pi can invoke instead of constructing raw API calls
* a read-only planning step for requests such as “the first four episodes of X” or “all aired episodes of Y”
* an explicit apply step that uses Sonarr for episode acquisition and queue management
* Jellyfin as the browser and player for completed local media
* Seerr as the human-facing discovery and request interface
* qBittorrent as the default Compose download client, while keeping the CLI coupled to Sonarr rather than directly to qBittorrent
* Docker Compose configuration and operator documentation for running the stack locally

Wit is an orchestration and library-management project. It must not supply content sources, bypass DRM, or configure unauthorised indexers.

## Audience

* people operating a personal, lawful, self-hosted television library
* Pi Coding Agent users who want a reliable natural-language control surface
* maintainers and open-source contributors

## Success criteria

The project is successful when:

* `docker compose config` validates a documented Jellyfin, Seerr, Sonarr, and qBittorrent stack
* services use explicit persistent paths and safe local-first network defaults
* `wit doctor` reports configuration and service connectivity without exposing credentials
* `wit plan` resolves a show and displays the exact aired, non-special episodes selected by `first`, `season/range`, or `all-aired` rules without mutating Sonarr
* plans are stored without secrets under the XDG state directory and can be inspected before execution
* `wit apply` safely adds or finds the series in Sonarr, maps the planned episodes, monitors only the intended episodes, and starts targeted searches
* repeated apply operations are idempotent and do not unnecessarily search episodes already downloaded or queued
* `wit status` reports Sonarr queue state and whether completed episodes are visible in Jellyfin
* Pi can operate Wit from this repository by following `AGENTS.md` and calling the CLI rather than raw service APIs
* tests, linting, formatting, type checks, Compose validation, safety scans, and CI pass
* setup, architecture, operations, security, limitations, and troubleshooting are honestly documented

## Non-goals

The agent must not spend time on:

* Stremio integration or migration tooling
* building replacement Jellyfin, Seerr, or Sonarr web interfaces
* movie automation; the first release is television-series only
* automatically adding indexers, trackers, Usenet providers, debrid services, or other content sources
* DRM circumvention, stream ripping, credential extraction, or scraping subscription services
* automatically exposing services to the public internet
* transcoding implementation beyond Jellyfin’s existing capabilities
* mobile or desktop native applications
* multi-user tenancy or hosted SaaS operation
* autonomous deletion of media or destructive library cleanup

## Technology preferences

Preferred stack:

* language: Python 3.12 or newer
* CLI framework: Typer
* HTTP client: httpx
* validation/settings: Pydantic and pydantic-settings
* metadata provider for read-only planning: TVmaze’s public API, mapped to Sonarr through external IDs
* state: versioned JSON plan files under `${XDG_STATE_HOME:-~/.local/state}/wit/`
* infrastructure: Docker Compose
* media services: Jellyfin, Seerr, Sonarr, and qBittorrent
* testing: pytest with mocked HTTP transport
* linting/formatting: Ruff
* type checking: mypy
* package manager: uv
* CI: GitHub Actions

Hard constraints:

* every successful build ticket produces exactly one focused conventional commit
* API keys and credentials must never be passed as CLI arguments, printed, committed, or stored in plan files
* all mutating media operations require a previously generated plan
* non-interactive apply requires an explicit confirmation flag
* ambiguous title matches must fail safely and present candidates rather than guessing
* “first N” means the first N aired regular episodes ordered by season and episode number
* “all aired” excludes specials, unaired episodes, and future episodes by default
* direct service API calls are implementation details; operator-facing and Pi-facing workflows use `wit`
* service images and dependencies must be version constrained and documented
* default networking must expose administrative services only where needed and must not assume public internet exposure
* the repository must contain examples only, never a real `.env`, API key, media path, private hostname, or private data

Flexible choices:

* exact module boundaries may evolve while preserving the architecture below
* a small dependency may be substituted when it materially improves safety or testability
* the download client may be external instead of the bundled qBittorrent service when documented
* JSON output may be added alongside human-readable output for reliable Pi automation

## Architecture expectations

```text
Pi / operator
    |
    | invokes only
    v
wit CLI -> planning service -> TVmaze (read-only metadata)
    |
    +-> plan store (secret-free XDG state)
    |
    +-> apply service -> Sonarr API -> configured download client -> downloads
    |                                      |
    |                                      v
    |                                TV media library
    |                                      |
    +-> status service -> Sonarr API        v
    +-> status service -> Jellyfin API <- Jellyfin browser/player

Human discovery browser: Seerr -> Sonarr
Default download client: Sonarr -> qBittorrent
```

Expected Python boundaries:

```text
cli -> application services -> typed service clients -> HTTP transport
                 |                    |
                 v                    v
             plan store          response models
```

Rules:

* domain selection and planning logic must not depend on terminal rendering
* HTTP clients must not own orchestration decisions
* plan files contain stable show identity and season/episode coordinates, not credentials
* apply must revalidate a plan against current Sonarr data before mutation
* Seerr is primarily the human discovery UI; Wit only needs enough integration to diagnose availability and document its role
* Sonarr owns acquisition and download-client interaction
* Jellyfin owns playback and the completed-media catalogue

## Quality expectations

Expected quality gates:

* unit tests for configuration, matching, selection, planning, persistence, and apply safety
* mocked integration tests for Sonarr, Jellyfin, Seerr, and TVmaze HTTP contracts
* Ruff linting and formatting checks
* mypy type checks
* package build validation
* Docker Compose validation
* shell-script syntax and regression tests inherited from the autonomous-build template
* secret and generated/private-file scans
* GitHub Actions CI

## Documentation expectations

Required docs:

* `README.md` with status, installation, setup, and common workflows
* `docs/architecture.md`
* `docs/configuration.md`
* `docs/operations.md`
* `docs/security.md`
* `docs/troubleshooting.md`
* clear explanation of Jellyfin, Seerr, Sonarr, and qBittorrent responsibilities
* clear explanation of planning, confirmation, selection defaults, and limitations
* autonomous-build instructions retained for maintainers

## Safety and security constraints

Do not include:

* real secrets, tokens, cookies, credentials, or private data
* hard-coded API keys or credentials in Compose, scripts, tests, examples, logs, or docs
* automatic indexer/provider configuration
* instructions tailored to unauthorised content acquisition
* DRM bypasses or subscription-service download mechanisms
* public-by-default administrative interfaces
* arbitrary shell execution from user-provided values
* unsafe path interpolation or path traversal
* destructive media deletion
* credential values in exception messages, debug output, process arguments, or persisted plans

The implementation must:

* use structured subprocess-free HTTP integrations where possible
* validate URLs, IDs, counts, seasons, episode ranges, and filesystem paths
* use bounded HTTP timeouts and actionable redacted errors
* write state atomically with restrictive permissions
* support dry/read-only planning before mutation
* document that operators are responsible for using authorised sources

## Agent behaviour notes

* Read `AGENTS.md`, this brief, and `BUILD_TICKETS.md` before implementation.
* Implement only the first TODO ticket and create exactly one commit for a successful ticket.
* Treat each ticket as a hard scope boundary; do not combine adjacent tickets.
* Preserve the distinction between a read-only plan and a mutating apply.
* Prefer deterministic tests and mocked APIs; CI must not contact real media services.
* Do not put local machine values into committed files while testing Compose or the CLI.
* Do not run or configure real downloads during autonomous development.
* Keep public claims proportional to implemented and tested behaviour.
