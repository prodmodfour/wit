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

run_summary_fixture() {
  local name="$1"
  local fixture_file="$2"
  local expected_line="$3"
  local slug="$4"
  local work_dir="$tmp_dir/work-$slug"
  local state_dir="$tmp_dir/state-$slug"
  local output

  pp_step "Regression: build-loop reports $name"

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

printf '\nStub implementation for ticket detection.\n' >> WORK_LOG.md
git add WORK_LOG.md
git commit -q -m "test: implement detected ticket"
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

    cp "$fixture_file" BUILD_TICKETS.md

    git add .
    git commit -q -m "test: initial fixture"

    output="$(
      NO_COLOR=1 \
      AUTONOMOUS_BUILD_LOOP_STATE_DIR="$state_dir" \
        bash scripts/build-loop.sh --max-cycles 1 --no-push 2>&1
    )"

    if ! grep -Fq "$expected_line" <<< "$output"; then
      printf '%s\n' "$output" >&2
      fail "build loop did not report $name"
    fi

    if grep -Fq "No TODO ticket found" <<< "$output"; then
      printf '%s\n' "$output" >&2
      fail "build loop still warned that no TODO ticket was found for $name"
    fi
  )
}

sprint_fixture="$tmp_dir/sprint-build-tickets.md"
cat > "$sprint_fixture" <<'BUILD_TICKETS'
# BUILD_TICKETS.md

AUTOMATION_STATUS: TODO

Ticket statuses:

* TODO — not done
* DONE — done

---

# Live Play Sprint 5 Tickets

## Sprint goal

Introductory section without a status line.

## LP-S5-001 — Audit current token movement presentation

Status: TODO

Document the current pipeline.

## LP-S5-002 — Add pure token motion curve utilities

Status: TODO

Add tested motion helpers.
BUILD_TICKETS

plain_fixture="$tmp_dir/plain-build-tickets.md"
cat > "$plain_fixture" <<'BUILD_TICKETS'
# BUILD_TICKETS.md

AUTOMATION_STATUS: TODO

Ticket statuses:

* TODO — not done
* DONE — done

---

# Hover/floating sprite centering tickets

## Goal

Introductory section without a status line.

## Ticket 1 — Define the visual-bounds metadata contract

Status: TODO

Add optional metadata types.

## Ticket 2 — Add a reusable sprite visual-bounds extractor

Status: TODO

Add extraction helpers.
BUILD_TICKETS

mixed_queue_fixture="$tmp_dir/mixed-queue-build-tickets.md"
cat > "$mixed_queue_fixture" <<'BUILD_TICKETS'
# BUILD_TICKETS.md

AUTOMATION_STATUS: TODO

## MA-003 — Completed foundation

Status: DONE

## MA-004 — Continue the current phase

Status: TODO

## REG-001 — Certify the later phase

Status: TODO
BUILD_TICKETS

run_summary_fixture \
  "sprint-style TODO ticket headings" \
  "$sprint_fixture" \
  "Now working on: ticket LP-S5-001 — Audit current token movement presentation (TODO)" \
  "sprint"

run_summary_fixture \
  "plain Ticket N TODO ticket headings" \
  "$plain_fixture" \
  "Now working on: ticket 001 — Define the visual-bounds metadata contract (TODO)" \
  "plain"

run_summary_fixture \
  "the first TODO across mixed ticket families" \
  "$mixed_queue_fixture" \
  "Now working on: ticket MA-004 — Continue the current phase (TODO)" \
  "mixed-queue"

pp_success "Build-loop ticket summary regression passed."
