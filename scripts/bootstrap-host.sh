#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

fail() {
  pp_error "$*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: scripts/bootstrap-host.sh [options]

Create the host directories required by the Wit Compose stack.

Options:
  --data-root PATH  Data root (default: WIT_DATA_ROOT or ./data)
  --puid UID        Container user ID to validate (default: PUID or 1000)
  --pgid GID        Container group ID to validate (default: PGID or 1000)
  --copy-env        Copy .env.example to .env when .env does not exist
  -h, --help        Show this help

Relative data roots are resolved from the repository root. Existing directories
and an existing .env are left unchanged.
USAGE
}

validate_id() {
  local label="$1"
  local value="$2"
  local normalized
  local numeric

  if [[ -z "$value" || ! "$value" =~ ^[0-9]+$ ]]; then
    fail "$label must be a positive decimal integer."
  fi

  normalized="$value"
  while [[ ${#normalized} -gt 1 && "${normalized:0:1}" == "0" ]]; do
    normalized="${normalized:1}"
  done

  if [[ ${#normalized} -gt 10 ]]; then
    fail "$label is outside the supported numeric range."
  fi

  numeric=$((10#$normalized))
  if (( numeric < 1 || numeric > 4294967294 )); then
    fail "$label must be between 1 and 4294967294."
  fi
}

resolve_from_repo() {
  local path="$1"

  if [[ "$path" == /* ]]; then
    realpath -m -- "$path"
  else
    realpath -m -- "$REPO_ROOT/$path"
  fi
}

data_root_input="${WIT_DATA_ROOT-./data}"
puid="${PUID-1000}"
pgid="${PGID-1000}"
copy_env=0

while (( $# > 0 )); do
  case "$1" in
    --data-root)
      (( $# >= 2 )) || fail "--data-root requires a path."
      data_root_input="$2"
      shift 2
      ;;
    --data-root=*)
      data_root_input="${1#*=}"
      shift
      ;;
    --puid)
      (( $# >= 2 )) || fail "--puid requires a UID."
      puid="$2"
      shift 2
      ;;
    --puid=*)
      puid="${1#*=}"
      shift
      ;;
    --pgid)
      (( $# >= 2 )) || fail "--pgid requires a GID."
      pgid="$2"
      shift 2
      ;;
    --pgid=*)
      pgid="${1#*=}"
      shift
      ;;
    --copy-env)
      copy_env=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      (( $# == 0 )) || fail "Positional arguments are not supported."
      ;;
    -*)
      fail "Unknown option: $1"
      ;;
    *)
      fail "Unexpected positional argument: $1"
      ;;
  esac
done

validate_id "PUID" "$puid"
validate_id "PGID" "$pgid"

if [[ -z "$data_root_input" || ! "$data_root_input" =~ [^[:space:]] ]]; then
  fail "The data root must not be empty."
fi
if [[ "$data_root_input" == *$'\n'* || "$data_root_input" == *$'\r'* ]]; then
  fail "The data root must not contain line breaks."
fi
if ! command -v realpath >/dev/null 2>&1; then
  fail "The realpath command is required to validate the data root."
fi

root_candidate="$data_root_input"
if [[ "$root_candidate" != /* ]]; then
  root_candidate="$REPO_ROOT/$root_candidate"
fi
if [[ ( -e "$root_candidate" || -L "$root_candidate" ) && ! -d "$root_candidate" ]]; then
  fail "The data root exists but is not a directory: $data_root_input"
fi

existing_parent="$root_candidate"
while [[ ! -e "$existing_parent" && ! -L "$existing_parent" ]]; do
  parent="$(dirname -- "$existing_parent")"
  if [[ "$parent" == "$existing_parent" ]]; then
    break
  fi
  existing_parent="$parent"
done
if [[ ( -e "$existing_parent" || -L "$existing_parent" ) && ! -d "$existing_parent" ]]; then
  fail "A data-root parent exists but is not a directory: $data_root_input"
fi

data_root="$(resolve_from_repo "$data_root_input")" \
  || fail "The data root could not be resolved: $data_root_input"
case "$data_root" in
  /)
    fail "Refusing to use the filesystem root as the Wit data root."
    ;;
  "$REPO_ROOT")
    fail "Refusing to use the repository itself as the Wit data root."
    ;;
esac

relative_directories=(
  config
  config/qbittorrent
  config/sonarr
  config/jellyfin
  config/seerr
  cache
  cache/jellyfin
  downloads
  television
)
target_directories=()

for relative_directory in "${relative_directories[@]}"; do
  candidate="$data_root/$relative_directory"
  target="$(realpath -m -- "$candidate")" \
    || fail "A target directory could not be resolved: $candidate"

  if [[ "$target" != "$data_root/"* ]]; then
    fail "A target directory resolves outside the data root: $candidate"
  fi
  if [[ ( -e "$candidate" || -L "$candidate" ) && ! -d "$candidate" ]]; then
    fail "A required target exists but is not a directory: $candidate"
  fi

  target_directories+=("$target")
done

env_example="$REPO_ROOT/.env.example"
env_file="$REPO_ROOT/.env"
create_env=0
if (( copy_env == 1 )); then
  if [[ ! -f "$env_example" || -L "$env_example" ]]; then
    fail "Expected a regular .env.example at the repository root."
  fi
  if [[ -L "$env_file" ]]; then
    fail "Refusing to use a symbolic link as .env."
  elif [[ -e "$env_file" && ! -f "$env_file" ]]; then
    fail "The .env path exists but is not a regular file."
  elif [[ ! -e "$env_file" ]]; then
    create_env=1
  fi
fi

pp_banner "Wit host bootstrap"
pp_kv "Data root" "$data_root"
pp_kv "PUID" "$puid"
pp_kv "PGID" "$pgid"

for target_directory in "${target_directories[@]}"; do
  pp_step "Ensuring directory exists: $target_directory"
  mkdir -p -- "$target_directory"
done

if (( create_env == 1 )); then
  if ! (
    umask 077
    set -o noclobber
    exec 3> "$env_file"
    cat -- "$env_example" >&3
  ); then
    fail "Could not create .env without overwriting an existing path."
  fi
  chmod 600 -- "$env_file"
  pp_success "Created .env from .env.example with mode 600."
elif (( copy_env == 1 )); then
  pp_info "Existing .env left unchanged."
fi

pp_success "Host directories are ready; no containers were started."
