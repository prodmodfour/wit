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

wait_for_file() {
  local file="$1"
  local attempts="${2:-200}"

  while (( attempts > 0 )); do
    if [[ -f "$file" ]]; then
      return 0
    fi
    sleep 0.05
    attempts=$((attempts - 1))
  done

  return 1
}

wait_for_text() {
  local file="$1"
  local expected="$2"
  local attempts="${3:-200}"

  while (( attempts > 0 )); do
    if [[ -f "$file" ]] && grep -Fq "$expected" "$file"; then
      return 0
    fi
    sleep 0.05
    attempts=$((attempts - 1))
  done

  return 1
}

create_fixture() {
  local work_dir="$1"

  git init -q "$work_dir"

  (
    cd "$work_dir"

    git config user.name "Build Loop Test"
    git config user.email "build-loop-test@example.invalid"

    mkdir -p scripts/lib
    cp "$REPO_ROOT/scripts/build-loop.sh" scripts/build-loop.sh
    cp "$REPO_ROOT/scripts/build-loop-follow.sh" scripts/build-loop-follow.sh
    cp "$REPO_ROOT/scripts/build-loop-stop.sh" scripts/build-loop-stop.sh
    cp "$REPO_ROOT/scripts/lib/pretty-print.sh" scripts/lib/pretty-print.sh
    cp "$REPO_ROOT/scripts/lib/git-branch.sh" scripts/lib/git-branch.sh
    cp "$REPO_ROOT/scripts/lib/pull-request.sh" scripts/lib/pull-request.sh
    cp "$REPO_ROOT/scripts/lib/build-loop-state.sh" scripts/lib/build-loop-state.sh

    cat > scripts/quality-gate.sh <<'QUALITY_GATE'
#!/usr/bin/env bash
set -euo pipefail
exit 0
QUALITY_GATE

    cat > scripts/run-agent.sh <<'RUN_AGENT'
#!/usr/bin/env bash
set -euo pipefail
printf 'scenario must replace run-agent.sh\n' >&2
exit 99
RUN_AGENT

    chmod +x scripts/*.sh scripts/lib/*.sh

    cat > AGENTS.md <<'AGENTS'
# AGENTS.md

Test fixture.
AGENTS

    cat > PROJECT_BRIEF.md <<'PROJECT_BRIEF'
# PROJECT_BRIEF.md

TEMPLATE_CUSTOMISED: true
PROJECT_BRIEF

    cat > BUILD_TICKETS.md <<'BUILD_TICKETS'
# BUILD_TICKETS.md

AUTOMATION_STATUS: NOT_DONE

## 001 — Graceful control fixture

Status: TODO
BUILD_TICKETS

    git add .
    git commit -q -m "test: initial fixture"
  )
}

tmp_dir="$(mktemp -d)"
cleanup() {
  if [[ -n "${fake_loop_pid:-}" ]]; then
    kill "$fake_loop_pid" 2>/dev/null || true
    wait "$fake_loop_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

pp_step "Regression: graceful stop finishes the active cycle and starts no next cycle"
success_work="$tmp_dir/success-work"
success_state="$tmp_dir/success-state"
success_output="$tmp_dir/success-loop.log"
stop_output="$tmp_dir/stop.log"
create_fixture "$success_work"

cat > "$success_work/scripts/run-agent.sh" <<'RUN_AGENT'
#!/usr/bin/env bash
set -euo pipefail

: "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:?state directory is required}"
mkdir -p "$AUTONOMOUS_BUILD_LOOP_STATE_DIR"
count_file="$AUTONOMOUS_BUILD_LOOP_STATE_DIR/invocation-count"
count=0
if [[ -f "$count_file" ]]; then
  IFS= read -r count < "$count_file"
fi
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
touch "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/agent-started"

while [[ ! -f "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/allow-agent-finish" ]]; do
  sleep 0.05
done

printf 'Completed graceful-stop fixture cycle.\n'
printf '\nCompleted graceful-stop fixture cycle.\n' >> WORK_LOG.md
git add WORK_LOG.md
git commit -q -m "test: complete graceful stop fixture"
RUN_AGENT
chmod +x "$success_work/scripts/run-agent.sh"
(
  cd "$success_work"
  git add scripts/run-agent.sh
  git commit -q -m "test: configure successful stop agent"
)

(
  cd "$success_work"
  NO_COLOR=1 \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$success_state" \
    bash scripts/build-loop.sh --max-cycles 2 --no-push
) > "$success_output" 2>&1 &
success_loop_job=$!

wait_for_file "$success_state/agent-started" \
  || fail "successful stop fixture agent did not start"

(
  cd "$success_work"
  NO_COLOR=1 \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$success_state" \
    bash scripts/build-loop-stop.sh
) > "$stop_output" 2>&1

touch "$success_state/allow-agent-finish"

if ! wait "$success_loop_job"; then
  cat "$success_output" >&2
  fail "build loop returned an error after a graceful stop"
fi

if [[ "$(< "$success_state/invocation-count")" != "1" ]]; then
  fail "graceful stop allowed another agent cycle to start"
fi
grep -Fq 'Cycle 1/2 finished; graceful stop requested.' "$success_output" \
  || fail "build loop did not report its graceful cycle-boundary stop"
grep -Fq 'Detailed agent activity is streaming to the follow log' "$success_output" \
  || fail "build-loop launcher did not direct the operator to just follow"
if grep -Fq 'Completed graceful-stop fixture cycle.' "$success_output"; then
  fail "build-loop launcher exposed detailed agent output"
fi
grep -Fq 'Graceful stop requested for build loop PID' "$stop_output" \
  || fail "stop command did not confirm the graceful request"
if [[ -d "$success_state/lock" ]]; then
  fail "build-loop lock remained after graceful stop"
fi
if [[ ! -f "$success_state/current.log" ]]; then
  fail "build loop did not create the stable launcher log"
fi
if [[ ! -f "$success_state/follow.log" ]]; then
  fail "build loop did not create the stable follow log"
fi
if grep -Fq 'Completed graceful-stop fixture cycle.' "$success_state/current.log"; then
  fail "stable launcher log exposed detailed agent output"
fi
grep -Fq 'Completed graceful-stop fixture cycle.' "$success_state/follow.log" \
  || fail "stable follow log omitted detailed agent output"

(
  cd "$success_work"
  NO_COLOR=1 \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$success_state" \
    bash scripts/build-loop-stop.sh
) > "$tmp_dir/no-active-stop.log" 2>&1
grep -Fq 'No active build loop was found' "$tmp_dir/no-active-stop.log" \
  || fail "stop command was not idempotent after loop exit"

pp_step "Regression: graceful stop prevents a failed attempt from retrying"
failure_work="$tmp_dir/failure-work"
failure_state="$tmp_dir/failure-state"
failure_output="$tmp_dir/failure-loop.log"
create_fixture "$failure_work"

cat > "$failure_work/scripts/run-agent.sh" <<'RUN_AGENT'
#!/usr/bin/env bash
set -euo pipefail

: "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:?state directory is required}"
mkdir -p "$AUTONOMOUS_BUILD_LOOP_STATE_DIR"
count_file="$AUTONOMOUS_BUILD_LOOP_STATE_DIR/invocation-count"
count=0
if [[ -f "$count_file" ]]; then
  IFS= read -r count < "$count_file"
fi
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
touch "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/agent-started"

while [[ ! -f "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/allow-agent-finish" ]]; do
  sleep 0.05
done

printf 'temporary provider failure\n' >&2
exit 1
RUN_AGENT
chmod +x "$failure_work/scripts/run-agent.sh"
(
  cd "$failure_work"
  git add scripts/run-agent.sh
  git commit -q -m "test: configure failed stop agent"
)

(
  cd "$failure_work"
  NO_COLOR=1 \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$failure_state" \
  AUTONOMOUS_BUILD_RETRY_SECONDS=30 \
    bash scripts/build-loop.sh --max-cycles 2 --no-push
) > "$failure_output" 2>&1 &
failure_loop_job=$!

wait_for_file "$failure_state/agent-started" \
  || fail "failed-attempt stop fixture agent did not start"

(
  cd "$failure_work"
  NO_COLOR=1 \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$failure_state" \
    bash scripts/build-loop-stop.sh
) > "$tmp_dir/failure-stop.log" 2>&1

touch "$failure_state/allow-agent-finish"

if ! wait "$failure_loop_job"; then
  cat "$failure_output" >&2
  fail "build loop returned an error while gracefully stopping after a failed attempt"
fi

if [[ "$(< "$failure_state/invocation-count")" != "1" ]]; then
  fail "graceful stop retried a failed agent attempt"
fi
grep -Fq 'cycle 1 will not be retried' "$failure_output" \
  || fail "build loop did not report that graceful stop suppressed retry"

pp_step "Regression: follower discovers the active loop and streams appended output"
follow_state="$tmp_dir/follow-state"
follow_output="$tmp_dir/follow.log"
mkdir -p "$follow_state/lock"
printf 'Initial detailed line.\n' > "$follow_state/follow.log"
sleep 20 &
fake_loop_pid=$!
printf '%s\n' "$fake_loop_pid" > "$follow_state/lock/pid"
printf '7\n' > "$follow_state/lock/cycle"
printf '12\n' > "$follow_state/lock/max-cycles"
printf 'MA-007 — Follow fixture (TODO)\n' > "$follow_state/lock/ticket"
printf 'agent\n' > "$follow_state/lock/phase"

(
  cd "$success_work"
  NO_COLOR=1 \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$follow_state" \
    bash scripts/build-loop-follow.sh --lines 10
) > "$follow_output" 2>&1 &
follow_job=$!

wait_for_text "$follow_output" 'Initial detailed line.' \
  || fail "follower did not show existing detailed output"
printf 'Appended live line.\n' >> "$follow_state/follow.log"
wait_for_text "$follow_output" 'Appended live line.' \
  || fail "follower did not stream newly appended output"
touch "$follow_state/lock/stop-requested"
wait_for_text "$follow_output" 'Graceful stop requested' \
  || fail "follower did not surface a later graceful stop request"

rm -rf "$follow_state/lock"
kill "$fake_loop_pid" 2>/dev/null || true
wait "$fake_loop_pid" 2>/dev/null || true
fake_loop_pid=""

if ! wait "$follow_job"; then
  cat "$follow_output" >&2
  fail "follower returned an error after the observed loop exited"
fi

grep -Fq 'Cycle:               7/12' "$follow_output" \
  || fail "follower did not report current cycle metadata"
grep -Fq 'Ctrl-C detaches this follower' "$follow_output" \
  || fail "follower did not explain detach semantics"
grep -Fq 'Build loop exited; follower finished.' "$follow_output" \
  || fail "follower did not report observed loop completion"

pp_success "Build-loop control regressions passed."
