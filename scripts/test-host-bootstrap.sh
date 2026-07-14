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

assert_failure() {
  local output_file="$1"
  local expected="$2"
  local status
  shift 2

  set +e
  "$@" > "$output_file" 2>&1
  status=$?
  set -e

  if (( status == 0 )); then
    fail "Expected command to fail: $*"
  fi
  if ! grep -Fq -- "$expected" "$output_file"; then
    cat "$output_file" >&2
    fail "Failure output did not contain: $expected"
  fi
}

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

fixture="$tmp_dir/repository"
mkdir -p "$fixture/scripts/lib"
cp "$SCRIPT_DIR/bootstrap-host.sh" "$fixture/scripts/bootstrap-host.sh"
cp "$SCRIPT_DIR/lib/pretty-print.sh" "$fixture/scripts/lib/pretty-print.sh"
cp "$REPO_ROOT/.env.example" "$fixture/.env.example"
chmod +x "$fixture/scripts/bootstrap-host.sh"
bootstrap="$fixture/scripts/bootstrap-host.sh"

clean_environment=(env -u WIT_DATA_ROOT -u PUID -u PGID NO_COLOR=1)

pp_step "Regression: bootstrap creates the complete storage layout and protected .env"
data_root="$fixture/storage with spaces"
(
  umask 000
  "${clean_environment[@]}" bash "$bootstrap" \
    --data-root "$data_root" \
    --puid 1234 \
    --pgid 5678 \
    --copy-env
) > "$tmp_dir/success.log" 2>&1

expected_directories=(
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
for relative_directory in "${expected_directories[@]}"; do
  if [[ ! -d "$data_root/$relative_directory" ]]; then
    fail "Bootstrap omitted directory: $relative_directory"
  fi
done

if [[ ! -f "$fixture/.env" ]]; then
  fail "Bootstrap did not create the requested .env"
fi
if ! cmp -s "$fixture/.env.example" "$fixture/.env"; then
  fail "Generated .env differs from .env.example"
fi
if [[ "$(stat -c '%a' "$fixture/.env")" != "600" ]]; then
  fail "Generated .env permissions are not 600"
fi

pp_step "Regression: repeated setup preserves directories and an existing .env"
printf '%s\n' 'keep this directory content' > "$data_root/downloads/existing-file"
printf '%s\n' 'LOCAL_VALUE=preserve-me' > "$fixture/.env"
chmod 640 "$fixture/.env"
"${clean_environment[@]}" bash "$bootstrap" \
  --data-root "$data_root" \
  --puid 1234 \
  --pgid 5678 \
  --copy-env > "$tmp_dir/repeat.log" 2>&1

if [[ "$(< "$fixture/.env")" != "LOCAL_VALUE=preserve-me" ]]; then
  fail "Bootstrap overwrote an existing .env"
fi
if [[ "$(stat -c '%a' "$fixture/.env")" != "640" ]]; then
  fail "Bootstrap changed permissions on an existing .env"
fi
if [[ "$(< "$data_root/downloads/existing-file")" != "keep this directory content" ]]; then
  fail "Bootstrap changed existing directory content"
fi
grep -Fq 'Existing .env left unchanged.' "$tmp_dir/repeat.log" \
  || fail "Bootstrap did not report that the existing .env was preserved"

pp_step "Regression: environment values are accepted only after validation"
environment_root="$fixture/environment-root"
env \
  NO_COLOR=1 \
  WIT_DATA_ROOT="$environment_root" \
  PUID=2001 \
  PGID=2002 \
  bash "$bootstrap" > "$tmp_dir/environment.log" 2>&1
[[ -d "$environment_root/television" ]] \
  || fail "Bootstrap did not use a validated environment data root"

invalid_uid_root="$fixture/invalid-uid"
assert_failure "$tmp_dir/invalid-uid.log" 'PUID must be a positive decimal integer.' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root "$invalid_uid_root" --puid '12x' --pgid 1000
[[ ! -e "$invalid_uid_root" ]] \
  || fail "Bootstrap created data for an invalid UID"

invalid_gid_root="$fixture/invalid-gid"
assert_failure "$tmp_dir/invalid-gid.log" 'PGID must be between 1 and 4294967294.' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root "$invalid_gid_root" --puid 1000 --pgid 0
[[ ! -e "$invalid_gid_root" ]] \
  || fail "Bootstrap created data for an invalid GID"

pp_step "Regression: unsafe and malformed roots fail before directory creation"
assert_failure "$tmp_dir/empty-root.log" 'The data root must not be empty.' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root '' --puid 1000 --pgid 1000
assert_failure "$tmp_dir/filesystem-root.log" \
  'Refusing to use the filesystem root as the Wit data root.' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root / --puid 1000 --pgid 1000
assert_failure "$tmp_dir/repository-root.log" \
  'Refusing to use the repository itself as the Wit data root.' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root . --puid 1000 --pgid 1000

blocked_root="$fixture/blocked-root"
mkdir -p "$blocked_root"
printf '%s\n' 'not a directory' > "$blocked_root/downloads"
assert_failure "$tmp_dir/blocked-root.log" \
  'A required target exists but is not a directory:' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root "$blocked_root" --puid 1000 --pgid 1000
[[ ! -e "$blocked_root/config" ]] \
  || fail "Bootstrap partially created directories before validating every target"

escape_root="$fixture/escape-root"
escape_destination="$tmp_dir/outside-data-root"
mkdir -p "$escape_root" "$escape_destination"
ln -s "$escape_destination" "$escape_root/config"
assert_failure "$tmp_dir/escape-root.log" \
  'A target directory resolves outside the data root:' \
  "${clean_environment[@]}" bash "$bootstrap" \
  --data-root "$escape_root" --puid 1000 --pgid 1000
if find "$escape_destination" -mindepth 1 -print -quit | grep -q .; then
  fail "Bootstrap followed a target symlink outside the data root"
fi
[[ ! -e "$escape_root/downloads" ]] \
  || fail "Bootstrap created directories after finding an escaping target"

pp_step "Regression: .env symlinks are refused without creating host data"
env_symlink_fixture="$tmp_dir/env-symlink-repository"
mkdir -p "$env_symlink_fixture/scripts/lib"
cp "$SCRIPT_DIR/bootstrap-host.sh" "$env_symlink_fixture/scripts/bootstrap-host.sh"
cp "$SCRIPT_DIR/lib/pretty-print.sh" "$env_symlink_fixture/scripts/lib/pretty-print.sh"
cp "$REPO_ROOT/.env.example" "$env_symlink_fixture/.env.example"
printf '%s\n' 'outside env content' > "$tmp_dir/outside.env"
ln -s "$tmp_dir/outside.env" "$env_symlink_fixture/.env"
symlink_root="$env_symlink_fixture/data"
assert_failure "$tmp_dir/env-symlink.log" \
  'Refusing to use a symbolic link as .env.' \
  "${clean_environment[@]}" bash "$env_symlink_fixture/scripts/bootstrap-host.sh" \
  --data-root "$symlink_root" --puid 1000 --pgid 1000 --copy-env
[[ ! -e "$symlink_root" ]] \
  || fail "Bootstrap created directories before refusing an unsafe .env"
[[ "$(< "$tmp_dir/outside.env")" == "outside env content" ]] \
  || fail "Bootstrap changed the target of an .env symlink"

pp_success "Host bootstrap regressions passed."
