# Wit runtime configuration

Wit's typed configuration layer reads runtime values from the process environment or from one explicitly selected TOML file. It does not discover a repository `.env` file and does not accept credentials as CLI arguments. The media-operation commands that will consume these settings are still under development.

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

Service URLs must use `http` or `https` and include a host. Reverse-proxy path prefixes are supported, but credentials, query strings, and fragments in a base URL are rejected. Keep API keys in the environment itself, not in shell command arguments or shell history.

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
