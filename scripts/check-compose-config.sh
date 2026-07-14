#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  pp_error "Docker Compose is required for the Compose configuration regression check."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  pp_error "Python 3 is required to inspect the rendered Compose configuration."
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
empty_env="$tmp_dir/empty.env"
rendered_config="$tmp_dir/compose.json"
: >"$empty_env"

pp_step "Rendering Compose with fallback defaults; no containers will be created or started."
pp_cmd "docker compose --env-file <empty> -f compose.yml config --format json"
env \
  -u WIT_DATA_ROOT \
  -u PUID \
  -u PGID \
  -u TZ \
  -u QBITTORRENT_PORT \
  -u SONARR_PORT \
  -u JELLYFIN_PORT \
  -u SEERR_PORT \
  docker compose \
    --project-directory "$REPO_ROOT" \
    --env-file "$empty_env" \
    -f "$REPO_ROOT/compose.yml" \
    config --format json >"$rendered_config"

python3 - "$rendered_config" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

config_path = Path(sys.argv[1])
config: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
errors: list[str] = []

expected_images = {
    "qbittorrent": "lscr.io/linuxserver/qbittorrent:5.2.1",
    "sonarr": "lscr.io/linuxserver/sonarr:4.0.16",
    "jellyfin": "jellyfin/jellyfin:10.11.11",
    "seerr": "ghcr.io/seerr-team/seerr:v3.3.0",
}
expected_ports = {
    "qbittorrent": (8080, "8080"),
    "sonarr": (8989, "8989"),
    "jellyfin": (8096, "8096"),
    "seerr": (5055, "5055"),
}
expected_dependencies = {
    "qbittorrent": set(),
    "sonarr": {"qbittorrent"},
    "jellyfin": set(),
    "seerr": {"jellyfin", "sonarr"},
}
forbidden_health_fragments = (
    "api-key",
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "password",
)

services = config.get("services")
if not isinstance(services, dict):
    errors.append("rendered configuration has no services mapping")
    services = {}

actual_service_names = set(services)
expected_service_names = set(expected_images)
if actual_service_names != expected_service_names:
    errors.append(
        "service set differs: "
        f"expected {sorted(expected_service_names)}, got {sorted(actual_service_names)}"
    )

for name, expected_image in expected_images.items():
    service = services.get(name)
    if not isinstance(service, dict):
        continue

    if service.get("image") != expected_image:
        errors.append(f"{name}: image must remain pinned to {expected_image}")
    if service.get("restart") != "unless-stopped":
        errors.append(f"{name}: restart policy must be unless-stopped")

    security_options = service.get("security_opt", [])
    if "no-new-privileges:true" not in security_options:
        errors.append(f"{name}: no-new-privileges is missing")

    healthcheck = service.get("healthcheck")
    if not isinstance(healthcheck, dict):
        errors.append(f"{name}: health check is missing")
    else:
        test = healthcheck.get("test")
        if not isinstance(test, list) or len(test) < 2:
            errors.append(f"{name}: health-check command is missing")
        else:
            health_command = " ".join(str(part) for part in test).lower()
            if "127.0.0.1" not in health_command and "localhost" not in health_command:
                errors.append(f"{name}: health check must probe the container loopback interface")
            for fragment in forbidden_health_fragments:
                if fragment in health_command:
                    errors.append(f"{name}: health check appears to contain authentication data")
                    break
        for duration_key in ("interval", "timeout", "start_period"):
            if duration_key not in healthcheck:
                errors.append(f"{name}: health check has no {duration_key}")
        if not isinstance(healthcheck.get("retries"), int) or healthcheck["retries"] < 1:
            errors.append(f"{name}: health check must have at least one retry")

    ports = service.get("ports", [])
    if not isinstance(ports, list):
        errors.append(f"{name}: ports must be a list")
        ports = []
    for port in ports:
        if not isinstance(port, dict) or port.get("host_ip") != "127.0.0.1":
            errors.append(f"{name}: every published port must bind to 127.0.0.1")

    expected_target, expected_published = expected_ports[name]
    if not any(
        isinstance(port, dict)
        and port.get("target") == expected_target
        and port.get("published") == expected_published
        and port.get("host_ip") == "127.0.0.1"
        for port in ports
    ):
        errors.append(
            f"{name}: expected localhost port mapping "
            f"127.0.0.1:{expected_published}:{expected_target} is missing"
        )

    service_networks = service.get("networks", {})
    if not isinstance(service_networks, dict) or set(service_networks) != {"internal", "egress"}:
        errors.append(f"{name}: service must join only the internal and egress networks")

    dependencies = service.get("depends_on", {})
    if not isinstance(dependencies, dict):
        errors.append(f"{name}: depends_on must be a mapping")
        dependencies = {}
    actual_dependencies = set(dependencies)
    if actual_dependencies != expected_dependencies[name]:
        errors.append(
            f"{name}: dependency set differs: "
            f"expected {sorted(expected_dependencies[name])}, got {sorted(actual_dependencies)}"
        )
    for dependency_name, dependency in dependencies.items():
        if not isinstance(dependency, dict) or dependency.get("condition") != "service_healthy":
            errors.append(f"{name}: dependency {dependency_name} must wait for service health")

networks = config.get("networks")
internal_network = networks.get("internal") if isinstance(networks, dict) else None
if not isinstance(internal_network, dict) or internal_network.get("internal") is not True:
    errors.append("internal network must retain internal: true")

if errors:
    print("Compose configuration regression check failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    raise SystemExit(1)
PY

pp_success "Compose configuration regression check passed."
