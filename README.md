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

Wit is currently an **autonomous-build scaffold**, not a working media application yet. The implementation queue is defined in [`BUILD_TICKETS.md`](BUILD_TICKETS.md), and the required outcome and safety boundaries are defined in [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md).

Each successful build ticket is deliberately sized for one focused conventional commit.

## Intended workflow

Once implemented, examples will look like:

```bash
wit doctor
wit plan "Example Show" --first 4
wit apply <plan-id> --yes
wit status <plan-id>
```

Planning is read-only. Applying a plan is explicit, narrowly targeted, and handled through Sonarr. “First N” means aired, non-special episodes in season/episode order; “all aired” excludes specials and future episodes by default.

## Responsible use

Wit will not provide content sources, configure indexers, bypass DRM, extract subscription-service credentials, or expose services publicly by default. Operators are responsible for using media and download sources they are authorised to use.

Never commit a real `.env`, API key, credential, private hostname, machine-specific path, or media-library data.

## Building Wit

This repository was created from the autonomous-build template and retains its ticket-driven build tooling.

Required reading for an implementation cycle:

- [`AGENTS.md`](AGENTS.md)
- [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md)
- [`BUILD_TICKETS.md`](BUILD_TICKETS.md)

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
