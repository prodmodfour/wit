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

git_log_has_subject() {
  local expected="$1"

  git log --format=%s | awk -v expected="$expected" '
    $0 == expected { found = 1 }
    END { exit found ? 0 : 1 }
  '
}

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

create_fixture() {
  local work_dir="$1"

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
printf 'default run-agent stub should be replaced by each scenario\n' >&2
exit 1
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

## 001 — Oversized ticket

Status: TODO

Implement too much in one ticket.
BUILD_TICKETS

    git add .
    git commit -q -m "test: initial fixture"
  )
}

pp_step "Regression: token/context failures split the ticket and continue"
context_work_dir="$tmp_dir/context-work"
context_state_dir="$tmp_dir/context-state"
create_fixture "$context_work_dir"

(
  cd "$context_work_dir"

  cat > scripts/run-agent.sh <<'RUN_AGENT'
#!/usr/bin/env bash
set -euo pipefail

prompt="${1:-}"
: "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:?AUTONOMOUS_BUILD_LOOP_STATE_DIR must be set by the test}"

if [[ "$prompt" == *"recovery task for an autonomous build loop"* ]]; then
  cat > BUILD_TICKETS.md <<'BUILD_TICKETS'
# BUILD_TICKETS.md

AUTOMATION_STATUS: NOT_DONE

## 001 — Oversized ticket part 1

Status: TODO

Implement the first smaller slice.

## 002 — Oversized ticket part 2

Status: TODO

Implement the remaining slice.
BUILD_TICKETS

  git add BUILD_TICKETS.md
  git commit -q -m "chore: split oversized build ticket"
  exit 0
fi

if [[ ! -f "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/context-failed" ]]; then
  mkdir -p "$AUTONOMOUS_BUILD_LOOP_STATE_DIR"
  touch "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/context-failed"
  printf '\nPartial oversized attempt that should be checkpointed.\n' >> WORK_LOG.md
  printf 'Error: context_length_exceeded: maximum context length exceeded\n' >&2
  exit 1
fi

printf '\nImplementation after split.\n' >> WORK_LOG.md
git add WORK_LOG.md
git commit -q -m "test: implementation after split"
RUN_AGENT

  chmod +x scripts/run-agent.sh
  git add scripts/run-agent.sh
  git commit -q -m "test: configure context recovery stub"

  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$context_state_dir" \
  AUTONOMOUS_BUILD_RETRY_SECONDS=0 \
    bash scripts/build-loop.sh --max-cycles 1 --no-push

  git_log_has_subject 'chore: split oversized build ticket' \
    || fail "context failure did not create a ticket split commit"
  git_log_has_subject 'test: implementation after split' \
    || fail "build loop did not continue after splitting the ticket"
  grep -q 'Oversized ticket part 2' BUILD_TICKETS.md \
    || fail "BUILD_TICKETS.md was not split into a second ticket"
  git_log_has_subject 'chore: checkpoint failed autonomous cycle' \
    || fail "context failure did not create a failure checkpoint commit"
  grep -q 'Partial oversized attempt that should be checkpointed' WORK_LOG.md \
    || fail "dirty context-failure changes were not checkpointed before recovery"
)

pp_step "Regression: non-token agent failures retry without splitting"
retry_work_dir="$tmp_dir/retry-work"
retry_state_dir="$tmp_dir/retry-state"
create_fixture "$retry_work_dir"

(
  cd "$retry_work_dir"

  cat > scripts/run-agent.sh <<'RUN_AGENT'
#!/usr/bin/env bash
set -euo pipefail

prompt="${1:-}"
: "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:?AUTONOMOUS_BUILD_LOOP_STATE_DIR must be set by the test}"

if [[ "$prompt" == *"recovery task for an autonomous build loop"* ]]; then
  printf 'split recovery should not run for transient failures\n' >&2
  exit 42
fi

if [[ ! -f "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/transient-failed" ]]; then
  mkdir -p "$AUTONOMOUS_BUILD_LOOP_STATE_DIR"
  touch "$AUTONOMOUS_BUILD_LOOP_STATE_DIR/transient-failed"
  printf '\nPartial transient attempt that should be checkpointed.\n' >> WORK_LOG.md
  printf 'OpenAI API returned a temporary 500 server error\n' >&2
  exit 1
fi

printf '\nImplementation after retry.\n' >> WORK_LOG.md
git add WORK_LOG.md
git commit -q -m "test: implementation after retry"
RUN_AGENT

  chmod +x scripts/run-agent.sh
  git add scripts/run-agent.sh
  git commit -q -m "test: configure retry stub"

  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$retry_state_dir" \
  AUTONOMOUS_BUILD_RETRY_SECONDS=0 \
    bash scripts/build-loop.sh --max-cycles 1 --no-push

  git_log_has_subject 'test: implementation after retry' \
    || fail "build loop did not retry after a transient failure"
  if git_log_has_subject 'chore: split oversized build ticket'; then
    fail "transient failure unexpectedly triggered ticket splitting"
  fi
  git_log_has_subject 'chore: checkpoint failed autonomous cycle' \
    || fail "transient failure did not create a failure checkpoint commit"
  grep -q 'Partial transient attempt that should be checkpointed' WORK_LOG.md \
    || fail "dirty transient-failure changes were not checkpointed before retry"
)

pp_success "Build-loop recovery regressions passed."
