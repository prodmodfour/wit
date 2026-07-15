# Wit configuration

Wit has two independent configuration domains. Do not assume that a value supplied to one is visible to the other:

| Configuration | Consumer | Contents | Secret-bearing? |
| --- | --- | --- | --- |
| repository `.env` | Docker Compose interpolation | data root, container identity, timezone, and host ports | no; keep it non-secret |
| protected TOML or inherited `WIT_*` environment | the `wit` CLI | service URLs, Sonarr defaults, API keys, timeouts, and state path | yes; contains Sonarr and Jellyfin API keys |
| each service's persisted config below `WIT_DATA_ROOT` | qBittorrent, Sonarr, Jellyfin, or Seerr | users, service integrations, and service-owned credentials | yes; manage it through the service UI |

The CLI never discovers the repository `.env`, never accepts credentials as command options, and never stores credentials in plans. It reads runtime values from one explicitly selected owner-only TOML file, the process environment, or both. The protected TOML file is recommended for a persistent local installation.

See the [`README` first-run guide](../README.md#first-run-guide) for host preparation and service setup order.

## CLI runtime settings

Every current CLI command loads and validates the **complete** runtime configuration before doing command-specific work. Therefore every row marked “required” must be supplied even though `plan`, for example, contacts only TVmaze.

| Variable | Requirement/default | Secret? | Meaning |
| --- | --- | --- | --- |
| `WIT_SONARR_URL` | required | no | Sonarr base URL as reached by the host-run CLI |
| `WIT_SONARR_API_KEY` | required | **yes** | Sonarr API key used for health, library, apply, and status operations |
| `WIT_SONARR_ROOT_FOLDER_ID` | required | no | Positive numeric Sonarr root-folder ID used when adding a series |
| `WIT_SONARR_QUALITY_PROFILE_ID` | required | no | Positive numeric Sonarr quality-profile ID used when adding a series |
| `WIT_JELLYFIN_URL` | required | no | Jellyfin base URL as reached by the host-run CLI |
| `WIT_JELLYFIN_API_KEY` | required | **yes** | Dedicated Jellyfin API key used for health and read-only library lookup |
| `WIT_SEERR_URL` | required | no | Seerr base URL used for its credential-free health endpoint |
| `WIT_TVMAZE_URL` | `https://api.tvmaze.com` | no | Read-only TVmaze metadata API base URL |
| `WIT_HTTP_CONNECT_TIMEOUT_SECONDS` | `5`; range 0.1–60 | no | Shared HTTP connection timeout in seconds |
| `WIT_HTTP_READ_TIMEOUT_SECONDS` | `30`; range 0.1–120 | no | Shared HTTP response-read timeout in seconds |
| `WIT_STATE_DIR` | XDG-derived path | no | Absolute single-user state directory after `~` expansion |
| `WIT_CONFIG_FILE` | unset | no (path only) | Absolute path, after `~` expansion, to the protected TOML file |

These are all supported CLI runtime environment settings. Unknown TOML fields are rejected; there are no undocumented TVmaze, Seerr, qBittorrent, source, or download-client credential settings.

From the Docker host, the default service URLs are `http://127.0.0.1:8989` for Sonarr, `http://127.0.0.1:8096` for Jellyfin, and `http://127.0.0.1:5055` for Seerr. Use locally changed host ports when applicable. Compose names such as `sonarr` and `jellyfin` resolve between containers, not from a host-run CLI.

Service URLs must use `http` or `https` and include a host. Reverse-proxy path prefixes are supported, but credentials, query strings, and fragments in a base URL are rejected. Sonarr root-folder and quality-profile values are positive API IDs, not a filesystem path or profile name. Configure `/tv` and the profile in Sonarr first, then identify their numeric IDs through Sonarr's local administrative interface and official documentation. Wit currently cannot enumerate those choices; it validates them before adding a new series and refuses an absent or inaccessible selection.

When `WIT_STATE_DIR` is absent, Wit uses `${XDG_STATE_HOME}/wit`. `XDG_STATE_HOME` must resolve to an absolute path. If it is unset, the fallback is `~/.local/state/wit`. A state path may not be relative after expansion, a filesystem root, a symbolic link, an existing non-directory, or contain `..` traversal.

Download plans are persisted as inspectable JSON at `<state-dir>/plans/<plan-id>.json`. The persistence layer creates the Wit and `plans` directories with mode `0700`, writes plan files with mode `0600`, and atomically replaces only complete files. It refuses traversal-style IDs, plan or directory symlinks, non-regular files, mismatched IDs, corrupt JSON, and unsupported plan schema versions. Listing considers only valid plan-shaped JSON filenames and does not return unrelated state-directory entries. `wit plan` renders the complete plan before writing it to this store and prints the saved plan ID afterward.

## Where credentials and other secrets belong

| Secret | Create/manage it in | Additional Wit placement |
| --- | --- | --- |
| qBittorrent Web UI login | qBittorrent's local Web UI; persisted in its service config | none; Wit never talks to qBittorrent |
| Sonarr login and API key | Sonarr's local administrative UI; persisted in Sonarr config | copy only the API key into protected TOML or a trusted inherited environment |
| authorised source credentials | Sonarr's local indexer/source settings | none; Wit does not configure or consume them |
| Jellyfin administrator login and API key | Jellyfin's local administrator UI; persisted in Jellyfin config | copy only a dedicated API key into protected TOML or a trusted inherited environment |
| Seerr login and upstream credentials | Seerr's local first-run/settings UI; persisted in Seerr config | none; Wit uses only Seerr's credential-free health endpoint |

Never put these values in the repository `.env`, Compose YAML, CLI arguments, plan JSON, shell command lines, issue output, or committed files. If environment-based API keys are necessary, inject them into the `wit` process from a trusted local secret manager or protected service environment rather than typing inline assignments into shell history. Wit redacts typed secret fields and reports validation failures by field name, but that does not make an unsafe storage location safe.

## First runtime setup

1. Complete qBittorrent, Sonarr, Jellyfin, and Seerr first-run setup in the order documented in the README.
2. In Sonarr, configure `/tv`, a quality profile, qBittorrent, and only authorised sources; record the root-folder ID, quality-profile ID, and API key.
3. In Jellyfin, configure `/tv` as television media and create a dedicated API key for Wit.
4. Create owner-only configuration and state locations:

   ```bash
   mkdir -p "$HOME/.config/wit" "$HOME/.local/state/wit"
   chmod 700 "$HOME/.config/wit" "$HOME/.local/state/wit"
   touch "$HOME/.config/wit/config.toml"
   chmod 600 "$HOME/.config/wit/config.toml"
   ```

5. Fill in the [protected TOML file](#protected-toml-file), then select its path:

   ```bash
   export WIT_CONFIG_FILE="$HOME/.config/wit/config.toml"
   ```

6. Run `wit doctor` and resolve every required failure before planning or applying a request.

## Diagnose configuration and connectivity

The configured state directory must already exist and grant the current user read, write, and search access. For the default location, create it with owner-only permissions before the first diagnostic run:

```bash
mkdir -p "$HOME/.local/state/wit"
chmod 700 "$HOME/.local/state/wit"
wit doctor
```

`wit doctor` first loads and validates configuration. Invalid configuration stops the command before any service request. Once configuration is valid, the command inspects the state directory without creating or changing it and checks Sonarr, Jellyfin, and Seerr independently through their read-only health endpoints. A failure from one service does not prevent the other service results from being reported.

The command prints a safe action for missing paths, unavailable services, authentication failures, and unhealthy responses. It never prints API credential values. Exit status `0` means every required local and service check passed; exit status `1` means at least one required check failed.

## Inspect a stored request

`wit status <plan-id>` strictly loads one saved plan, reads current Sonarr episode and queue state, and prints an acquisition/import classification for every planned coordinate. It then uses Jellyfin only when at least one planned episode is imported in Sonarr, reporting viewer visibility for each such episode. The command performs no monitoring change, search, queue cancellation, or Jellyfin library scan.

The Jellyfin lookup first compares the plan's TVDB ID with Jellyfin's `ProviderIds` metadata. Jellyfin 10.11 does not expose an exact provider-ID value filter on its `Items` endpoint, so Wit paginates TVDB-tagged series and performs the exact comparison locally. If no TVDB match exists, Wit uses the title/year fallback only when the plan has a known year. The fallback requires one candidate with the same year and a title equal after harmless case, punctuation, and whitespace normalisation. A candidate carrying a different or malformed TVDB ID is never accepted by fallback, and duplicate external-ID or title/year candidates fail as ambiguous rather than being guessed.

Series and episode queries use authenticated `GET` requests only, exclude virtual, missing, and placeholder episodes, and are limited to 5,000 items per paginated lookup. Exceeding that safety bound fails explicitly instead of returning a partial result. Results distinguish an unavailable Jellyfin server, an absent series, an absent episode coordinate, and a visible episode coordinate.

The overall status is `ACTIVE` for ordinary incomplete work, `COMPLETE` when all imports are visible, `DEGRADED` when Jellyfin is unavailable after Sonarr status succeeds, and `FAILED` for Sonarr warnings, failures, or mapping inconsistencies. Incomplete downloads and degraded Jellyfin visibility do not by themselves produce a failing exit code. Invalid configuration or plans, service reads that cannot complete, and `FAILED` Sonarr results exit non-zero.

## Create a read-only plan

`wit plan` validates the complete runtime configuration, but its network operations use only `WIT_TVMAZE_URL` and the bounded HTTP timeout settings. It searches and retrieves public TVmaze metadata and never contacts Sonarr, Jellyfin, or Seerr. The state directory may be absent before planning; the secure plan store creates it when saving.

Choose exactly one episode rule: `--first N`, `--season S --episodes START-END`, or `--all-aired`. `--season S` may also limit a first-N request. If matching is ambiguous, Wit prints ordered public candidate identities and exits without saving; repeat the command with `--candidate <TVMAZE-ID>`. A result without a TVDB ID is rejected before episode retrieval because it cannot be mapped safely during a future Sonarr apply.

For example:

```bash
wit plan "Example Show" --first 4
wit plan "Example Show" --season 2 --episodes 3-6
wit plan "Example Show" --all-aired
```

Planning prints every selected episode before atomically saving the versioned, secret-free JSON file. It does not apply the plan or initiate media acquisition.

## Apply a stored plan through Sonarr

`wit apply` accepts only a stored plan ID; it does not accept a title, episode selector, API key, or other ad hoc mutation input. Wit strictly loads the corresponding plan from the configured state directory, reprints its complete contents, and then requires confirmation:

```bash
wit apply <plan-id>
```

The prompt is available only when standard input is an interactive terminal and defaults to no. A declined confirmation exits without contacting Sonarr. Scripts, pipes, agents, and other non-interactive callers must provide the explicit confirmation flag:

```bash
wit apply <plan-id> --yes
```

A plan is fresh for seven 24-hour days from its UTC creation time. An older plan is rejected before Sonarr access and all selected episodes are reported as rejected. After reviewing the rendered plan, explicitly add `--allow-stale` to bypass only this age check:

```bash
wit apply <plan-id> --yes --allow-stale
```

After confirmation, apply performs this bounded sequence:

1. find the series by the plan's TVDB ID, or add it fully unmonitored with the configured Sonarr root-folder and quality-profile IDs and no automatic search;
2. fetch the current Sonarr episode list and map every planned season/episode coordinate exactly once;
3. fetch the complete Sonarr queue and classify every mapped episode before episode-level mutation;
4. skip IDs that already have files or have queued, downloading, or importing queue records;
5. reject IDs with matching warning or failed queue records instead of starting duplicate work;
6. monitor only the remaining actionable IDs; and
7. submit one targeted `EpisodeSearch` containing exactly those actionable IDs.

A missing or duplicate coordinate aborts before queue inspection, episode monitoring, or search, so a subset of the plan is never applied. A series newly added before that mapping failure remains in Sonarr unmonitored. When no actionable IDs remain, apply is a successful no-op and neither monitoring nor search is submitted.

Wit treats harmless title case, punctuation, and whitespace differences as equivalent. Material current series-title, episode-title, or title/coordinate differences are shown before episode mutation and require a second interactive confirmation. A non-interactive caller must use both `--yes` and the separate metadata override:

```bash
wit apply <plan-id> --yes --allow-mismatch
```

The result prints separate `Applied`, `Skipped-file`, `Skipped-queue`, and `Rejected` counts. It prints a Sonarr command ID only when at least one episode was actionable. Warning or failed queue records remain under Sonarr's control; Wit does not cancel them, delete media, or silently resubmit them. A result with rejected episodes exits non-zero, while a no-op made entirely of file and active-queue skips exits successfully.

## Protected TOML file

For persistent local settings, create a user-owned TOML file outside the repository. It must be a regular, non-symlink file readable only by its owner (mode `0600`, or `0400` for a read-only file). Select it with an absolute path:

```bash
export WIT_CONFIG_FILE="$HOME/.config/wit/config.toml"
```

The file has this structure:

```toml
state_dir = "~/.local/state/wit"

[sonarr]
url = "http://127.0.0.1:8989"
api_key = "<sonarr-api-key>"
root_folder_id = 1      # Replace with the numeric ID for the configured /tv root
quality_profile_id = 1  # Replace with the numeric ID for the chosen profile

[jellyfin]
url = "http://127.0.0.1:8096"
api_key = "<jellyfin-api-key>"

[seerr]
url = "http://127.0.0.1:5055"

[tvmaze]
url = "https://api.tvmaze.com"

[http]
connect_timeout_seconds = 5
read_timeout_seconds = 30
```

Create or repair its permissions before use:

```bash
chmod 600 "$HOME/.config/wit/config.toml"
```

Individual `WIT_*` environment settings override the corresponding TOML values. Passing a config path directly to the Python configuration loader overrides `WIT_CONFIG_FILE` when embedding Wit, but the file permission and ownership checks still apply. Invalid settings are reported by field name without including input values, and secret fields use Pydantic's redacted representation.

## Compose `.env` settings

The repository `.env` is an ignored, host-specific Docker Compose interpolation file. [`.env.example`](../.env.example) defines every supported value:

| Variable | Default | Secret? | Meaning |
| --- | --- | --- | --- |
| `WIT_DATA_ROOT` | `./data` | no | Host root for all service config, cache, downloads, and television data |
| `PUID` | `1000` | no | Host UID used by qBittorrent and Sonarr and as Jellyfin's container user |
| `PGID` | `1000` | no | Host GID used by qBittorrent and Sonarr and as Jellyfin's container group |
| `TZ` | `Etc/UTC` | no | Container timezone setting |
| `QBITTORRENT_PORT` | `8080` | no | qBittorrent Web UI host port and container Web UI port |
| `SONARR_PORT` | `8989` | no | Host port mapped to Sonarr container port `8989` |
| `JELLYFIN_PORT` | `8096` | no | Host port mapped to Jellyfin container port `8096` |
| `SEERR_PORT` | `5055` | no | Host port mapped to Seerr container port `5055` |

These are all supported Compose environment settings. Despite its prefix, `WIT_DATA_ROOT` is not a CLI runtime setting. Conversely, Compose does not consume `WIT_SONARR_URL`, `WIT_JELLYFIN_API_KEY`, or any other CLI setting.

Create the file and storage tree with:

```bash
scripts/bootstrap-host.sh --copy-env
```

The helper gives a newly copied `.env` mode `0600` but leaves an existing file untouched. Its `--data-root`, `--puid`, and `--pgid` options validate values for directory creation; they do not edit `.env.example` or `.env`. When using overrides, put the same values in `.env` before `docker compose config` or `docker compose up`.

`WIT_DATA_ROOT` may be relative to the repository or an absolute host path, but it must not be empty, `/`, the repository root, a non-directory, or resolve through a child symlink outside the selected root. `PUID` and `PGID` must be positive decimal IDs; the bootstrap helper accepts values from 1 through 4294967294. Seerr does not consume these identity variables and runs as UID `1000` in the pinned image, so its config directory must separately be writable by that UID.

Compose binds all four administrative ports to `127.0.0.1`, regardless of the selected port numbers. It does not use `.env` to provision users or credentials. Keep qBittorrent logins, Sonarr authentication and source credentials, Jellyfin credentials, and Seerr upstream credentials in their service-owned configuration as described above. Wit deliberately does not load `.env` as runtime CLI configuration, and real credentials must never be committed there or anywhere else in the repository.
