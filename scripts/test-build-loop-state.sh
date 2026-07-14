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

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

work_dir="$tmp_dir/work"
state_dir="$tmp_dir/state"

pp_step "Regression: build-loop state survives .agent cleanup"

git init -q "$work_dir"

(
  cd "$work_dir"

  git config user.name "Build Loop Test"
  git config user.email "build-loop-test@example.invalid"

  mkdir -p scripts/lib
  cp "$REPO_ROOT/scripts/build-loop.sh" scripts/build-loop.sh
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

: "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:?AUTONOMOUS_BUILD_LOOP_STATE_DIR must be set by the test}"
: "${PI_AGENT_EVENT_LOG:?PI_AGENT_EVENT_LOG must be supplied by the build loop}"

commit_count="$(git rev-list --count HEAD)"
echo "Stub agent cycle from commit count ${commit_count}."
(umask 077 && printf '{"type":"fixture","cycle":%s}\n' "$commit_count" > "$PI_AGENT_EVENT_LOG")
printf '\nStub cycle %s\n' "$commit_count" >> WORK_LOG.md
git add WORK_LOG.md
git commit -q -m "test: stub cycle ${commit_count}"

# Simulate repo guardrails removing private/runtime state after an agent cycle.
rm -rf .agent

# Simulate the active log directory disappearing between cycles. The build loop
# must recreate it before the next tee invocation.
if [[ "$commit_count" == "1" ]]; then
  rm -rf "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/logs"
fi
RUN_AGENT

  chmod +x scripts/build-loop.sh scripts/quality-gate.sh scripts/run-agent.sh

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

## 000 — Test ticket

Status: TODO
BUILD_TICKETS

  git add .
  git commit -q -m "test: initial fixture"

  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$state_dir" \
    bash scripts/build-loop.sh --max-cycles 2 --no-push

  if [[ -e .agent/logs/build-loop || -e .agent/build-loop.lock ]]; then
    fail "build-loop wrote active log/lock state inside .agent"
  fi
)

if [[ -d "$state_dir/lock" ]]; then
  fail "build-loop lock directory was not cleaned up: $state_dir/lock"
fi

if [[ ! -d "$state_dir/logs" ]]; then
  fail "build-loop did not recreate the external log directory"
fi

if [[ ! -f "$state_dir/follow.log" ]]; then
  fail "build-loop did not preserve the stable follow log"
fi
follow_line_count="$(grep -c 'Stub agent cycle' "$state_dir/follow.log" || true)"
if [[ "$follow_line_count" -ne 2 ]]; then
  fail "stable follow log did not retain detailed output across cycles"
fi

log_count="$(find "$state_dir/logs" -type f -name 'cycle-*.log' | wc -l | tr -d ' ')"
if [[ "$log_count" -lt 1 ]]; then
  fail "expected at least one external build-loop log in $state_dir/logs"
fi

event_log_count="$(find "$state_dir/logs" -type f -name 'cycle-*.pi-events.jsonl' | wc -l | tr -d ' ')"
if [[ "$event_log_count" -lt 1 ]]; then
  fail "build loop did not provide a full Pi event sidecar path to the agent wrapper"
fi
if ! grep -Fq 'Full Pi event log:' "$state_dir/follow.log"; then
  fail "stable follow log did not identify the full Pi event sidecar"
fi

pp_step "Regression: an exiting stale loop preserves a replacement lock"
ownership_work_dir="$tmp_dir/ownership-work"
ownership_state_dir="$tmp_dir/ownership-state"
git clone -q "$work_dir" "$ownership_work_dir"
(
  cd "$ownership_work_dir"
  git config user.name "Build Loop Ownership Test"
  git config user.email "build-loop-ownership-test@example.invalid"

  cat > scripts/run-agent.sh <<'RUN_AGENT'
#!/usr/bin/env bash
set -euo pipefail

: "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:?AUTONOMOUS_BUILD_LOOP_STATE_DIR must be set by the test}"

rm -rf "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/lock"
mkdir "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/lock"
printf '%s\n' '999999' > "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/lock/pid"
printf '%s\n' 'replacement-loop' > "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/lock/phase"
exit 130
RUN_AGENT
  chmod +x scripts/run-agent.sh
  git add scripts/run-agent.sh
  git commit -q -m "test: replace active lock during agent exit"
)

set +e
(
  cd "$ownership_work_dir"
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$ownership_state_dir" \
    bash scripts/build-loop.sh --max-cycles 1 --no-push
) > "$tmp_dir/ownership-loop.log" 2>&1
ownership_status=$?
set -e

if [[ "$ownership_status" -ne 130 ]]; then
  fail "replacement-lock fixture exited with status $ownership_status instead of 130"
fi
if [[ ! -d "$ownership_state_dir/lock" ]]; then
  fail "exiting stale loop deleted the replacement lock"
fi
if [[ "$(< "$ownership_state_dir/lock/pid")" != "999999" ]]; then
  fail "replacement lock owner changed during stale-loop cleanup"
fi

pp_success "Build-loop state regressions passed."
