# Wit operations

This is the maintainer and host-operator runbook for an already configured Wit installation. Use the [README first-run guide](../README.md#first-run-guide) for initial provisioning and [configuration](configuration.md) for every setting.

Docker Compose lifecycle commands in this document are host-administration tasks for a human operator. Pi and other automation must use `wit doctor`, `wit plan`, `wit apply`, and `wit status` for media requests rather than substituting raw service APIs, direct database changes, qBittorrent commands, or ad hoc container operations.

## Operating principles

- Run repository commands from the checkout containing the active `compose.yml`.
- Keep the repository commit, exact image tags, local `.env`, `WIT_DATA_ROOT`, protected Wit configuration, and `WIT_STATE_DIR` identifiable as one installation.
- Validate Compose before every lifecycle change.
- Prefer one reviewed service change at a time.
- Take a coherent backup before an image update, path change, ownership change, or service-database recovery.
- Never delete media, queue state, service databases, or the old data root as a recovery shortcut.
- Do not expose the administrative ports beyond loopback without a separately reviewed access design.

## Routine startup

For a stack whose first-run wizards and integrations are complete:

```bash
docker compose config >/dev/null
docker compose up -d
docker compose ps
wit doctor
```

Compose waits for qBittorrent health before starting Sonarr, and for Sonarr and Jellyfin health before starting Seerr. These conditions order initial container creation only; they do not continuously supervise dependencies after startup.

A container can be `healthy` while an API key, root folder, download client, authorised source, or library is misconfigured. `wit doctor` adds authenticated application checks for Sonarr and Jellyfin plus Seerr and local-state checks, but it still does not validate qBittorrent or acquisition sources. Resolve every required doctor failure before planning an operation that depends on that component.

Use `wit status <plan-id>` for an existing request. An `ACTIVE` or `DEGRADED` result is a successful observation, not by itself a reason to restart the stack.

## Routine shutdown

Allow an in-progress `wit apply` process to return before maintenance. The Sonarr search it submits is asynchronous, so also review relevant plan status and the local Sonarr activity view before deciding when to interrupt downloads or imports.

Gracefully stop all containers while retaining their bind-mounted data:

```bash
docker compose stop
docker compose ps
```

`docker compose stop` is the normal shutdown operation. A later `docker compose up -d` resumes the stack. If container and network objects must also be recreated, plain `docker compose down` leaves this project's bind-mounted data in place, but it is unnecessary for routine shutdown. Do not request volume removal and do not remove directories below `WIT_DATA_ROOT`.

After an unplanned host shutdown, start normally and check:

```bash
docker compose up -d
docker compose ps
wit doctor
```

Then run `wit status <plan-id>` for affected plans. Let Sonarr and its configured download client reconcile their own queue and import state; Wit does not repair those databases directly.

## Observability and safe evidence collection

Use the narrowest source that answers the question:

1. `wit doctor` for configuration, state-directory access, authentication, and service health.
2. `wit status <plan-id>` for plan-specific Sonarr progress and Jellyfin visibility.
3. `docker compose ps` for container and health-check state.
4. A service's local dashboard for service-owned settings and queue details.
5. `docker compose logs --tail=200 "$SERVICE"` locally, with `SERVICE` set to one known Compose service, when the preceding checks are insufficient.

Logs are sensitive operational data. qBittorrent logs can contain its first-login temporary password; other logs can contain internal URLs, request history, filenames, or service-generated diagnostics. Do not paste raw logs into an issue, Pi session, or chat. Extract the minimum relevant lines and redact credentials, private paths, hostnames, media titles, and download history before sharing.

Machine consumers should use `--json` with Wit commands. The JSON is credential-safe by design but can still reveal plan IDs, show titles, episode selections, service versions, and operational state; treat it as private user data.

## Backup policy

### What must be backed up

A complete recovery has distinct layers:

| Layer | Location | Contents and sensitivity |
| --- | --- | --- |
| Deployment definition | reviewed repository commit, `compose.yml`, `uv.lock`, and local `.env` | Version and host topology; `.env` should be non-secret but remains machine-specific |
| Service control data | `${WIT_DATA_ROOT}/config/` | Service databases, users, API keys, qBittorrent state, source credentials, request records; **secret-bearing** |
| Wit configuration | protected file selected by `WIT_CONFIG_FILE`, if used | Sonarr and Jellyfin API keys; **secret-bearing** |
| Wit plan state | `WIT_STATE_DIR`, normally an XDG state path | Secret-free plans, but private viewing/request metadata |
| Media payload | `${WIT_DATA_ROOT}/downloads/` and `${WIT_DATA_ROOT}/television/` | Potentially large, private data needed for full content recovery |
| Regenerable cache | `${WIT_DATA_ROOT}/cache/jellyfin/` | Optional; may be omitted if a slower Jellyfin cache rebuild is acceptable |

A control-plane backup without `downloads` and `television` can restore configuration and request intent, but not media. A media-only backup does not restore service databases, identities, users, or Wit plans. Define both retention policies explicitly.

Store backups outside `WIT_DATA_ROOT`, restrict access at least as tightly as the protected configuration, and encrypt backup media that leaves the host. Keep checksums and the repository commit/image-tag manifest with the backup. Do not upload an archive to an untrusted service merely to test it.

### Coherent offline backup procedure

Service databases can change while containers run. Use a stopped-stack snapshot when backup consistency matters:

1. Record, privately, the repository commit, exact Compose image tags, configured user/group IDs (`PUID`/`PGID`), timezone, host ports, data-root path, Wit config-file path, and Wit state path. Do not record credential values.
2. Confirm the backup destination is outside the live data root, has enough capacity, and is owner-restricted.
3. Let any executing `wit apply` invocation finish. Note which requests are still active with `wit status`.
4. Run `docker compose stop` and verify all four services are stopped with `docker compose ps`.
5. Copy the selected layers with a trusted local backup tool that preserves numeric ownership, modes, timestamps, ACLs, and extended attributes. Do not follow symlinks out of the selected roots.
6. Verify that the backup can be listed/read, calculate a checksum, and confirm expected top-level directories without printing file contents.
7. Restart with `docker compose up -d`, then run `docker compose ps` and `wit doctor`.

For GNU `tar`, this is a safe shape for a private service-config archive after an operator has set `DATA_ROOT` and `BACKUP_DIR` to reviewed absolute paths. These names are shell-local examples; do not source the repository `.env` as shell code and do not paste placeholder paths unchanged:

```bash
umask 077
mkdir -p -- "$BACKUP_DIR"
tar --create --acls --xattrs --numeric-owner \
  --file "$BACKUP_DIR/service-config.tar" \
  --directory "$DATA_ROOT" config
tar --list --file "$BACKUP_DIR/service-config.tar" >/dev/null
sha256sum "$BACKUP_DIR/service-config.tar" >"$BACKUP_DIR/service-config.tar.sha256"
```

Create separate archives for the Wit state and, if included, `downloads` and `television`, so retention and restore decisions remain explicit. Copy the protected Wit TOML without rendering it and retain mode `0600` or `0400`. If the backup tool cannot read service-owned files, arrange narrowly scoped host-administrator access; do not make the live tree world-readable.

A backup is not proven until a restore rehearsal has succeeded against an isolated, non-public test location using synthetic or appropriately protected data.

## Restore procedure

Restore to a fresh staging location rather than overwriting the only live copy. This makes rollback possible and avoids mixing database generations.

1. Keep the current stack stopped and preserve its data root unchanged.
2. Verify archive checksums and inspect archive path names before extraction. Restore only a trusted archive; reject absolute paths, parent traversal, unexpected symlinks, and unexpected device files.
3. Check out the recorded repository commit and use the exact image tags from the backup first. Do not combine a restored older database with a newer image until upstream migration guidance has been reviewed.
4. Extract the service configuration—and media, if part of the recovery—under a new approved `WIT_DATA_ROOT`. Preserve numeric ownership and permissions.
5. Restore the protected Wit configuration to a new owner-only regular file with mode `0600` or `0400`. Restore `WIT_STATE_DIR` as a current-user-owned, non-symlink directory; its plan files should remain private.
6. Set the untracked Compose `.env` to the staged data root and the recorded `PUID`, `PGID`, timezone, and ports. Select the restored protected TOML with `WIT_CONFIG_FILE`.
7. Confirm the required layout and ownership against [configuration](configuration.md#compose-env-settings), then render the model:

   ```bash
   docker compose config >/dev/null
   ```

8. Start the restored stack, preserving the normal dependency order:

   ```bash
   docker compose up -d
   docker compose ps
   wit doctor
   ```

9. Use `wit status <plan-id>` for representative restored plans. Confirm Sonarr sees imported files and Jellyfin reports expected visibility; do not infer request completion solely from filesystem contents.
10. Keep the former data root and backup unchanged until the restored installation has passed service-specific checks and an operator-defined observation period.

If ownership cannot be preserved, stop before startup and repair only the exact staged paths according to the identities in `.env`: qBittorrent and Sonarr use `PUID`/`PGID`, Jellyfin runs as that same pair, and the pinned Seerr image writes its config as UID `1000`. Avoid blanket recursive permission broadening.

## Updating

Images are constrained to exact release tags, and Python dependencies are locked. Updates are deliberate; there is no unattended updater.

### Service image update

1. Read the upstream release and security notes, including database migration and rollback statements.
2. Take and verify a stopped-stack backup.
3. Change only one service's exact image tag in `compose.yml`.
4. Run the repository quality gate, which renders Compose without starting containers:

   ```bash
   scripts/quality-gate.sh
   ```

5. Pull and recreate only the reviewed service. Set `SERVICE` to one of `qbittorrent`, `sonarr`, `jellyfin`, or `seerr`:

   ```bash
   docker compose pull "$SERVICE"
   docker compose up -d "$SERVICE"
   docker compose ps "$SERVICE"
   wit doctor
   ```

6. Exercise the affected read-only path and observe it before updating another service. For example, use `wit status` after a Jellyfin or Sonarr update.
7. Commit the reviewed tag change if this checkout is the installation source of record.

An exact tag is version-constrained but not a content-addressed digest; registry integrity and upstream image provenance remain trust assumptions.

### CLI/source update

Review the source changes and lockfile before selecting a new commit. In that reviewed checkout:

```bash
uv sync --locked --all-groups
scripts/quality-gate.sh
uv run wit --version
```

If `wit` was installed with `uv tool install .`, reinstall it from the same reviewed checkout after validation. Preserve the old checkout or commit reference until plan loading and the read-only commands have been verified.

### Rollback after an update

Do not point an older image at a database that a newer image may have migrated. If upstream explicitly supports an in-place downgrade, follow its documented compatibility procedure. Otherwise:

1. stop the affected stack;
2. stage the verified pre-update backup in a new data root;
3. restore the matching old repository commit and exact image tags;
4. validate and start the staged restoration; and
5. retain the failed-upgrade data separately for diagnosis.

This is a restore, not a blind tag reversal.

## Non-destructive recovery ladder

When service operation is impaired:

1. Preserve the exact error and time without capturing credentials or private payloads.
2. Run `wit doctor`; for a plan-specific symptom, also run `wit status <plan-id>`.
3. Run `docker compose ps` and inspect only the affected service's recent local logs.
4. Check disk capacity, mount availability, host ownership, configured URLs, and port conflicts.
5. Correct configuration through the owning service's local UI or protected Wit configuration, then retest.
6. Restart only the affected service when configuration requires it:

   ```bash
   docker compose restart "$SERVICE"
   docker compose ps "$SERVICE"
   ```

7. If corruption or an incompatible migration is suspected, stop and use the staged [restore procedure](#restore-procedure).

Do not edit SQLite databases, remove lock/database files, cancel Sonarr queue records, delete qBittorrent state, force a Jellyfin scan through an undocumented API, or recursively reset permissions as a first response. See [troubleshooting](troubleshooting.md) for symptom-specific checks.

## Periodic operator checklist

- Run `wit doctor` after host, network, credential, or service changes.
- Review `docker compose ps` for health and unexpected restarts.
- Confirm free capacity for configuration databases, downloads, media, and backups.
- Verify backups and periodically rehearse a staged restore.
- Review upstream release/security notes without enabling unattended updates.
- Rotate credentials after suspected disclosure and update only protected locations.
- Review active plans with `wit status`; do not treat incomplete downloads as operational failures.
- Keep services loopback-bound unless a separate remote-access threat model is approved.
- Keep only authorised acquisition sources configured in Sonarr.
