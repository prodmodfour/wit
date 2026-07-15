# Wit architecture

Wit is a single-host television-library operations system. It combines a deterministic Python CLI with a Docker Compose stack, while leaving each media service responsible for the work it already knows how to do.

This document describes the implemented boundaries. See [configuration](configuration.md), [operations](operations.md), [security](security.md), and [troubleshooting](troubleshooting.md) for the corresponding operator guidance.

## System context

```text
Pi / operator
    |
    | invokes `wit` only for episode operations
    v
wit CLI ---- read-only planning ----> TVmaze
    |
    +---- secret-free JSON plans ----> XDG state directory
    |
    +---- confirmed apply -----------> Sonarr ----> configured download client
    |                                      |                 |
    |                                      |                 v
    |                                      +----------> /downloads
    |                                      +----------> /tv
    |
    +---- read-only status ----------> Sonarr
    +---- imported-item visibility --> Jellyfin <---- read-only /tv

Human discovery: browser ----> Seerr ----> Sonarr
Default Compose client: Sonarr ----> qBittorrent
```

The host-run CLI reaches published loopback URLs. Containers use Compose service names on shared bridge networks. Wit is not itself a container in the supported deployment.

## Component responsibilities

| Component | Authority | Explicitly outside its Wit role |
| --- | --- | --- |
| `wit` | Validate configuration; plan exact episodes; persist plans; apply a confirmed plan through Sonarr; report progress | Selecting acquisition sources, controlling qBittorrent directly, playback, queue cancellation, deletion, or library scans |
| TVmaze | Public, read-only show and episode metadata used during planning | Sonarr library state or acquisition decisions |
| Sonarr | Series records, episode monitoring, targeted searches, queue state, download-client integration, and import into `/tv` | Human playback and Wit's title-selection policy |
| qBittorrent | Default Compose download client configured and controlled by Sonarr | A normal Wit or Pi control surface |
| Jellyfin | Completed-media catalogue and playback; read-only visibility checks for Wit | Acquisition, Sonarr monitoring, and automatic scans initiated by Wit |
| Seerr | Human discovery and request browser connected to Sonarr and Jellyfin | Wit's episode-level plan/apply path |
| plan store | Versioned, inspectable request intent under the XDG state directory | Credentials, service responses, queue history, or media paths |

A request made in Seerr follows Seerr and Sonarr policy; it does not create or satisfy a Wit plan. Conversely, `wit plan`, `wit apply`, and `wit status` validate the configured Seerr URL but do not contact Seerr; only `wit doctor` checks its health.

## CLI and code boundaries

The Python package follows this dependency direction:

```text
cli.py
  |
  +--> application services: planning.py, apply.py, status.py, doctor.py
          |
          +--> pure domain: matching.py, selection.py, plans.py
          +--> persistence: plan_store.py
          +--> typed clients: clients/tvmaze.py, clients/sonarr.py,
          |                  clients/jellyfin.py, clients/seerr.py
          +--> shared transport: transport.py

config.py --> validated settings used at the composition boundary
output.py --> versioned JSON envelope models used by the CLI
```

Important separation rules are:

- title matching, episode selection, plan validation, and status classification do not depend on terminal rendering;
- application services decide orchestration order; HTTP clients expose bounded service operations rather than workflow policy;
- service responses are parsed into narrow Pydantic models, and raw payloads are not retained in plans or status errors;
- the shared asynchronous HTTP transport owns authentication headers, bounded timeouts, safe error translation, and cleanup;
- human-readable and JSON rendering happen at the CLI boundary after domain work returns typed results.

## Command data flows

### `wit doctor`

1. Load and validate the complete CLI configuration.
2. Inspect the configured state directory without creating or changing it.
3. Check Sonarr, Jellyfin, and Seerr independently and concurrently.
4. Normalise each result as healthy, unhealthy, unauthorised, or unavailable.

Doctor is read-only. It does not test qBittorrent directly, validate a Sonarr source, or prove that a healthy service is fully configured for acquisition.

### `wit plan`

1. Validate exactly one episode selector and the complete runtime configuration.
2. Search TVmaze using the supplied title.
3. Select only a uniquely confident exact-title or known-alias match; otherwise return ordered candidates.
4. Require an explicit candidate ID when ambiguous and require a stable TVDB ID.
5. Fetch TVmaze episodes and select aired, dated, regular episodes using the current timezone-aware clock.
6. Construct an immutable schema-version-1 plan.
7. In default human mode, render every episode before atomically persisting the plan. JSON mode persists the same complete plan and then emits it in one final envelope so standard output remains one JSON document.

Planning contacts TVmaze only. It never contacts Sonarr, Jellyfin, Seerr, or qBittorrent and cannot start acquisition.

### `wit apply`

1. Load one strictly validated stored plan.
2. Reject a plan older than seven days unless the reviewed plan receives a separate stale override.
3. Render the complete plan and require interactive confirmation, or `--yes` after an external confirmation for non-interactive use.
4. Find the series by TVDB ID. If absent, validate Sonarr's configured root-folder and quality-profile IDs, look it up, and add it with all monitoring disabled and no search.
5. Fetch current Sonarr episodes and map **every** planned `(season, episode)` coordinate exactly once.
6. Fetch the complete bounded Sonarr queue and classify every mapped episode.
7. Skip episodes with files and episodes already queued, downloading, or importing. Reject warning or failed queue records.
8. Before any actionable episode mutation, compare current series and episode titles with the stored metadata. Material differences require a separate confirmation or `--allow-mismatch`.
9. Monitor only the remaining explicit episode IDs, then submit one targeted `EpisodeSearch` for exactly those IDs.

The fail-before-mutation guarantee applies to episode monitoring and search: all coordinates are mapped first. A newly needed series must be added before Sonarr can expose its episode IDs, so a mapping failure can leave that series present but fully unmonitored. Sonarr's monitor and search calls are also separate operations; if search submission fails after monitoring succeeds, the episodes remain monitored. Apply is deliberately narrow, but it is not a distributed transaction.

### `wit status`

1. Load the stored plan and find the Sonarr series by TVDB ID.
2. Map each coordinate, retrieve the complete queue, and combine monitoring, file, and queue observations.
3. Classify each episode as `planned`, `queued`, `downloading`, `imported`, `missing`, `warning`, or `failed`.
4. If Sonarr reports at least one imported episode, find the series in Jellyfin and list visible episode coordinates.
5. Classify the overall result as `active`, `complete`, `degraded`, or `failed`.

Status is observational. It does not cancel queue records, retry searches, alter monitoring, or trigger a Jellyfin scan. The apply command ID is printed but is not stored in schema-version-1 plans, so the current CLI status path primarily reconstructs progress from current series, episode, file, and queue state.

## Mutation matrix

| Operation | `doctor` | `plan` | `apply` | `status` |
| --- | ---: | ---: | ---: | ---: |
| Read local configuration/state | yes | yes | yes | yes |
| Read TVmaze metadata | no | yes | no | no |
| Read Sonarr | yes | no | yes | yes |
| Add a Sonarr series unmonitored | no | no | possible | no |
| Change explicit episode monitoring | no | no | possible | no |
| Submit targeted `EpisodeSearch` | no | no | possible | no |
| Read Jellyfin catalogue | health only | no | no | imported episodes only |
| Read Seerr | health only | no | no | no |
| Control qBittorrent | no | no | no | no |
| Delete, cancel, or scan | no | no | no | no |

## Identity and consistency model

TVDB ID is the stable cross-service series identity:

- TVmaze supplies it when a plan is created;
- Sonarr lookup and existing-series selection use it during apply and status;
- Jellyfin visibility first uses its TVDB provider metadata.

A plan stores TVmaze ID for provenance, TVDB ID for cross-service mapping, and season/episode coordinates for episode identity. Episode titles are review evidence and mismatch checks, not the mutation key. Jellyfin uses a unique normalised title/year fallback only when no TVDB match exists, the plan has a year, and no conflicting provider identity is present. Duplicate or inconsistent records fail rather than being guessed.

## Persistent data

Wit has two independent state roots:

1. **Compose data**, below `WIT_DATA_ROOT`:
   - `config/{qbittorrent,sonarr,jellyfin,seerr}` for service-owned databases, settings, users, and credentials;
   - `cache/jellyfin` for regenerable cache data;
   - `downloads` shared by qBittorrent and Sonarr at `/downloads`;
   - `television` writable by Sonarr and mounted read-only in Jellyfin at `/tv`.
2. **CLI state**, below `WIT_STATE_DIR` (default `${XDG_STATE_HOME:-~/.local/state}/wit`):
   - `plans/<plan-id>.json` for immutable, secret-free plans.

The protected CLI TOML is configuration, not state. It normally lives below `~/.config/wit` and contains the Sonarr and Jellyfin API keys. Compose `.env` is a separate, non-secret interpolation file and is never loaded by the CLI.

See [configuration](configuration.md) for exact variables and [operations](operations.md) for backup boundaries.

## Network topology

All web ports publish to `127.0.0.1` by default. The containers join:

- `wit-internal`, an internal bridge used for service-name communication; and
- `wit-egress`, a bridge that permits required outbound access.

Because every service joins both bridges, the networks are not a security boundary between these four containers. They primarily provide predictable addressing and keep administrative ports off non-loopback host interfaces. Sonarr reaches qBittorrent by service name, while Seerr reaches Sonarr and Jellyfin by service name. Host-run Wit uses the published loopback ports.

No reverse proxy, TLS termination, host networking, discovery ports, peer/listening port, or GPU device is enabled by default. See the [security model](security.md#network-exposure) before designing remote access.

## Failure and scaling boundaries

- HTTP connect and read timeouts are bounded; redirects and proxy settings inherited from the shell are not followed by the transport.
- Queue pagination is verified and bounded to 100 pages requested at 100 records per page. Jellyfin lookups are verified and bounded to 5,000 items per lookup. Inconsistent or excessive pagination fails instead of returning partial state.
- Plans are single-user files; Wit has no daemon, scheduler, lock service, multi-user tenancy, or hosted API.
- Apply idempotence is based on current Sonarr files and active queue records, not a separate Wit job database.
- Compose health checks prove that local HTTP endpoints respond. They do not validate authentication, authorised sources, library correctness, or successful acquisition.
- Exact image tags and `uv.lock` constrain updates, but operations and backup remain the operator's responsibility.

These boundaries are intentional for the pre-1.0, local-first release.