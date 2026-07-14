#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

pp_banner "Mock autonomous build output" "Demonstrates formatting only; no agent, validation, git, or network commands are run."

pp_section "Summary"
pp_info "Now working on: ticket 001 — Add core project functionality (TODO)"
pp_kv "Cycle" "1/3"
pp_kv "Branch" "feature/autonomous-build"
pp_success "Planned work has been identified."

pp_section "Optional setup"
pp_step "Create or select a work branch before the loop starts."
pp_cmd "scripts/build-loop.sh --create-branch feature/autonomous-build --max-cycles 3"
pp_step "Create a GitHub or GitLab repository from the current checkout."
pp_cmd "scripts/create-remote-repo.sh --github --name OWNER/REPO --visibility private"

pp_section "Validation"
pp_step "Run shell syntax checks."
pp_cmd "bash -n scripts/*.sh scripts/lib/*.sh"
pp_step "Run project quality gate."
pp_cmd "scripts/quality-gate.sh"
pp_success "Mock validation passed."

pp_section "Commit"
pp_step "Stage changed files."
pp_cmd "git add README.md src tests"
pp_step "Create a conventional commit."
pp_cmd "git commit -m 'feat: add core project functionality'"
pp_success "Mock commit created: abc1234"

pp_section "Next steps"
pp_info "Push would run next when push-after-commit is enabled."
pp_cmd "git push"
pp_info "If PR/MR automation is enabled, the loop would then create or merge the PR/MR."
pp_cmd "gh pr create --base main --head feature/autonomous-build --title 'feat: add core project functionality' --body 'Automated autonomous build update.'"
