# Wit troubleshooting

Start with the supported read-only controls and move outward only as needed. This guide does not require reading API keys, dumping a real configuration file, calling service APIs directly, editing databases, cancelling queues, or deleting data.

See [configuration](configuration.md) for exact settings, [operations](operations.md) for lifecycle and recovery, [architecture](architecture.md) for ownership boundaries, and [security](security.md) before changing exposure or permissions.

## First diagnostic pass

From the active repository checkout:

```bash
docker compose config >/dev/null
docker compose ps
wit doctor
```

For a request-specific problem, also run:

```bash
wit status <plan-id>
```

Interpret these separately:

- **Compose health** checks whether each container's credential-free local HTTP endpoint responds.
- **`wit doctor`** validates complete CLI settings, state-directory access, authenticated Sonarr/Jellyfin health, and Seerr health.
- **`wit status`** reads one stored plan and current Sonarr/Jellyfin observations. `ACTIVE` means incomplete, not failed; `DEGRADED` means Sonarr was readable while Jellyfin was unavailable.

If a command is being consumed by automation, add `--json` and use its exit code. Do not publish the output without removing private show/request metadata and local topology.

When logs are necessary, inspect only a bounded tail locally:

```bash
SERVICE=sonarr
docker compose logs --tail=200 "$SERVICE"
```

Never share raw logs. qBittorrent logs can contain a temporary first-login password, and all service logs may reveal internal paths, media titles, or request history.

## Configuration or state-directory failures

### `invalid Wit configuration`

Every command currently validates the complete configuration, even when it uses only a subset. A planning request therefore still requires valid Sonarr, Jellyfin, and Seerr settings.

Check field names in the redacted error against [the runtime setting table](configuration.md#cli-runtime-settings). Common causes are:

- a required `WIT_*` value is missing;
- `WIT_CONFIG_FILE` is unset, relative, empty, or points to the wrong file;
- an environment override unintentionally supersedes a correct TOML value;
- a service URL lacks `http://` or `https://`, contains credentials/query/fragment, or uses an unreachable container-only hostname;
- a Sonarr root-folder or quality-profile ID is not a positive integer;
- timeout values are outside their documented bounds; or
- `WIT_STATE_DIR` is relative, a filesystem root, contains parent traversal, is a symlink, or is not a directory.

Do not print the configuration or environment to diagnose it. Review the protected file locally, by field name, and run `wit doctor` again.

### Protected TOML cannot be opened securely

The selected file must be a current-user-owned regular file, not a symlink, with mode exactly `0600` or `0400`. For the standard owner-only location:

```bash
chmod 700 "$HOME/.config/wit"
chmod 600 "$HOME/.config/wit/config.toml"
```

If ownership is wrong, have the host administrator correct only that file and its parent. Do not recursively make the configuration tree writable or readable by everyone. Network filesystems and platforms without compatible ownership semantics may not satisfy the supported security checks.

### State directory is missing or inaccessible

`wit doctor` never creates the state directory. Planning can create it while saving, but readiness checks expect it to exist. For the default location:

```bash
mkdir -p "$HOME/.local/state/wit"
chmod 700 "$HOME/.local/state/wit"
wit doctor
```

The current user needs read, write, and search access. The directory must be owned by that user for plan-store access and must not be group/other-writable. A custom state path must be absolute and non-symlink.

### Stored plan cannot be loaded

Typical safe rejections include an unsafe plan ID, missing file, symlink, non-regular file, wrong owner, group/other-writable file, malformed JSON, unsupported schema, or a plan ID that differs from its filename.

Do not rename or edit the JSON to make it pass. Preserve the rejected file privately for diagnosis and create a fresh plan from current TVmaze metadata. Restore an original plan only from a trusted backup with its ownership and permissions intact.

## Host and container permissions

The bootstrap helper creates directories but deliberately does not change ownership. The expected writers are:

| Host path | Expected access |
| --- | --- |
| `config/qbittorrent` | writable by configured `PUID`/`PGID` |
| `config/sonarr` | writable by configured `PUID`/`PGID` |
| `config/jellyfin` and `cache/jellyfin` | writable by configured `PUID`/`PGID` |
| `config/seerr` | writable by UID `1000` for the pinned Seerr image |
| `downloads` | writable by qBittorrent and readable/writable as needed by Sonarr, using the same `/downloads` container path |
| `television` | writable by Sonarr; readable by Jellyfin at its read-only `/tv` mount |

Check the intended identities in the private `.env`, then inspect only the relevant paths locally. A useful non-secret metadata check is:

```bash
stat -c '%a %u:%g %n' "$DATA_ROOT" "$DATA_ROOT/config" "$DATA_ROOT/television"
```

Set `DATA_ROOT` to the reviewed absolute data root first. If IDs differ from the host owner, arrange the precise ownership/group policy needed for those paths. Avoid `chmod -R 777`, broad ACL grants, or recursive ownership changes across an unverified variable. They can expose credentials and databases or alter media unexpectedly.

Common symptoms:

- **qBittorrent or Sonarr repeatedly restarts:** its config directory is not writable by `PUID`/`PGID`, or the selected IDs do not match the prepared tree.
- **Seerr loses setup or reports database errors:** `config/seerr` is not writable by UID `1000`.
- **Jellyfin cannot catalogue files:** the configured user/group cannot traverse the host data-root parents or read `television`.
- **Jellyfin cannot write beside media:** this is expected; `/tv` is intentionally read-only. Disable service options that require writing artwork or metadata into media folders.
- **Sonarr cannot import:** it lacks write access to `television`, lacks access to `downloads`, or the qBittorrent/Sonarr paths are inconsistent.

After a narrowly scoped correction, restart only the affected service and rerun `wit doctor`. Do not use a permission change to mask an incorrect data root.

## API authentication and URLs

### Sonarr or Jellyfin is `unauthorised`

A 401 or 403 health response is normalised as `unauthorised`. Confirm locally that:

- the URL identifies the intended service and any reverse-proxy path prefix is correct;
- host-run Wit uses `127.0.0.1` plus the published host port, not a Compose service name;
- the API key still exists in the owning service;
- the protected TOML field name is correct and no stale environment value overrides it; and
- Jellyfin setup is complete and the key is a dedicated API key rather than an administrator password.

Rotate a key if uncertain, update it without putting it on a command line, and rerun `wit doctor`. Do not ask anyone to paste the old or new value.

### Seerr is `unauthorised` or unhealthy

Wit sends no Seerr credential; it reads Seerr's status endpoint. An authorisation result usually means the configured URL or a reverse proxy blocks that endpoint. Use the host-published Seerr URL for host-run Wit and verify the first-run setup locally. Seerr also reports unhealthy when it requires a restart.

### Compose is healthy but doctor fails

Compose health checks are deliberately credential-free and narrower than doctor:

- Sonarr's `/ping` can respond while its authenticated health report has issues.
- Jellyfin's `/health` can respond before its setup/API state satisfies Wit.
- Seerr's public settings endpoint can respond while its status reports a required restart.
- qBittorrent has a Compose health check but no `wit doctor` client.

Trust the more specific failure and inspect that service's local dashboard. Do not disable authentication to make doctor pass.

### Service is unavailable or times out

Check, in order:

1. `docker compose ps` for running and health state;
2. the configured host URL and local port;
3. whether another process occupies the port;
4. whether a local firewall or proxy design interferes;
5. bounded service logs; and
6. the configured connect/read timeouts.

The HTTP transport intentionally ignores inherited proxy environment settings and does not follow redirects. A URL that relies on a redirect will fail; configure the final canonical base URL instead. Increase timeouts only for a measured local need and stay within documented bounds.

## Compose and path problems

### Bootstrap and Compose use different roots

`bootstrap-host.sh --data-root`, `--puid`, and `--pgid` validate and create directories; they do **not** rewrite `.env`. Put the same values in the ignored `.env` before `docker compose up`. A mismatch can make Compose use an empty second tree while the intended tree appears correctly prepared.

Render `docker compose config` locally and inspect only the resolved mount sources and port bindings. Do not publish the full rendered model if local paths are private.

### Sonarr root-folder or quality-profile default is rejected

Wit's settings require positive numeric API IDs, not `/tv` and not a profile name. Configure `/tv` and a quality profile in Sonarr first, then record their IDs using Sonarr's local UI and official documentation.

`wit doctor` validates only that the values are syntactically positive; the IDs are checked against Sonarr when Wit needs to add a new series. An existing series can therefore work while a later new-series apply rejects missing or inaccessible defaults. Correct the protected configuration and create a new plan only if request metadata itself needs to change.

### Sonarr and qBittorrent disagree about download paths

Both Compose services mount the same host `downloads` directory at `/downloads`. Configure qBittorrent to place Sonarr-managed downloads there and configure Sonarr's download client using:

- host `qbittorrent`;
- the qBittorrent Web UI container port selected by `QBITTORRENT_PORT`; and
- the locally created qBittorrent credentials.

Do not use `localhost` from Sonarr; that means the Sonarr container itself. A remote path mapping is normally unnecessary in this Compose layout. Use Sonarr's own connection test and keep qBittorrent under Sonarr's control for normal operations.

### Sonarr cannot see `/tv`

The Sonarr root folder must be the container path `/tv`, backed by `${WIT_DATA_ROOT}/television`. Confirm the mount is present in rendered Compose, the directory exists, parent directories are searchable, and Sonarr's configured user can write it. Do not configure a host path inside Sonarr.

### Port conflict

Change only the relevant host port in the ignored `.env`, rerender Compose, and update the corresponding `WIT_*_URL` used by the host-run CLI. qBittorrent's selected Web UI port is also its container Web UI port in this Compose file, so update the Sonarr download-client port to match. Ports remain bound to `127.0.0.1` regardless of their numbers.

## Unhealthy or restarting services

Use this order:

```bash
docker compose ps
wit doctor
SERVICE=jellyfin
docker compose logs --tail=200 "$SERVICE"
```

Then check the service-specific condition:

- **qBittorrent:** writable config/download paths and completion of first-login credential replacement. Its temporary password is sensitive.
- **Sonarr:** health issues in its dashboard, qBittorrent connectivity, `/downloads` and `/tv`, and manually configured authorised source health.
- **Jellyfin:** completed startup wizard, no pending restart, writable config/cache, and readable `/tv`.
- **Seerr:** writable config, completed setup, no pending restart, and reachable `http://sonarr:8989` / `http://jellyfin:8096` upstreams inside Compose.

A dependent service is not automatically restarted when an upstream later recovers. After fixing the upstream, restart the dependent only if its dashboard or logs show that recovery did not occur naturally.

If the service reports database corruption or an update migration failure, stop making changes and use the staged [restore procedure](operations.md#restore-procedure). Do not delete database, journal, lock, or migration files.

## Planning and apply safeguards

### Planning returns candidates

Wit did not find one safe deterministic match. Review every candidate and repeat the same request with the displayed TVmaze ID:

```bash
wit plan "Example Show" --first 4 --candidate 123
```

Do not pick by list position, guess a similarly named show, or broaden the query silently. `--year` can disambiguate exact/alias matches but cannot promote an inexact result. A show without a TVDB ID cannot be planned for later Sonarr mapping.

### Planning selects no episodes

The selector matched no currently aired, dated, regular episode. Wit excludes season zero, specials, future/unaired episodes, and undated episodes with no override. Check the intended selector and TVmaze metadata; do not substitute a different season or special.

### Apply rejects a stale plan

Plans older than seven 24-hour days are rejected before Sonarr access. Prefer creating a new plan from current metadata. Use `--allow-stale` only after re-reading the complete old plan and explicitly approving the age risk; it does not replace normal confirmation.

### Apply reports metadata differences

Current Sonarr series/episode titles materially differ from the stored plan. Read every displayed difference. A coordinate/title move can indicate changed numbering or the wrong series. Cancel and create a new plan unless the operator has independently verified the mapping. `--allow-mismatch` is a separate informed approval, not a routine retry flag.

### Apply failed after adding or monitoring

Apply is narrow but not transactional across Sonarr calls:

- a newly added series remains fully unmonitored if later coordinate mapping fails;
- monitoring can succeed before targeted search submission fails.

Inspect the exact safe error and the series in Sonarr. Do not repeat blindly: first run `wit status <plan-id>` and determine whether files, active queue records, rejected queue records, or monitored/missing episodes now exist. Repeating apply is idempotent for existing files and active queue records, but it cannot roll back a partial Sonarr call.

## Stalled searches and queues

`wit apply` submits a Sonarr `EpisodeSearch`; acceptance does not guarantee a release exists or a download/import will complete. Use:

```bash
wit status <plan-id>
```

Interpret episode states as follows:

| State | Meaning / next check |
| --- | --- |
| `planned` | Series/episode is not currently monitored or the series is absent; verify whether apply completed |
| `queued` | Sonarr queue work is waiting; ordinary incomplete state |
| `downloading` | Downloading or importing; allow service-owned work to continue |
| `imported` | Sonarr has a file; check the Jellyfin line |
| `missing` | Episode is monitored with no file or active queue; inspect Sonarr search/history and authorised-source health |
| `warning` | Matching queue record reports a warning; inspect it in Sonarr |
| `failed` | Queue, command, or mapping state is operationally failed; inspect the safe detail and Sonarr |

For a queue that does not progress:

1. inspect Sonarr's local Activity/Queue and Health pages;
2. use Sonarr's configured download-client test rather than calling qBittorrent directly;
3. check qBittorrent availability, free disk space, and the shared `/downloads` path;
4. check only the manually configured authorised source through Sonarr;
5. verify import permissions and `/tv` capacity; and
6. preserve warning/failure evidence before making a service-owned correction.

Wit intentionally does not cancel, remove, retry, or delete queue records. A repeat apply skips active records and rejects warning/failed records instead of creating duplicate work. Resolve the underlying condition in Sonarr or the owning service under human control.

If no source is configured or no authorised source has a matching result, health checks can pass and the episode can remain `missing`. That is an acquisition outcome, not a reason to add an unauthorised source.

## Missing Jellyfin items

First inspect `wit status <plan-id>`:

1. If Sonarr is not `imported`, troubleshoot acquisition/import before Jellyfin.
2. If Jellyfin is `unavailable`, run `wit doctor` and repair connectivity/authentication. Sonarr progress remains valid.
3. If the series is absent, confirm Jellyfin has a television library rooted at container path `/tv`, can read the host television tree, and has completed a library refresh.
4. If only the episode is absent, confirm Sonarr imported it under `/tv`, Jellyfin can read it, and its season/episode numbering matches the plan.
5. If status fails because matches are ambiguous, repair duplicate or conflicting Jellyfin series metadata rather than guessing.

Wit first matches a Jellyfin series by exact TVDB provider ID. Only when there is no such match and the plan has a year does it permit one unique normalised title/year fallback without a conflicting TVDB ID. Duplicate TVDB or fallback candidates fail safely. Large lookups exceeding the fixed 5,000-item bound also fail rather than truncate.

Jellyfin discovery can lag behind Sonarr import. Wait for the configured Jellyfin library schedule. Wit never triggers a scan. If an immediate refresh is operationally necessary, a human administrator may choose Jellyfin's documented library-refresh action in the local dashboard after verifying `/tv`; Pi must not invoke an undocumented API or assume that scan approval from the original media request.

Because `/tv` is read-only in Jellyfin, settings that try to save metadata or artwork beside media will fail. Keep service metadata in Jellyfin's writable config/cache locations.

## When to restore instead of repair

Stop and use [staged restore](operations.md#restore-procedure) when:

- a service database is corrupt;
- an image update performed an incompatible migration;
- a data root was partially copied or ownership was changed broadly;
- plan state was tampered with and no trusted original remains; or
- repeated restarts cannot produce a consistent service state.

Keep the failed tree for diagnosis. Do not overwrite the only copy, delete state to force a wizard, or downgrade an image against a migrated database without explicit upstream support.
