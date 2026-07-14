#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

fail() {
  pp_error "$*"
  exit 1
}

assert_contains() {
  local file="$1"
  local expected="$2"
  local message="$3"

  grep -Fq -- "$expected" "$file" || fail "$message"
}

assert_not_contains() {
  local file="$1"
  local unexpected="$2"
  local message="$3"

  if grep -Fq -- "$unexpected" "$file"; then
    fail "$message"
  fi
}

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

runtime_tmp="$tmp_dir/runtime"
mkdir -p "$runtime_tmp"

cat > "$tmp_dir/fake-pi" <<'FAKE_PI'
#!/usr/bin/env bash
set -euo pipefail

: "${CODEX_RATE_HEADER_LOG:?rate header log is required}"
: "${FAKE_CODEX_ARGS_LOG:?argument log is required}"
: "${FAKE_CODEX_MODE:?fixture mode is required}"

printf '%s\n' "$@" > "$FAKE_CODEX_ARGS_LOG"
grep -Fq '"transport": "sse"' .pi/settings.json

case "$FAKE_CODEX_MODE" in
  limited)
    cat > "$CODEX_RATE_HEADER_LOG" <<'JSON'
{"status":429,"headers":{"x-codex-active-limit":"premium","x-codex-plan-type":"prolite","x-codex-primary-used-percent":"100","x-codex-primary-window-minutes":"300","x-codex-primary-reset-after-seconds":"1497","x-codex-primary-reset-at":"1893456000","x-codex-secondary-used-percent":"29","x-codex-secondary-window-minutes":"10080","x-codex-secondary-reset-after-seconds":"570287","x-codex-secondary-reset-at":"1894060800"}}
JSON
    printf '%s\n' 'You have hit your ChatGPT usage limit.' >&2
    exit 1
    ;;
  available)
    cat > "$CODEX_RATE_HEADER_LOG" <<'JSON'
{"status":200,"headers":{"x-codex-active-limit":"premium","x-codex-plan-type":"prolite","x-codex-primary-used-percent":"42","x-codex-primary-window-minutes":"300","x-codex-primary-reset-after-seconds":"3600","x-codex-primary-reset-at":"1893456000","x-codex-secondary-used-percent":"29","x-codex-secondary-window-minutes":"10080","x-codex-secondary-reset-after-seconds":"570287","x-codex-secondary-reset-at":"1894060800"}}
JSON
    printf '%s\n' 'OK'
    ;;
  inactive-secondary)
    cat > "$CODEX_RATE_HEADER_LOG" <<'JSON'
{"status":200,"headers":{"x-codex-active-limit":"premium","x-codex-plan-type":"prolite","x-codex-primary-used-percent":"35","x-codex-primary-window-minutes":"10080","x-codex-primary-reset-after-seconds":"561600","x-codex-primary-reset-at":"1784504839","x-codex-secondary-used-percent":"0","x-codex-secondary-window-minutes":"0","x-codex-secondary-reset-after-seconds":"0","x-codex-secondary-reset-at":"0"}}
JSON
    printf '%s\n' 'OK'
    ;;
  missing)
    printf '%s\n' 'Codex error: fixture unavailable' >&2
    exit 23
    ;;
  *)
    printf 'Unknown fixture mode: %s\n' "$FAKE_CODEX_MODE" >&2
    exit 2
    ;;
esac
FAKE_PI
chmod +x "$tmp_dir/fake-pi"

common_env=(
  "NO_COLOR=1"
  "TMPDIR=$runtime_tmp"
  "PI_CODEX_USAGE_COMMAND=$tmp_dir/fake-pi"
  "FAKE_CODEX_ARGS_LOG=$tmp_dir/fake-args.log"
)

pp_step "Regression: Codex usage reports the limiting five-hour window"
env "${common_env[@]}" FAKE_CODEX_MODE=limited \
  bash "$SCRIPT_DIR/codex-usage.sh" > "$tmp_dir/limited.log" 2>&1
assert_contains "$tmp_dir/limited.log" 'Codex usage' \
  "Codex usage omitted its heading"
assert_contains "$tmp_dir/limited.log" 'Primary (5 hours): 100% used' \
  "Codex usage omitted the exhausted primary window"
assert_contains "$tmp_dir/limited.log" 'Secondary (7 days): 29% used' \
  "Codex usage omitted the weekly percentage"
assert_contains "$tmp_dir/limited.log" \
  'The 5-hour window is exhausted; the weekly window has remaining capacity.' \
  "Codex usage misclassified the limiting window"
for required_arg in --approve --no-session --no-context-files --no-tools --print; do
  grep -Fxq -- "$required_arg" "$tmp_dir/fake-args.log" \
    || fail "Codex usage omitted safety argument $required_arg"
done

pp_step "Regression: Codex usage reports available capacity"
env "${common_env[@]}" FAKE_CODEX_MODE=available \
  bash "$SCRIPT_DIR/codex-usage.sh" > "$tmp_dir/available.log" 2>&1
assert_contains "$tmp_dir/available.log" 'HTTP status:          200' \
  "Codex usage omitted the successful provider status"
assert_contains "$tmp_dir/available.log" 'Primary (5 hours): 42% used' \
  "Codex usage omitted available primary usage"
assert_contains "$tmp_dir/available.log" 'Codex is currently available.' \
  "Codex usage misclassified available capacity"

pp_step "Regression: Codex usage ignores an inactive secondary-window sentinel"
env "${common_env[@]}" FAKE_CODEX_MODE=inactive-secondary \
  bash "$SCRIPT_DIR/codex-usage.sh" > "$tmp_dir/inactive-secondary.log" 2>&1
assert_contains "$tmp_dir/inactive-secondary.log" 'Primary (7 days): 35% used' \
  "Codex usage omitted the active weekly window"
assert_contains "$tmp_dir/inactive-secondary.log" 'Secondary:            not active' \
  "Codex usage did not classify the zero-length secondary window as inactive"
assert_not_contains "$tmp_dir/inactive-secondary.log" 'Secondary reset:' \
  "Codex usage printed a reset for an inactive secondary window"
assert_not_contains "$tmp_dir/inactive-secondary.log" '1970' \
  "Codex usage rendered the zero reset sentinel as an epoch timestamp"

pp_step "Regression: Codex usage fails clearly when headers are unavailable"
set +e
env "${common_env[@]}" FAKE_CODEX_MODE=missing \
  bash "$SCRIPT_DIR/codex-usage.sh" > "$tmp_dir/missing.log" 2>&1
missing_status=$?
set -e
if [[ "$missing_status" -eq 0 ]]; then
  fail "Codex usage succeeded without rate-limit headers"
fi
assert_contains "$tmp_dir/missing.log" \
  'Pi did not expose Codex rate-limit headers (exit status 23).' \
  "Codex usage did not explain missing headers"

if find "$runtime_tmp" -mindepth 1 -print -quit | grep -q .; then
  fail "Codex usage left temporary diagnostic files behind"
fi

pp_success "Codex usage regressions passed."
