# Wit

Wit is a local-first, self-hosted television-library project designed to be operated by people or by [Pi Coding Agent](https://pi.dev) through a small deterministic CLI.

The intended stack is:

```text
Pi / operator -> wit CLI -> Sonarr -> download client -> TV library -> Jellyfin
                                  ^                                
                                  |
                            Seerr browser
```

- **Jellyfin** browses and plays completed media.
- **Seerr** is the human-facing discovery and request interface.
- **Sonarr** owns series, episode, queue, and import management.
- **qBittorrent** is the default Compose download client.
- **Wit** plans and applies precise episode requests without making Pi construct raw API calls.

## Status

Wit is a pre-1.0 project. Its core Compose stack and the complete `doctor` → `plan` → `apply` → `status` CLI workflow are implemented and covered by offline tests, but installation and service configuration are still manual. Wit intentionally does not configure acquisition sources, public access, or destructive media operations. Review [Implemented limitations and local-network assumptions](#implemented-limitations-and-local-network-assumptions) before relying on it.

The implementation queue is defined in [`BUILD_TICKETS.md`](BUILD_TICKETS.md), and the required outcome and safety boundaries are defined in [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md). Each successful build ticket is deliberately sized for one focused conventional commit.

## Documentation

- [Configuration reference](docs/configuration.md) — CLI settings, Compose variables, protected files, and credentials
- [Architecture](docs/architecture.md) — component authority, command data flows, storage, networking, and failure boundaries
- [Operations](docs/operations.md) — startup, shutdown, backup, restore, updates, and non-destructive recovery
- [Security model](docs/security.md) — threat model, secrets, network exposure, state safety, and responsible use
- [Troubleshooting](docs/troubleshooting.md) — permissions, authentication, paths, health, queues, and Jellyfin visibility
- [Security reporting policy](SECURITY.md) and [contribution guide](CONTRIBUTING.md)

Start with the [first-run guide](#first-run-guide), then use the references above for maintenance. The service and CLI operations in these documents preserve the boundaries summarized below; they do not provide acquisition sources or public deployment automation.

## Prerequisites

Run Wit on a host that provides:

- a local Linux-compatible environment with Bash and GNU `realpath` (the host bootstrap helper uses `realpath -m`)
- Docker Engine with the Docker Compose v2 plugin and permission to run `docker compose`
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- a Git checkout or source archive of this repository
- enough local storage for service configuration, downloads, and the television library
- a browser on the Docker host, or an independently secured way to reach its loopback-only web interfaces
- outbound DNS/HTTPS for image pulls and TVmaze metadata, plus only those media sources the operator is authorised to use

The default host ports `8080`, `8989`, `8096`, and `5055` must be free unless changed in the local `.env`. Check the required tools before continuing:

```bash
docker --version
docker compose version
python3 --version
uv --version
```

All repository commands below are run from the repository root. The Compose stack uses Linux container paths and UID/GID-aware bind mounts; Docker Desktop and non-Linux hosts are not currently documented or tested by this project.

## Install the CLI

Install the locked project environment and verify the source checkout:

```bash
uv sync --locked
uv run wit --version
uv run wit --help
```

The examples below use `wit` for readability. Either prefix each command with `uv run` while working from the checkout, or install the command into an isolated uv tool environment:

```bash
uv tool install .
wit --version
```

No published package or binary installer is provided yet; the repository checkout is the supported installation source.

## First-run guide

### 1. Prepare Compose settings and host storage

The safe default uses the ignored repository-local `./data` directory and container UID/GID `1000`. Create every required bind-mount directory and copy the non-secret Compose template to an ignored `.env`:

```bash
scripts/bootstrap-host.sh --copy-env
```

If the data root or container identity must differ, pass the intended values to the helper, then edit `.env` to contain exactly the same values before starting Compose:

```bash
scripts/bootstrap-host.sh \
  --copy-env \
  --data-root /path/to/wit-data \
  --puid "$(id -u)" \
  --pgid "$(id -g)"
```

The helper copies `.env.example` unchanged; command-line overrides do **not** rewrite the copied values. It validates all target paths before creating anything, never overwrites an existing `.env`, and does not change ownership, start containers, or delete data. Arrange host permissions yourself when the selected container IDs differ from the directory owner. In particular, the Seerr image runs as UID `1000` by default and its config directory must be writable by that UID.

Keep `.env` limited to the documented non-secret Compose values. API keys, qBittorrent credentials, Seerr logins, and source credentials do not belong there. See [Compose services and storage](#compose-services-and-storage) and the [complete Compose variable table](docs/configuration.md#compose-env-settings).

Render and validate the final Compose model before pulling or starting anything:

```bash
docker compose config >/dev/null
```

### 2. Configure services in this order

Set up one service at a time so credentials and paths exist before a dependent service needs them:

1. [qBittorrent](#qbittorrent-first-login): start it, retrieve the generated temporary password locally, and replace the temporary login.
2. [Sonarr](#sonarr-first-run-setup): enable its local authentication, add `/tv`, choose a quality profile, connect qBittorrent at `qbittorrent`, and manually configure only authorised acquisition sources. Record the Sonarr API key plus the numeric root-folder and quality-profile IDs for Wit.
3. [Jellyfin](#jellyfin-library-setup-and-optional-transcoding): create the local administrator, add `/tv` as a television library, and create a dedicated Jellyfin API key for Wit.
4. [Seerr](#seerr-discovery-and-request-setup): connect it to Jellyfin and Sonarr through their Compose service names after both are configured.

Each detailed section includes the exact startup command and default local URL. When all first-run wizards are complete, reconcile the full stack and inspect container health:

```bash
docker compose up -d
docker compose ps
```

A healthy container means its local HTTP endpoint responds; it does not prove that authentication, a download client, an acquisition source, or a library path is correctly configured.

### 3. Configure Wit separately from Compose

Wit does not read the repository `.env`. Create its state directory and an owner-only configuration file outside the repository:

```bash
mkdir -p "$HOME/.config/wit" "$HOME/.local/state/wit"
chmod 700 "$HOME/.config/wit" "$HOME/.local/state/wit"
touch "$HOME/.config/wit/config.toml"
chmod 600 "$HOME/.config/wit/config.toml"
```

Populate that file using the [protected TOML template](docs/configuration.md#protected-toml-file), including the two service API keys and the numeric Sonarr defaults recorded above. From the host, use the published loopback URLs such as `http://127.0.0.1:8989`; Compose names such as `sonarr` work only between containers.

Select the protected file by path, not by passing any credential on a command line:

```bash
export WIT_CONFIG_FILE="$HOME/.config/wit/config.toml"
```

It is safe to persist that path-only export in a shell profile. Keep the file itself outside the checkout, owner-only, and out of backups or logs that are not approved to contain secrets. [`docs/configuration.md`](docs/configuration.md) lists every CLI and Compose setting, precedence, validation rules, and where each secret belongs.

### 4. Verify readiness and run the first request

Run the read-only diagnostic before depending on any service:

```bash
wit doctor
```

Resolve every failed required check, then create a read-only plan and inspect every printed episode:

```bash
wit plan "Example Show" --first 4
```

Copy the generated plan ID from the output. In the commands below, `<plan-id>` means that exact value and is not literal shell syntax. Apply only after reviewing and affirmatively confirming that displayed plan, then inspect progress:

```bash
wit apply <plan-id>
wit status <plan-id>
```

Interactive apply defaults to **no**. Non-interactive callers may use `--yes` only after receiving confirmation tied to the displayed plan. `--allow-stale` and `--allow-mismatch` are separate safety approvals, not routine flags. Use `--json` on any of these four commands when a versioned machine-readable result is required.

## Available diagnostics

After supplying the documented runtime settings and creating the configured state directory, run:

```bash
wit doctor
```

`wit doctor` validates configuration before making network requests, checks that the state directory exists with read, write, and search access, and reports Sonarr, Jellyfin, and Seerr health independently. It does not create directories or mutate services. The command exits with status `0` only when every required check passes and status `1` when configuration, a local path, or a service check fails. Diagnostic output names settings that need attention but never prints API credentials.

## Read-only planning

`wit plan` searches public TVmaze metadata, resolves a show, selects aired regular episodes, prints the complete plan, and only then saves its secret-free JSON under the configured state directory. It does not contact or mutate Sonarr, search for downloads, or apply the plan.

Supply exactly one primary selector:

```bash
wit plan "Example Show" --first 4
wit plan "Example Show" --first 3 --season 2
wit plan "Example Show" --season 2 --episodes 3-6
wit plan "Example Show" --all-aired
```

`--first N` uses season/episode order and may be limited to one positive `--season`. `--episodes START-END` is an inclusive range and requires `--season`. `--all-aired` cannot be combined with another selector or a season. Every rule excludes season zero, specials, future episodes, and undated episodes by default.

Wit chooses a show automatically only for a confident deterministic title match. An ambiguous result exits without fetching episodes or writing a plan and prints ordered TVmaze candidate IDs; retry with the displayed ID, for example `--candidate 123`. `--year YYYY` may disambiguate matching remakes, but never turns an inexact title into an automatic match. A selected show must have a TVDB ID so a future apply operation can map it safely to Sonarr.

The saved plan ID is printed after persistence. Plan files contain stable show identity and selected episode coordinates and titles, never service credentials or raw API responses.

## Applying a stored plan

Apply only a previously saved plan. With an interactive terminal, Wit reprints every stored episode and asks for confirmation with a safe default of no:

```bash
wit apply <plan-id>
```

Automation and any other non-interactive invocation must provide the explicit confirmation flag:

```bash
wit apply <plan-id> --yes
```

By default, Wit rejects a plan more than seven 24-hour days old before contacting Sonarr. Review an intentionally delayed plan and add `--allow-stale` to override that age check; this flag does not replace the normal prompt or non-interactive `--yes` confirmation.

After confirmation, Wit asks Sonarr to find the TVDB series or add it with series, season, and episode monitoring disabled and without an automatic search. It fetches the current Sonarr episodes, maps every stored season/episode coordinate, and reads the complete active queue before changing episode monitoring. A missing or ambiguous coordinate stops the operation without monitoring or searching only part of the plan. If the series had to be added before mapping could occur, it remains in Sonarr unmonitored after a mapping failure.

Harmless title case, punctuation, and whitespace differences are accepted. Material series-title, episode-title, or title/coordinate differences are displayed and require a second interactive confirmation. Non-interactive callers must separately provide `--allow-mismatch`; `--yes` alone does not approve metadata that differs from the inspected plan.

Episodes for which Sonarr already has a file are skipped. Episodes in queued, downloading, or importing queue states are also skipped, so repeating an apply does not submit another search for work already in progress. A matching warning or failed queue item is rejected rather than searched again. Wit monitors and submits one targeted `EpisodeSearch` only for the remaining actionable IDs; when none remain, it makes neither call.

The result reports `Applied`, `Skipped-file`, `Skipped-queue`, and `Rejected` counts independently, followed by the command ID or an explicit no-action value. Any rejected episode makes the command exit non-zero, while a no-op consisting only of safe file and queue skips succeeds.

## Inspecting a stored request

Use the same stored plan ID to inspect progress without changing either service:

```bash
wit status <plan-id>
```

`wit status` shows a Sonarr state for every planned coordinate: `planned`, `queued`, `downloading`, `imported`, `missing`, `warning`, or `failed`. For episodes Sonarr reports as imported, it also shows whether the coordinate is visible in Jellyfin, whether the series or episode is absent, or whether Jellyfin is unavailable. Jellyfin is not queried when Sonarr has not imported any planned episode, and status never triggers a library scan.

The overall result is `ACTIVE` while work or library visibility is incomplete, `COMPLETE` only when every planned episode is imported and visible, `DEGRADED` when Jellyfin is unavailable but Sonarr status succeeded, and `FAILED` for a Sonarr warning, failure, or inconsistent coordinate mapping. Active and degraded results exit with status `0`; configuration, plan loading, service-read failures, and a `FAILED` result exit non-zero. The command performs only bounded read operations and never changes monitoring, queues, or media.

## Machine-readable output and exit codes

Add `--json` to `doctor`, `plan`, `apply`, or `status` for automation:

```bash
wit doctor --json
wit plan "Example Show" --first 4 --json
wit apply <plan-id> --yes --json
wit status <plan-id> --json
```

A valid JSON-mode invocation writes exactly one JSON document to standard output. It does not mix human rendering, progress text, terminal decoration, or confirmation prompts into that stream. JSON apply is deliberately non-interactive and still requires `--yes`; metadata differences still require the separate `--allow-mismatch` safeguard.

Every command uses output schema version 1 and the same top-level envelope:

```json
{
  "schema_version": 1,
  "command": "status",
  "success": false,
  "data": null,
  "warnings": [],
  "errors": [
    {
      "code": "plan-load-failed",
      "message": "The stored plan could not be loaded"
    }
  ]
}
```

`command` is one of `doctor`, `plan`, `apply`, or `status`. `success` agrees with command exit success. `warnings` and `errors` are always arrays of stable `code` and safe human-readable `message` objects; a successful envelope has no errors, and a failed envelope has at least one. `data` is command-specific and may be `null` when failure occurs before safe result data exists:

- `doctor` returns configuration validity, overall state, local-path checks, and each service check.
- `plan` returns the query, save state, complete secret-free plan, and candidates when explicit selection is required.
- `apply` returns the complete stored plan and, when available, Sonarr series, disjoint outcome counts and episode IDs, command state, and confirmed discrepancies.
- `status` returns the complete stored plan and normalised Sonarr, queue, command, per-episode, and Jellyfin visibility state.

Consumers must reject unsupported `schema_version` values rather than guessing at a changed contract. The version covers both the envelope and command data shapes.

Exit-code semantics are:

- **0** — the command completed successfully and a JSON envelope has `success: true`. For `status`, ordinary incomplete (`active`) and Jellyfin-unavailable (`degraded`) results are successful reads. A safe apply no-op also succeeds.
- **1** — configuration, persistence, service access, safety confirmation, apply rejection, or failed request status prevented command success; a JSON invocation that reached the command emits `success: false` with at least one error.
- **2** — command-line usage or argument validation failed. Errors detected inside the command use a failed JSON envelope; errors rejected by Typer before command execution are written to standard error and cannot produce an envelope. Standard output remains empty in that case.

Human-readable output remains the default when `--json` is absent.

## Responsible use

Wit will not provide content sources, configure indexers, bypass DRM, extract subscription-service credentials, or expose services publicly by default. Operators are responsible for using media and download sources they are authorised to use.

Never commit a real `.env`, API key, credential, private hostname, machine-specific path, or media-library data.

## Wit runtime configuration

The typed configuration layer validates service URLs, Sonarr apply defaults, bounded HTTP timeouts, and the local XDG state path. Sonarr and Jellyfin API keys are accepted only through `WIT_*` environment values or an explicitly selected, owner-only TOML file; secret values are redacted from representations and configuration errors. `wit doctor` uses these settings for read-only diagnostics, `wit plan` uses only the TVmaze, timeout, and state values after validating the complete configuration, confirmed `wit apply` uses the state store plus Sonarr settings, and `wit status` reads the state store, Sonarr, and Jellyfin. Credentials are never accepted as command arguments.

See [`docs/configuration.md`](docs/configuration.md) for the complete variable list, protected-file format, precedence, validation rules, and the distinction from Compose's local `.env` file.

## Compose services and storage

[`compose.yml`](compose.yml) provides qBittorrent as the stack's default download client, Sonarr for television-library management, Jellyfin as the completed-library browser and player, and Seerr as the human discovery and request interface. All four services join the isolated service network and a bridge used for outbound traffic. Their web interfaces are available only on `127.0.0.1` by default. The Compose model validates without a real `.env`:

```bash
docker compose config
```

All persistent bind-mounted data lives below one `WIT_DATA_ROOT` (default: the ignored `./data` directory) with this fixed layout:

- `${WIT_DATA_ROOT}/config/qbittorrent/` for qBittorrent configuration
- `${WIT_DATA_ROOT}/config/sonarr/` for Sonarr configuration
- `${WIT_DATA_ROOT}/config/jellyfin/` for Jellyfin configuration
- `${WIT_DATA_ROOT}/config/seerr/` for Seerr configuration and its local database
- `${WIT_DATA_ROOT}/cache/jellyfin/` for Jellyfin's separately persisted cache
- `${WIT_DATA_ROOT}/config/` for other per-service configuration added later
- `${WIT_DATA_ROOT}/downloads/` mounted at `/downloads` in both qBittorrent and Sonarr
- `${WIT_DATA_ROOT}/television/` mounted at `/tv` in Sonarr and read-only at `/tv` in Jellyfin

[`.env.example`](.env.example) defines generic `PUID`, `PGID`, `TZ`, and localhost port defaults. Copy it to the ignored `.env` only when local overrides are needed, and keep every machine-specific value there.

### Host directory bootstrap

Run the non-destructive host helper before starting the stack:

```bash
scripts/bootstrap-host.sh --copy-env
```

The helper creates every documented service configuration directory, Jellyfin's cache directory, the shared downloads directory, and the television library. `--copy-env` copies `.env.example` unchanged only when `.env` is absent and gives the new file mode `600`; an existing `.env` is never overwritten or re-permissioned.

The defaults match `.env.example`. Use `--data-root`, `--puid`, and `--pgid` (or the corresponding `WIT_DATA_ROOT`, `PUID`, and `PGID` environment variables) when local values differ, then put those same non-secret values in the ignored `.env` before running Compose. Relative data roots are resolved from the repository root. The helper validates all IDs and target paths before creating anything, refuses empty paths, `/`, the repository root, non-directory targets, and paths that escape through symlinks, and never changes ownership, starts containers, or deletes existing data. Run `scripts/bootstrap-host.sh --help` for the complete option list.

### Health checks, startup ordering, and image updates

Every service has a credential-free HTTP health check against its container loopback interface, an `unless-stopped` restart policy, and `no-new-privileges`. Health indicates that the local application endpoint responds; it does not prove that first-run setup or external integrations are correctly configured.

Compose waits for qBittorrent to become healthy before starting Sonarr. Seerr waits for both Sonarr and Jellyfin because those are its configured upstream services. These conditions provide deterministic startup ordering only: they do not restart a dependent service after an upstream runtime outage.

Images are constrained to explicit stable application release tags in [`compose.yml`](compose.yml), never floating `latest`, major-version, or development aliases. Updates are deliberate and service-by-service: review the upstream release and security notes, change the service to a new exact release tag, run the repository quality gate, then pull and recreate only that service. Do not use an unattended image updater for this stack. For example, after a reviewed Seerr tag change:

```bash
scripts/quality-gate.sh
docker compose pull seerr
docker compose up -d seerr
docker compose ps seerr
```

[`scripts/check-compose-config.sh`](scripts/check-compose-config.sh) renders the model with an empty environment file and asserts the pinned images, health checks, restart and security settings, healthy dependency conditions, isolated network, and localhost port bindings. It uses `docker compose config` only; it does not pull images or create or start containers.

### qBittorrent first login

Start only qBittorrent with `docker compose up -d qbittorrent`, then inspect `docker compose logs qbittorrent` locally for the generated temporary password for the initial `admin` user. Sign in at `http://127.0.0.1:8080` (or the locally configured `QBITTORRENT_PORT`) and immediately replace the temporary login with a unique username and password in the Web UI settings. Until it is changed, qBittorrent generates a new temporary password on each start. Do not paste the log output into tickets or store the resulting credentials in Compose, `.env`, or any committed file.

### Sonarr first-run setup

After completing qBittorrent's first login, start Sonarr and open its host-local interface:

```bash
docker compose up -d sonarr
```

The default URL is `http://127.0.0.1:8989`; use the locally configured `SONARR_PORT` when it differs. Complete Sonarr's authentication setup rather than leaving its administrative UI open without a login, even though Compose binds it only to loopback.

In **Settings → Media Management**, add `/tv` as the root folder. Select or create an appropriate television quality profile in Sonarr. In **Settings → Download Clients**, add qBittorrent using:

- host `qbittorrent` (not `localhost`)
- Web UI port `8080`, or the configured `QBITTORRENT_PORT`
- the credentials created in qBittorrent's Web UI

Use Sonarr's connection test before saving. Both containers see download data at `/downloads`, so a remote path mapping is not normally needed. Sonarr alone owns completed-download handling and import into `/tv`; Wit does not control qBittorrent directly.

#### Configure authorised sources manually

Wit and this Compose file provide no indexer, tracker, feed, provider preset, or other acquisition source. In Sonarr's local **Settings → Indexers** interface, manually add only a source that you are authorised to use, following that source's official documentation and terms. Store any source credential only in Sonarr's persisted application configuration, use Sonarr's built-in connection test, and do not put it in Wit configuration, Compose `.env`, shell commands, tickets, or committed files.

The project intentionally recommends no specific source and performs no automatic source setup. Without a working authorised source, service health checks can pass and Wit can submit a targeted search, but Sonarr will not be able to find releases.

Before configuring the Wit CLI, create or identify a Sonarr API key and record the positive numeric IDs for the `/tv` root folder and selected quality profile. These IDs are API identities, not the path or profile name. Wit currently has no operator command that enumerates them; identify them through Sonarr's local administrative interface and official documentation rather than guessing. Wit validates both selections before adding a new series and fails safely if either is missing or the root folder is inaccessible. Put the API key only in the protected Wit TOML file or an inherited secret environment, as described in [`docs/configuration.md`](docs/configuration.md).

The repository never preconfigures Sonarr authentication, API keys, root folders, download-client credentials, quality choices, or acquisition sources.

### Jellyfin library setup and optional transcoding

Start Jellyfin with `docker compose up -d jellyfin` and open `http://127.0.0.1:8096` (or the locally configured `JELLYFIN_PORT`). Create a unique local administrator in the first-run wizard and add a television library rooted at `/tv`. Compose mounts that path read-only so Jellyfin can catalogue and play completed episodes without modifying the media Sonarr manages. Jellyfin configuration and cache data are writable, separate bind mounts under `WIT_DATA_ROOT`.

After setup, create a dedicated API key for Wit from Jellyfin's administrator dashboard. Put that value only in the protected Wit TOML file or an inherited secret environment; do not put the administrator password or API key in Compose `.env`. Wit uses the API key for read-only health and library-visibility requests and never triggers a library scan.

Hardware transcoding is optional and host-specific. The default service intentionally enables no GPU runtime or host devices. Follow Jellyfin's [hardware-acceleration documentation](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/) and keep any host-specific Compose override untracked (for example, list `compose.override.yml` in `.git/info/exclude`) if acceleration is needed. Grant only the required device: Linux VA-API or QSV hosts commonly use a render node such as `/dev/dri/renderD128`, while other hardware has different requirements. Verify device ownership and container-user access locally rather than committing a machine-specific mapping. The default service also publishes no discovery ports and does not use host networking.

### Seerr discovery and request setup

Complete the first-run setup for Sonarr and Jellyfin before configuring Seerr. Start Seerr with `docker compose up -d seerr` and open `http://127.0.0.1:5055` (or the locally configured `SEERR_PORT`). The official image runs as UID 1000 by default, so `${WIT_DATA_ROOT}/config/seerr/` must be writable by that user for settings and its local database to persist.

In Seerr's setup wizard, connect Jellyfin at `http://jellyfin:8096` and Sonarr at `http://sonarr:8989`; these Compose service names work between containers. Supply the required login or API credentials only through the local first-run interfaces. No users, credentials, or API keys are preconfigured or stored in committed files.

Seerr is the human discovery and request browser and hands approved series requests to Sonarr. It is not in Wit's episode-level plan/apply path: that planned CLI workflow communicates with Sonarr directly. The current complete configuration and `wit doctor` diagnostics still require a Seerr URL and a healthy Seerr endpoint.

## Implemented limitations and local-network assumptions

- **Television only:** Wit does not automate movies. Planning selects aired, dated, regular episodes only; season zero, specials, future or otherwise unaired episodes, and undated episodes are excluded. There is no override that broadens those rules.
- **Metadata identity required:** planning needs public TVmaze access and a confidently matched show with a TVDB ID. Ambiguous matches require an explicit TVmaze candidate ID. Wit does not silently fall back to a similarly named show.
- **Manual service setup:** no users, authentication, root folders, quality choices, download-client credentials, acquisition sources, or Jellyfin libraries are provisioned. Wit also cannot currently list Sonarr root-folder or quality-profile IDs for the operator.
- **Authorised sources only:** qBittorrent is only a download client. Sonarr needs a separately configured, lawful source before targeted searches can acquire anything. Wit neither verifies nor manages that source.
- **Host-local administration:** every web port is bound to `127.0.0.1`. A browser on another LAN device cannot connect directly. The repository provides no TLS, reverse proxy, VPN, public exposure, or remote-access automation; operators must design and secure any non-local access separately.
- **Host/container addressing differs:** host-run Wit uses published loopback URLs. The names `qbittorrent`, `sonarr`, and `jellyfin` resolve only between Compose containers. The supported setup assumes Wit runs on the Docker host, not in a container.
- **Conservative Compose defaults:** qBittorrent's peer/listening port, Jellyfin discovery ports, host networking, and GPU devices are not exposed. Only the four administrative HTTP ports are published. Hardware transcoding and inbound peer connectivity require deliberate, untracked host-specific configuration.
- **Complete CLI configuration:** every command validates the complete Sonarr, Jellyfin, and Seerr configuration even when that command uses only a subset. For example, planning contacts only TVmaze but still refuses an incomplete runtime configuration.
- **Narrow mutations:** apply can add a series unmonitored, monitor mapped episodes, and submit one targeted Sonarr search. Wit does not cancel queues, delete media, trigger Jellyfin scans, change broad season monitoring, or control qBittorrent directly.
- **Visibility is observational:** `wit status` trusts Sonarr import state and Jellyfin's current catalogue. It reports an imported episode as not yet visible until Jellyfin discovers it, but never forces a scan.
- **Single-host, pre-1.0 operation:** the bind-mount/bootstrap flow is documented for a Linux-compatible local host. There is no multi-user tenancy, hosted service, migration utility, or published binary/package release.

These are intentional first-release boundaries, not instructions to bypass safeguards. Operators remain responsible for service hardening, backups, source authorisation, and compliance with applicable law.

## Development and autonomous build

Wit retains a ticket-driven autonomous build harness for maintainers. Product scope is already defined; changes must follow the project brief and the first `TODO` ticket rather than treating the repository as a blank project generator. See [changing Wit scope safely](docs/CUSTOMISING.md), [autonomous build operations](docs/USAGE.md), and the [build safety notes](docs/SAFETY.md).

Required reading for an implementation cycle:

- [`AGENTS.md`](AGENTS.md)
- [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md)
- [`BUILD_TICKETS.md`](BUILD_TICKETS.md)

Bootstrap development requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). Install the locked dependencies and run the available CLI options with:

```bash
uv sync --locked --all-groups
uv run wit --help
uv run wit --version
```

Run the current quality gate:

```bash
scripts/quality-gate.sh
```

Run one local ticket cycle without pushing:

```bash
just autobuild
```

Run multiple cycles with each successful commit pushed:

```bash
just run 10
```

Follow or monitor an active build:

```bash
just follow
just monitor 10
```

The autonomous loop must implement only the first `TODO` ticket, validate it, mark that ticket done, make one commit, and leave a clean tree.

## Technology

- Python 3.12+
- Typer, httpx, and Pydantic
- uv, pytest, Ruff, and mypy
- Docker Compose
- Jellyfin, Seerr, Sonarr, and qBittorrent
- GitHub Actions

## License

[MIT](LICENSE.md)
