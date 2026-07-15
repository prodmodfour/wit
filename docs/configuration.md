# Wit runtime configuration

Wit's typed configuration layer reads runtime values from the process environment or from one explicitly selected TOML file. It does not discover a repository `.env` file and does not accept credentials as CLI arguments. The read-only `wit doctor` and `wit plan` commands consume these settings; mutating media-operation commands are still under development.

## Environment settings

Set individual values in the environment before invoking Wit:

| Variable | Required | Meaning |
| --- | --- | --- |
| `WIT_SONARR_URL` | yes | Sonarr base URL |
| `WIT_SONARR_API_KEY` | yes | Sonarr API key; treated as a secret |
| `WIT_SONARR_ROOT_FOLDER_ID` | yes | Positive Sonarr root-folder ID used by apply |
| `WIT_SONARR_QUALITY_PROFILE_ID` | yes | Positive Sonarr quality-profile ID used by apply |
| `WIT_JELLYFIN_URL` | yes | Jellyfin base URL |
| `WIT_JELLYFIN_API_KEY` | yes | Jellyfin API key; treated as a secret |
| `WIT_SEERR_URL` | yes | Seerr base URL |
| `WIT_TVMAZE_URL` | no | TVmaze base URL; defaults to `https://api.tvmaze.com` |
| `WIT_HTTP_CONNECT_TIMEOUT_SECONDS` | no | Connect timeout from 0.1 through 60 seconds; defaults to 5 |
| `WIT_HTTP_READ_TIMEOUT_SECONDS` | no | Read timeout from 0.1 through 120 seconds; defaults to 30 |
| `WIT_STATE_DIR` | no | Absolute local state directory |
| `WIT_CONFIG_FILE` | no | Absolute path to the protected TOML file described below |

Sonarr and Jellyfin need API keys for the API operations Wit will perform. TVmaze's metadata API and the Seerr health endpoint used by Wit do not require credentials, so no TVmaze or Seerr credential is modelled.

When `WIT_STATE_DIR` is absent, Wit uses `${XDG_STATE_HOME}/wit`. If `XDG_STATE_HOME` is unset, the fallback is `~/.local/state/wit`. A state path may not be relative, a filesystem root, a symbolic link, an existing non-directory, or contain `..` traversal.

Download plans are persisted as inspectable JSON at `<state-dir>/plans/<plan-id>.json`. The persistence layer creates the Wit and `plans` directories with mode `0700`, writes plan files with mode `0600`, and atomically replaces only complete files. It refuses traversal-style IDs, plan or directory symlinks, non-regular files, mismatched IDs, corrupt JSON, and unsupported plan schema versions. Listing considers only valid plan-shaped JSON filenames and does not return unrelated state-directory entries. `wit plan` renders the complete plan before writing it to this store and prints the saved plan ID afterward.

Service URLs must use `http` or `https` and include a host. Reverse-proxy path prefixes are supported, but credentials, query strings, and fragments in a base URL are rejected. Keep API keys in the environment itself, not in shell command arguments or shell history.

## Diagnose configuration and connectivity

The configured state directory must already exist and grant the current user read, write, and search access. For the default location, create it with owner-only permissions before the first diagnostic run:

```bash
mkdir -p "$HOME/.local/state/wit"
chmod 700 "$HOME/.local/state/wit"
wit doctor
```

`wit doctor` first loads and validates configuration. Invalid configuration stops the command before any service request. Once configuration is valid, the command inspects the state directory without creating or changing it and checks Sonarr, Jellyfin, and Seerr independently through their read-only health endpoints. A failure from one service does not prevent the other service results from being reported.

The command prints a safe action for missing paths, unavailable services, authentication failures, and unhealthy responses. It never prints API credential values. Exit status `0` means every required local and service check passed; exit status `1` means at least one required check failed.

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
root_folder_id = 1
quality_profile_id = 1

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

The root repository `.env` remains for local Docker Compose overrides. Wit deliberately does not load it as runtime CLI configuration, and real credentials must never be committed there or anywhere else in the repository.
