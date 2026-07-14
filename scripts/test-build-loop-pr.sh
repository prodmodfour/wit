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
remote_dir="$tmp_dir/origin.git"
state_dir="$tmp_dir/state"
fake_bin="$tmp_dir/bin"
gh_call_log="$tmp_dir/gh-calls.log"

pp_step "Regression: build loop creates and merges PRs after a successful cycle"

git init --bare -q "$remote_dir"
git init -q "$work_dir"

mkdir -p "$fake_bin"
cat > "$fake_bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail

: "${GH_CALL_LOG:?GH_CALL_LOG must be set by the test}"
printf '%s\n' "$*" >> "$GH_CALL_LOG"

if [[ "${1:-}" == "pr" && "${2:-}" == "list" ]]; then
  exit 0
fi

if [[ "${1:-}" == "pr" && "${2:-}" == "create" ]]; then
  printf 'https://example.invalid/owner/repo/pull/1\n'
  exit 0
fi

if [[ "${1:-}" == "pr" && "${2:-}" == "merge" ]]; then
  exit 0
fi

printf 'unexpected gh invocation: %s\n' "$*" >&2
exit 99
GH
chmod +x "$fake_bin/gh"

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

printf '\nImplemented PR automation fixture cycle.\n' >> WORK_LOG.md
git add WORK_LOG.md
git commit -q -m "test: implement pr automation fixture"
RUN_AGENT

  chmod +x scripts/build-loop.sh scripts/quality-gate.sh scripts/run-agent.sh scripts/lib/pull-request.sh

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
  git branch -M main
  git remote add origin "$remote_dir"
  git push -q -u origin main
  git switch -q -c feature/autonomous-build
  git push -q -u origin feature/autonomous-build

  PATH="$fake_bin:$PATH" \
  GH_CALL_LOG="$gh_call_log" \
  AUTONOMOUS_BUILD_LOOP_STATE_DIR="$state_dir" \
    bash scripts/build-loop.sh \
      --max-cycles 1 \
      --merge-pr-each-cycle \
      --pr-provider github \
      --pr-base main

  if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    fail "build loop left a dirty working tree after PR automation"
  fi

  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse refs/remotes/origin/feature/autonomous-build)"
  if [[ "$local_head" != "$remote_head" ]]; then
    fail "PR automation did not push the current branch to the configured remote"
  fi
)

if ! grep -q '^pr create ' "$gh_call_log"; then
  fail "gh pr create was not called"
fi

if ! grep -q -- '--head feature/autonomous-build' "$gh_call_log"; then
  fail "gh pr create did not use the current branch as the PR head"
fi

if ! grep -q -- '--base main' "$gh_call_log"; then
  fail "gh pr create did not use the configured base branch"
fi

if ! grep -q '^pr merge feature/autonomous-build --merge --match-head-commit ' "$gh_call_log"; then
  fail "gh pr merge was not called for the current branch with a head guard"
fi

pp_success "Build-loop PR automation regression passed."
