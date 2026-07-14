# Security Policy

## Supported versions

Wit is currently pre-release. Security fixes are made against the latest `main` branch. Supported release versions will be listed here after the first release.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting or the repository’s Security Advisory flow. If private reporting is unavailable, open a minimal public issue requesting a private maintainer contact; do not include exploit details, credentials, private URLs, media records, or other sensitive data.

Include, where safe:

* affected version or commit
* affected component
* likely impact
* minimal reproduction steps using synthetic data
* suggested mitigation, if known

## Security-sensitive areas

Reports are especially relevant for:

* API-key handling and redaction
* authentication headers and error messages
* plan-file permissions, integrity, and path traversal
* title/episode mismatch that could apply a request to the wrong series
* unintended broad monitoring or searching in Sonarr
* command injection or unsafe subprocess use
* Docker socket, filesystem, or network exposure
* public exposure of Sonarr, qBittorrent, Jellyfin, or Seerr
* secrets or private data entering logs, plans, test fixtures, or Pi sessions

## Project boundaries

Wit does not provide content sources, indexer presets, DRM circumvention, subscription-stream extraction, or public hosting. Reports that require adding real credentials, private media data, or unauthorised sources to reproduce should instead provide a synthetic minimal case.

## Operator responsibilities

Operators should:

* keep real `.env` and config files outside version control with restrictive permissions
* rotate credentials after suspected exposure
* bind administrative interfaces to trusted networks
* use authorised media sources
* review plans before applying them
* back up service configuration and media metadata before upgrades

Detailed threat modelling and operational guidance will be added during the documented build tickets.
