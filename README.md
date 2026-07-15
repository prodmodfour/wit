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

Wit is currently in early autonomous development, not a working media application yet. The installable CLI supports `wit --help`, `wit --version`, and the read-only `wit doctor` diagnostics described below; planning, apply, and status commands remain planned. The implementation queue is defined in [`BUILD_TICKETS.md`](BUILD_TICKETS.md), and the required outcome and safety boundaries are defined in [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md).

Each successful build ticket is deliberately sized for one focused conventional commit.

## Available diagnostics

After supplying the documented runtime settings and creating the configured state directory, run:

```bash
wit doctor
```

`wit doctor` validates configuration before making network requests, checks that the state directory exists with read, write, and search access, and reports Sonarr, Jellyfin, and Seerr health independently. It does not create directories or mutate services. The command exits with status `0` only when every required check passes and status `1` when configuration, a local path, or a service check fails. Diagnostic output names settings that need attention but never prints API credentials.

## Intended media workflow

Once the remaining commands are implemented, examples will look like:

```bash
wit plan "Example Show" --first 4
wit apply <plan-id> --yes
wit status <plan-id>
```

Planning is read-only. Applying a plan is explicit, narrowly targeted, and handled through Sonarr. “First N” means aired, non-special episodes in season/episode order; “all aired” excludes specials and future episodes by default.

## Responsible use

Wit will not provide content sources, configure indexers, bypass DRM, extract subscription-service credentials, or expose services publicly by default. Operators are responsible for using media and download sources they are authorised to use.

Never commit a real `.env`, API key, credential, private hostname, machine-specific path, or media-library data.

## Wit runtime configuration

The typed configuration layer validates service URLs, Sonarr apply defaults, bounded HTTP timeouts, and the local XDG state path. Sonarr and Jellyfin API keys are accepted only through `WIT_*` environment values or an explicitly selected, owner-only TOML file; secret values are redacted from representations and configuration errors. `wit doctor` consumes these settings only for read-only diagnostics; mutating media commands are not implemented yet.

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

### Sonarr download-client setup

After completing qBittorrent's first login, start Sonarr with `docker compose up -d sonarr` and open `http://127.0.0.1:8989` (or the locally configured `SONARR_PORT`). In Sonarr's download-client settings, add qBittorrent using the Compose service hostname `qbittorrent`, its Web UI port (`8080` by default, or the configured `QBITTORRENT_PORT`), and the credentials set through qBittorrent's Web UI. Do not use `localhost` as the qBittorrent hostname from inside Sonarr.

Both containers see download data at `/downloads`, so a remote path mapping is not normally needed. Sonarr sees the television library at `/tv`; select that container path as its root folder through the Sonarr UI when setting up the library. The repository does not preconfigure download-client credentials, API keys, root folders, indexers, feeds, trackers, or content sources.

### Jellyfin library setup and optional transcoding

Start Jellyfin with `docker compose up -d jellyfin` and open `http://127.0.0.1:8096` (or the locally configured `JELLYFIN_PORT`). In the first-run wizard, add the television library from `/tv`. Compose mounts that path read-only so Jellyfin can catalogue and play completed episodes without modifying the media Sonarr manages. Jellyfin configuration and cache data are writable, separate bind mounts under `WIT_DATA_ROOT`.

Hardware transcoding is optional and host-specific. The default service intentionally enables no GPU runtime or host devices. Follow Jellyfin's [hardware-acceleration documentation](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/) and use the ignored local `compose.override.yml` if acceleration is needed. Grant only the required device: Linux VA-API or QSV hosts commonly use a render node such as `/dev/dri/renderD128`, while other hardware has different requirements. Verify device ownership and container-user access locally rather than committing a machine-specific mapping. The default service also publishes no discovery ports and does not use host networking.

### Seerr discovery and request setup

Complete the first-run setup for Sonarr and Jellyfin before configuring Seerr. Start Seerr with `docker compose up -d seerr` and open `http://127.0.0.1:5055` (or the locally configured `SEERR_PORT`). The official image runs as UID 1000 by default, so `${WIT_DATA_ROOT}/config/seerr/` must be writable by that user for settings and its local database to persist.

In Seerr's setup wizard, connect Jellyfin at `http://jellyfin:8096` and Sonarr at `http://sonarr:8989`; these Compose service names work between containers. Supply the required login or API credentials only through the local first-run interfaces. No users, credentials, or API keys are preconfigured or stored in committed files.

Seerr is the optional human discovery and request browser and hands approved series requests to Sonarr. It is not in Wit's episode-level plan/apply path: that planned CLI workflow communicates with Sonarr directly and does not depend on Seerr being available.

## Building Wit

This repository was created from the autonomous-build template and retains its ticket-driven build tooling.

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

## Planned technology

- Python 3.12+
- Typer, httpx, and Pydantic
- uv, pytest, Ruff, and mypy
- Docker Compose
- Jellyfin, Seerr, Sonarr, and qBittorrent
- GitHub Actions

## License

[MIT](LICENSE.md)
