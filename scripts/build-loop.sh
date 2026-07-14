#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"
# shellcheck source=scripts/lib/git-branch.sh
source "$SCRIPT_DIR/lib/git-branch.sh"
# shellcheck source=scripts/lib/pull-request.sh
source "$SCRIPT_DIR/lib/pull-request.sh"
# shellcheck source=scripts/lib/build-loop-state.sh
source "$SCRIPT_DIR/lib/build-loop-state.sh"

usage() {
  cat <<'USAGE'
Usage: scripts/build-loop.sh [options]

Runs autonomous build cycles.

Each cycle:

* reads AGENTS.md, PROJECT_BRIEF.md, and BUILD_TICKETS.md
* selects the first TODO ticket in queue order
* implements only that ticket
* runs quality gates
* updates BUILD_TICKETS.md
* commits the completed work
* pushes the new commit unless --no-push is used
* optionally creates or creates-and-merges a PR/MR for the new commit
* on agent failure, commits any failed-run changes, pushes them unless --no-push is used, then retries
* leaves the working tree clean

Options:
--max-cycles N       Number of cycles to run. Default: 1.
--sleep SECONDS      Pause between successful cycles. Default: 0.
--agent-output MODE  Agent output mode: live, final, or json. Default: live.
--no-push            Do not push after successful cycles. By default, each new commit is pushed.
--push               Push after successful cycles (default; kept for compatibility).
--branch NAME        Select an existing local branch, or a unique remote branch, before running.
--create-branch NAME
                     Create and select a new branch before running.
--branch-start REF   Start point for --create-branch. Default: HEAD.
--pr-each-cycle      Create or reuse a GitHub PR or GitLab MR after each successful cycle.
--create-pr          Alias for --pr-each-cycle.
--merge-pr-each-cycle
                     Create and merge a PR/MR after each successful cycle.
--merge-pr           Alias for --merge-pr-each-cycle.
--no-pr              Disable PR/MR automation (default).
--pr-provider VALUE  PR provider: auto, github, or gitlab. Default: auto.
--pr-base NAME       PR/MR base or target branch. Default: detected remote default branch.
--pr-remote NAME     Remote used for PR/MR push and base detection. Default: origin.
--allow-ahead        Allow starting when branch is already ahead of upstream (default; kept for compatibility).
--allow-template     Allow running even if PROJECT_BRIEF.md is still marked uncustomised.
-h, --help           Show this help.

Environment:
AUTONOMOUS_BUILD_LOOP_STATE_DIR
                   Override the per-repository state directory used for build-loop
                   logs and lock files. Defaults outside the repository under
                   ${XDG_STATE_HOME:-$HOME/.local/state}/autonomous-build-template/build-loop/<repo-key>.
AUTONOMOUS_BUILD_RETRY_SECONDS
                   Seconds to wait before retrying after transient agent failures.
                   Defaults to 600 (10 minutes).

While a loop is active, use `just follow` from another terminal for concise activity,
`just monitor 10` for normalized-event interpreted updates, and `just stop` to
request a graceful stop after the current cycle or attempt.

This script intentionally does not pass a model or thinking level.
Agent invocation is delegated to scripts/run-agent.sh.
USAGE
}

MAX_CYCLES=1
SLEEP_SECONDS=0
PUSH_AFTER=1
SELECT_BRANCH=""
CREATE_BRANCH=""
BRANCH_START_POINT="HEAD"
BRANCH_START_SET=0
ALLOW_AHEAD=1
ALLOW_TEMPLATE=0
AGENT_RETRY_SECONDS="${AUTONOMOUS_BUILD_RETRY_SECONDS:-600}"
AGENT_OUTPUT_MODE="live"
PR_MODE="none"
PR_PROVIDER="auto"
PR_REMOTE_NAME="origin"
PR_BASE_BRANCH=""
PR_RESOLVED_PROVIDER=""
PR_RESOLVED_BASE_BRANCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --max-cycles)
      if [[ $# -lt 2 ]]; then
        pp_error "--max-cycles requires a value"
        usage >&2
        exit 2
      fi
      MAX_CYCLES="$2"
      shift 2
      ;;
    --sleep)
      if [[ $# -lt 2 ]]; then
        pp_error "--sleep requires a value"
        usage >&2
        exit 2
      fi
      SLEEP_SECONDS="$2"
      shift 2
      ;;
    --agent-output)
      if [[ $# -lt 2 ]]; then
        pp_error "--agent-output requires a value"
        usage >&2
        exit 2
      fi
      AGENT_OUTPUT_MODE="$2"
      shift 2
      ;;
    --push)
      PUSH_AFTER=1
      shift
      ;;
    --no-push)
      PUSH_AFTER=0
      shift
      ;;
    --branch)
      if [[ $# -lt 2 ]]; then
        pp_error "--branch requires a value"
        usage >&2
        exit 2
      fi
      SELECT_BRANCH="$2"
      shift 2
      ;;
    --create-branch)
      if [[ $# -lt 2 ]]; then
        pp_error "--create-branch requires a value"
        usage >&2
        exit 2
      fi
      CREATE_BRANCH="$2"
      shift 2
      ;;
    --branch-start)
      if [[ $# -lt 2 ]]; then
        pp_error "--branch-start requires a value"
        usage >&2
        exit 2
      fi
      BRANCH_START_POINT="$2"
      BRANCH_START_SET=1
      shift 2
      ;;
    --pr-each-cycle|--create-pr)
      PR_MODE="create"
      shift
      ;;
    --merge-pr-each-cycle|--merge-pr)
      PR_MODE="merge"
      shift
      ;;
    --no-pr)
      PR_MODE="none"
      shift
      ;;
    --pr-provider)
      if [[ $# -lt 2 ]]; then
        pp_error "--pr-provider requires a value"
        usage >&2
        exit 2
      fi
      PR_PROVIDER="$2"
      shift 2
      ;;
    --pr-base)
      if [[ $# -lt 2 ]]; then
        pp_error "--pr-base requires a value"
        usage >&2
        exit 2
      fi
      PR_BASE_BRANCH="$2"
      shift 2
      ;;
    --pr-remote)
      if [[ $# -lt 2 ]]; then
        pp_error "--pr-remote requires a value"
        usage >&2
        exit 2
      fi
      PR_REMOTE_NAME="$2"
      shift 2
      ;;
    --allow-ahead)
      ALLOW_AHEAD=1
      shift
      ;;
    --allow-template)
      ALLOW_TEMPLATE=1
      shift
      ;;
    *)
      pp_error "Unknown argument: $1"
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$MAX_CYCLES" =~ ^[0-9]+$ ]] || [[ "$MAX_CYCLES" -lt 1 ]]; then
  pp_error "--max-cycles must be a positive integer"
  exit 2
fi

if ! [[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  pp_error "--sleep must be a non-negative integer"
  exit 2
fi

if ! [[ "$AGENT_RETRY_SECONDS" =~ ^[0-9]+$ ]]; then
  pp_error "AUTONOMOUS_BUILD_RETRY_SECONDS must be a non-negative integer"
  exit 2
fi

case "$AGENT_OUTPUT_MODE" in
  live|final|json) ;;
  *)
    pp_error "--agent-output must be live, final, or json: $AGENT_OUTPUT_MODE"
    exit 2
    ;;
esac

if [[ -n "$SELECT_BRANCH" && -n "$CREATE_BRANCH" ]]; then
  pp_error "--branch and --create-branch cannot be used together"
  exit 2
fi

if (( BRANCH_START_SET == 1 )) && [[ -z "$CREATE_BRANCH" ]]; then
  pp_error "--branch-start requires --create-branch"
  exit 2
fi

case "$PR_MODE" in
  none|create|merge) ;;
  *)
    pp_error "Internal error: invalid PR mode: $PR_MODE"
    exit 2
    ;;
esac

pr_validate_provider "$PR_PROVIDER" || exit $?

if [[ "$PR_MODE" != "none" && "$PUSH_AFTER" == "0" ]]; then
  pp_error "PR/MR automation requires pushing; remove --no-push or remove PR options."
  exit 2
fi

if [[ -z "$PR_REMOTE_NAME" ]]; then
  pp_error "--pr-remote must not be empty"
  exit 2
fi

if ! [[ "$PR_REMOTE_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  pp_error "Invalid PR remote name: $PR_REMOTE_NAME"
  pp_hint "Use letters, numbers, dots, underscores, or dashes."
  exit 2
fi

REQUIRED_FILES=(
  AGENTS.md
  PROJECT_BRIEF.md
  BUILD_TICKETS.md
  scripts/quality-gate.sh
  scripts/run-agent.sh
  scripts/lib/pretty-print.sh
  scripts/lib/git-branch.sh
  scripts/lib/pull-request.sh
  scripts/lib/build-loop-state.sh
)

BUILD_LOOP_STATE_DIR=""
LOG_DIR=""
LOCK_DIR=""
CURRENT_LOG=""
FOLLOW_LOG=""
STOP_REQUEST_FILE=""
CYCLE_UPSTREAM_REF=""
CYCLE_UPSTREAM_HEAD=""

PROMPT=$(cat <<'PROMPT_EOF'
You are continuing an autonomous ticket-driven build.

Read AGENTS.md, PROJECT_BRIEF.md, and BUILD_TICKETS.md.

Your task in this run:

* Select the first TODO ticket in file order from BUILD_TICKETS.md.
* At the start of the run, print a short "Now working on ..." line naming the selected ticket and immediate action.
* Implement only that ticket.
* Do not start future tickets.
* Do not broaden scope.
* Respect all project-specific instructions in PROJECT_BRIEF.md.
* Respect all general instructions in AGENTS.md.
* Add or update tests/validation where appropriate.
* Update documentation if the ticket changes setup, architecture, behaviour, operations, security posture, limitations, or public-facing usage.
* Run scripts/quality-gate.sh.
* Update only the selected ticket status in BUILD_TICKETS.md.
* Do not add run notes, validation summaries, blocker notes, or other commentary to BUILD_TICKETS.md.
* Commit the completed ticket with a conventional commit message.
* Do not create or merge PRs/MRs; the outer build loop handles that when configured.
* Leave the working tree clean.

If you cannot safely complete the ticket:

* print the blocker in the agent response
* leave the ticket status as not done
* do not add blocker notes to BUILD_TICKETS.md
* do not mark it DONE
* do not commit partial broken work
* leave the working tree clean if possible
PROMPT_EOF
)

SPLIT_TICKET_PROMPT=$(cat <<'PROMPT_EOF'
You are running a recovery task for an autonomous build loop after the implementation agent failed with a token or context-length error.

This recovery task overrides the normal implementation workflow in AGENTS.md.

Read AGENTS.md, PROJECT_BRIEF.md, and BUILD_TICKETS.md.

Your only task in this run:

* Identify the first TODO ticket in file order in BUILD_TICKETS.md.
* Split that one ticket into two smaller, sequential, independently actionable tickets.
* Preserve the original intent and acceptance criteria across the two new tickets.
* Make the first ticket a narrow foundation or vertical slice.
* Make the second ticket the remaining behaviour, integration, documentation, or validation work.
* Keep both tickets small enough for separate future autonomous runs.
* Set the first split ticket to TODO.
* Set the second split ticket to TODO.
* Renumber later tickets if needed to keep ordering clear.
* Do not implement product code.
* Do not start or complete any ticket.
* Do not run the normal quality gate unless you need it to validate this ticket-file-only edit.
* Do not add recovery notes or commentary to BUILD_TICKETS.md.
* Commit only BUILD_TICKETS.md with a conventional commit message such as "chore: split oversized build ticket".
* Leave the working tree clean.
PROMPT_EOF
)

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    pp_error "Required command not found: $1"
    exit 127
  fi
}

run_agent_with_log() {
  local prompt="$1"
  local log_file="$2"
  local -a pipeline_status
  local agent_status
  local tee_status
  local pi_event_log

  pi_event_log=""
  if [[ "$AGENT_OUTPUT_MODE" != "final" ]]; then
    pi_event_log="$(build_loop_pi_event_log "$log_file")"
  fi
  pp_info "Detailed agent activity is streaming to the follow log; use: just follow"
  {
    printf '\nAgent activity — cycle %s/%s\n' "$cycle" "$MAX_CYCLES"
    printf '%s\n' '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
    if [[ -n "${next_ticket:-}" ]]; then
      printf 'Ticket: %s\n' "$next_ticket"
    fi
    printf 'Cycle log: %s\n' "$log_file"
    if [[ -n "$pi_event_log" ]]; then
      printf 'Full Pi event log: %s\n' "$pi_event_log"
    fi
    printf '\n'
  } >> "$FOLLOW_LOG"

  set +e
  PI_AGENT_OUTPUT_MODE="$AGENT_OUTPUT_MODE" \
  PI_AGENT_EVENT_LOG="$pi_event_log" \
    scripts/run-agent.sh "$prompt" 2>&1 \
    | tee "$log_file" >> "$FOLLOW_LOG"
  pipeline_status=("${PIPESTATUS[@]}")
  set -e

  agent_status="${pipeline_status[0]}"
  tee_status="${pipeline_status[1]}"

  if (( tee_status != 0 )); then
    pp_error "Failed to write agent log: $log_file"
    return 125
  fi

  return "$agent_status"
}

is_token_context_failure() {
  local log_file="$1"

  [[ -f "$log_file" ]] || return 1

  grep -Eiq '(context[_ -]?length|context window|context_length_exceeded|maximum context|max context|context limit)' "$log_file" && return 0
  grep -Eiq '(too many tokens|too much input|token limit|token budget|maximum tokens|max tokens|exceed(ed|s|ing)?[^[:cntrl:]]{0,120}tokens|tokens[^[:cntrl:]]{0,120}exceed(ed|s|ing)?)' "$log_file" && return 0
  grep -Eiq '((input|prompt)[^[:cntrl:]]{0,80}(too long|too large|exceed(ed|s|ing)?)|maximum (input|prompt) length|request too large)' "$log_file" && return 0

  return 1
}

lock_owned_by_current_process() {
  local recorded_pid

  recorded_pid="$(build_loop_read_lock_value "$BUILD_LOOP_STATE_DIR" pid 2>/dev/null || true)"
  [[ "$recorded_pid" == "$$" ]]
}

require_lock_ownership() {
  local recorded_pid

  if lock_owned_by_current_process; then
    return 0
  fi

  recorded_pid="$(build_loop_read_lock_value "$BUILD_LOOP_STATE_DIR" pid 2>/dev/null || true)"
  pp_error "Build-loop lock ownership was lost; refusing to modify another loop's state."
  pp_kv "Expected PID" "$$" >&2
  pp_kv "Recorded PID" "${recorded_pid:-missing}" >&2
  return 1
}

write_lock_value() {
  local name="$1"
  local value="$2"
  local temporary_file="$LOCK_DIR/.${name}.$$"

  require_lock_ownership || return 1
  printf '%s\n' "$value" > "$temporary_file"
  mv "$temporary_file" "$LOCK_DIR/$name"
}

set_loop_phase() {
  write_lock_value phase "$1"
}

stop_requested() {
  [[ -f "$STOP_REQUEST_FILE" ]]
}

stop_if_requested() {
  local message="$1"

  if ! stop_requested; then
    return 0
  fi

  set_loop_phase "stopping"
  pp_section "Graceful stop"
  pp_success "$message"
  pp_info "No further autonomous cycle will start."
  exit 0
}

sleep_with_stop_checks() {
  local seconds="$1"
  local stop_message="$2"
  local remaining="$seconds"

  while (( remaining > 0 )); do
    stop_if_requested "$stop_message"
    sleep 1
    remaining=$((remaining - 1))
  done

  stop_if_requested "$stop_message"
}

sleep_before_agent_retry() {
  local reason="$1"

  stop_if_requested "Stop requested; cycle $cycle will not be retried."

  if (( AGENT_RETRY_SECONDS > 0 )); then
    set_loop_phase "retry-wait"
    pp_warn "$reason; retrying in ${AGENT_RETRY_SECONDS}s."
    sleep_with_stop_checks \
      "$AGENT_RETRY_SECONDS" \
      "Stop requested during the retry wait; cycle $cycle will not be retried."
  else
    pp_warn "$reason; retrying immediately."
  fi
}

git_clean_preserving_build_loop_state() {
  local repo_root
  local repo_abs
  local state_abs
  local state_rel

  repo_root="$(git rev-parse --show-toplevel)"
  repo_abs="$(cd "$repo_root" && pwd -P)"
  state_abs="$(cd "$BUILD_LOOP_STATE_DIR" 2>/dev/null && pwd -P || true)"

  if [[ -n "$state_abs" && "$state_abs" == "$repo_abs"/* ]]; then
    state_rel="${state_abs#"$repo_abs"/}"
    pp_cmd "git clean -fd -e $state_rel/"
    git clean -fd -e "$state_rel/" >/dev/null
  else
    pp_cmd "git clean -fd"
    git clean -fd >/dev/null
  fi
}

run_failure_checkpoint_guardrails() {
  local fail=0

  if [[ -f scripts/check-no-secrets.sh ]]; then
    if ! bash scripts/check-no-secrets.sh; then
      fail=1
    fi
  fi

  if [[ -f scripts/check-no-generated-private-files.sh ]]; then
    if ! bash scripts/check-no-generated-private-files.sh; then
      fail=1
    fi
  fi

  if (( fail != 0 )); then
    pp_error "Refusing to checkpoint failed-run changes because a safety guardrail failed."
    return 1
  fi
}

refresh_cycle_upstream_after_self_push() {
  local upstream_ref

  upstream_ref="$(get_upstream_ref)"
  CYCLE_UPSTREAM_REF="$upstream_ref"
  CYCLE_UPSTREAM_HEAD=""

  if [[ -n "$upstream_ref" ]]; then
    git fetch --quiet
    CYCLE_UPSTREAM_HEAD="$(git rev-parse "$upstream_ref")"
  fi
}

push_failure_checkpoint() {
  if (( PUSH_AFTER == 0 )); then
    pp_info "Skipping failure checkpoint push because --no-push is set."
    return 0
  fi

  pp_section "Push failure checkpoint"
  if [[ "$PR_MODE" == "none" ]]; then
    git_branch_push_current origin
  else
    git_branch_push_current_to_remote "$PR_REMOTE_NAME"
  fi

  refresh_cycle_upstream_after_self_push
}

checkpoint_failed_agent_run() {
  local before_head="$1"
  local reason="$2"
  local log_file="$3"
  local current_head
  local checkpoint_needed=0

  pp_section "Failure checkpoint"
  pp_warn "Checkpointing failed-run state before deciding whether to retry."
  pp_kv "Reason" "$reason"
  pp_kv "Log file" "$log_file"

  current_head="$(git rev-parse HEAD)"
  if [[ "$current_head" != "$before_head" ]]; then
    pp_warn "Agent changed HEAD before failing; preserving existing failed-run commit(s)."
    pp_kv "Before failure" "$before_head"
    pp_kv "Current HEAD" "$current_head"
    checkpoint_needed=1
  fi

  if git cat-file -e "$before_head:BUILD_TICKETS.md" 2>/dev/null; then
    pp_info "Restoring BUILD_TICKETS.md to its pre-run state so the retry keeps the same ticket queue."
    pp_cmd "git checkout $before_head -- BUILD_TICKETS.md"
    git checkout "$before_head" -- BUILD_TICKETS.md
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    pp_warn "Agent left uncommitted changes after failure; committing them before retry."
    run_failure_checkpoint_guardrails || return 1
    pp_cmd "git add -A"
    git add -A

    if git diff --cached --quiet; then
      pp_info "No staged changes to checkpoint after git add."
    else
      pp_cmd "git commit -m 'chore: checkpoint failed autonomous cycle'"
      git commit -m "chore: checkpoint failed autonomous cycle"
      checkpoint_needed=1
    fi
  fi

  if (( checkpoint_needed == 1 )); then
    refuse_if_remote_advanced "$CYCLE_UPSTREAM_REF" "$CYCLE_UPSTREAM_HEAD"
    push_failure_checkpoint || return 1
    pp_success "Failure checkpoint preserved at current HEAD."
  else
    pp_info "No failed-run changes or commits to checkpoint."
  fi
}

require_clean_tree() {
  git_branch_require_clean_tree || exit 1
}

require_customised_template() {
  if (( ALLOW_TEMPLATE == 1 )); then
    return 0
  fi

  if grep -Eq '^TEMPLATE_CUSTOMISED:[[:space:]]*false[[:space:]]*$' PROJECT_BRIEF.md; then
    pp_error "PROJECT_BRIEF.md is still marked TEMPLATE_CUSTOMISED: false."
    pp_hint "Edit PROJECT_BRIEF.md for this project and set TEMPLATE_CUSTOMISED: true before running."
    exit 1
  fi
}

get_upstream_ref() {
  git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true
}

get_automation_status() {
  awk -F: '
    /^##[[:space:]]/ { exit }
    /^AUTOMATION_STATUS:/ {
      status=$2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", status)
      print status
      exit
    }
  ' BUILD_TICKETS.md
}

get_next_ticket_summary() {
  awk '
    function trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return value
    }

    function record_if_current() {
      if (!found && ticket_id != "" && ticket_status == "TODO") {
        found = 1
        next_id = ticket_id
        next_title = ticket_title
        next_status = ticket_status
      }
    }

    /^##[[:space:]]+/ {
      record_if_current()

      heading = $0
      sub(/^##[[:space:]]+/, "", heading)

      ticket_id = heading
      ticket_title = heading
      if (ticket_id ~ /^Ticket[[:space:]]+[0-9]+([[:space:]]|$)/) {
        sub(/^Ticket[[:space:]]+/, "", ticket_id)
        sub(/[[:space:]].*$/, "", ticket_id)
        ticket_id = sprintf("%03d", ticket_id + 0)
        sub(/^Ticket[[:space:]]+[0-9]+[[:space:]]*/, "", ticket_title)
      } else {
        sub(/[[:space:]].*$/, "", ticket_id)
        if (ticket_id !~ /[0-9]/) {
          ticket_id = ""
          ticket_title = ""
          ticket_status = ""
          next
        }
        sub(/^[^[:space:]]+[[:space:]]+/, "", ticket_title)
        if (ticket_title == heading) ticket_title = ""
      }
      sub(/^[-—][[:space:]]*/, "", ticket_title)

      ticket_status = ""
      next
    }

    ticket_id != "" && /^[[:space:]]*Status:/ {
      ticket_status = $0
      sub(/^[[:space:]]*Status:[[:space:]]*/, "", ticket_status)
      ticket_status = trim(ticket_status)
    }

    END {
      record_if_current()
      if (found) {
        printf "%s — %s (%s)\n", next_id, next_title, next_status
      }
    }
  ' BUILD_TICKETS.md
}

sync_before_cycle() {
  local upstream_ref
  local counts
  local behind_count
  local ahead_count

  require_clean_tree

  upstream_ref="$(get_upstream_ref)"
  CYCLE_UPSTREAM_REF="$upstream_ref"
  CYCLE_UPSTREAM_HEAD=""

  if [[ -z "$upstream_ref" ]]; then
    pp_info "No upstream configured; skipping remote sync checks."
    pp_success "Pre-flight checks passed."
    return 0
  fi

  git fetch --quiet
  CYCLE_UPSTREAM_HEAD="$(git rev-parse "$upstream_ref")"
  counts="$(git rev-list --left-right --count "${upstream_ref}...HEAD")"
  read -r behind_count ahead_count <<< "$counts"

  if (( behind_count > 0 )); then
    pp_error "Branch is behind upstream by ${behind_count} commit(s); refusing to start."
    pp_hint "Synchronise with upstream manually, then rerun the build loop."
    exit 1
  fi

  pp_kv "Upstream" "$upstream_ref"
  pp_kv "Behind" "$behind_count commit(s)"
  pp_kv "Ahead" "$ahead_count commit(s)"
  pp_success "Pre-flight checks passed."
}

refuse_if_remote_advanced() {
  local upstream_ref="$1"
  local expected_upstream_head="$2"
  local current_upstream_head

  if [[ -z "$upstream_ref" || -z "$expected_upstream_head" ]]; then
    return 0
  fi

  git fetch --quiet
  current_upstream_head="$(git rev-parse "$upstream_ref")"

  if [[ "$current_upstream_head" != "$expected_upstream_head" ]]; then
    pp_error "Upstream $upstream_ref advanced during the cycle; refusing to continue."
    pp_kv "Expected upstream" "$expected_upstream_head" >&2
    pp_kv "Current upstream" "$current_upstream_head" >&2
    exit 1
  fi
}

configure_pr_automation() {
  if [[ "$PR_MODE" == "none" ]]; then
    return 0
  fi

  pp_section "PR/MR setup"

  pr_require_remote "$PR_REMOTE_NAME" || exit $?

  PR_RESOLVED_PROVIDER="$(pr_resolve_provider "$PR_PROVIDER" "$PR_REMOTE_NAME")" || exit $?

  pr_require_provider_cli "$PR_RESOLVED_PROVIDER" || exit $?

  if [[ -n "$PR_BASE_BRANCH" ]]; then
    git_branch_validate_name "$PR_BASE_BRANCH" "PR base branch" || exit $?
    PR_RESOLVED_BASE_BRANCH="$PR_BASE_BRANCH"
  else
    PR_RESOLVED_BASE_BRANCH="$(pr_detect_base_branch "$PR_REMOTE_NAME")" || exit $?
  fi

  git_branch_validate_name "$PR_RESOLVED_BASE_BRANCH" "PR base branch" || exit $?
  pr_validate_current_branch "$PR_RESOLVED_BASE_BRANCH" || exit $?

  pp_kv "Provider" "$PR_RESOLVED_PROVIDER"
  pp_kv "Remote" "$PR_REMOTE_NAME"
  pp_kv "Base branch" "$PR_RESOLVED_BASE_BRANCH"
  pp_kv "Mode" "$PR_MODE"
}

build_pr_body() {
  local context="$1"
  local head_sha="$2"
  local current_branch="$3"

  cat <<BODY
Automated autonomous build update.

Context: $context
Branch: $current_branch
Commit: $head_sha
Base: $PR_RESOLVED_BASE_BRANCH

Created by scripts/build-loop.sh.
BODY
}

publish_current_commit() {
  local context="$1"
  local head_sha="$2"
  local current_branch
  local pr_title
  local pr_body

  if (( PUSH_AFTER == 1 )); then
    if [[ "$PR_MODE" == "none" ]]; then
      pp_section "Push"
      git_branch_push_current origin
    else
      pp_section "Push"
      git_branch_push_current_to_remote "$PR_REMOTE_NAME"
    fi
  fi

  if [[ "$PR_MODE" == "none" ]]; then
    return 0
  fi

  current_branch="$(git branch --show-current 2>/dev/null || true)"
  pr_title="$(git log -1 --pretty=%s)"
  pr_body="$(build_pr_body "$context" "$head_sha" "$current_branch")"

  pp_section "Create PR/MR"
  pr_create_current_branch "$PR_RESOLVED_PROVIDER" "$PR_RESOLVED_BASE_BRANCH" "$pr_title" "$pr_body"

  if [[ "$PR_MODE" == "merge" ]]; then
    pp_section "Merge PR/MR"
    pr_merge_current_branch "$PR_RESOLVED_PROVIDER" "$head_sha"
  fi
}

split_current_ticket_after_context_failure() {
  local split_before_head
  local split_log
  local split_status
  local split_after_head

  set_loop_phase "token-context-recovery"
  pp_section "Token/context recovery"
  pp_warn "Detected a token/context-length failure in the agent log."
  pp_info "Asking the configured agent wrapper to split the current ticket into two smaller tickets."

  split_before_head="$(git rev-parse HEAD)"
  mkdir -p "$LOG_DIR"
  log_sequence=$((log_sequence + 1))
  split_log="$LOG_DIR/split-ticket-$(date +%Y%m%d-%H%M%S)-$cycle-$log_sequence.log"

  pp_kv "Split log file" "$split_log"

  if run_agent_with_log "$SPLIT_TICKET_PROMPT" "$split_log"; then
    split_status=0
  else
    split_status=$?
  fi

  if (( split_status != 0 )); then
    pp_error "Ticket split agent failed with exit status $split_status."
    pp_hint "See $split_log"
    if (( split_status == 130 || split_status == 143 )); then
      pp_error "Ticket split agent was interrupted; stopping."
      return 2
    fi
    checkpoint_failed_agent_run "$split_before_head" "ticket split failed with exit status $split_status" "$split_log" || return 2
    return 1
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    pp_error "Ticket split agent left a dirty working tree."
    git status --short >&2
    checkpoint_failed_agent_run "$split_before_head" "ticket split left a dirty working tree" "$split_log" || return 2
    return 1
  fi

  split_after_head="$(git rev-parse HEAD)"
  if [[ "$split_after_head" == "$split_before_head" ]]; then
    pp_error "Ticket split recovery completed without a new commit; retrying."
    return 1
  fi

  refuse_if_remote_advanced "$CYCLE_UPSTREAM_REF" "$CYCLE_UPSTREAM_HEAD"

  pp_success "Ticket split committed $(git rev-parse --short HEAD)"

  publish_current_commit "ticket split recovery" "$(git rev-parse HEAD)"
}

configure_build_loop_state_paths() {
  local repo_root
  local state_dir

  repo_root="$(git rev-parse --show-toplevel)"
  if ! state_dir="$(build_loop_resolve_state_dir "$repo_root")"; then
    pp_error "Unable to resolve the build-loop state directory."
    exit 1
  fi

  BUILD_LOOP_STATE_DIR="$state_dir"
  LOG_DIR="$(build_loop_log_dir "$BUILD_LOOP_STATE_DIR")"
  LOCK_DIR="$(build_loop_lock_dir "$BUILD_LOOP_STATE_DIR")"
  CURRENT_LOG="$(build_loop_current_log "$BUILD_LOOP_STATE_DIR")"
  FOLLOW_LOG="$(build_loop_follow_log "$BUILD_LOOP_STATE_DIR")"
  STOP_REQUEST_FILE="$(build_loop_stop_request_file "$BUILD_LOOP_STATE_DIR")"
  export AUTONOMOUS_BUILD_LOOP_STATE_DIR="$BUILD_LOOP_STATE_DIR"
}

release_lock() {
  if lock_owned_by_current_process; then
    rm -rf "$LOCK_DIR"
  fi
}

acquire_lock() {
  mkdir -p "$BUILD_LOOP_STATE_DIR" "$LOG_DIR"

  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    pp_error "Another build loop appears to be running: $LOCK_DIR"
    exit 1
  fi

  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  trap release_lock EXIT
}

start_run_logs() {
  : > "$CURRENT_LOG"
  : > "$FOLLOW_LOG"
  exec > >(tee -a "$CURRENT_LOG") 2>&1
}

require_command git
require_command tee

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pp_error "Not inside a git work tree."
  exit 1
fi

configure_build_loop_state_paths
acquire_lock
write_lock_value max-cycles "$MAX_CYCLES"
write_lock_value cycle "0"
write_lock_value ticket ""
set_loop_phase "starting"
start_run_logs

pp_banner "Autonomous build loop"
pp_kv "Max cycles" "$MAX_CYCLES"
pp_kv "Sleep" "${SLEEP_SECONDS}s"
pp_kv "Agent retry sleep" "${AGENT_RETRY_SECONDS}s"
pp_kv "Agent log format" "$AGENT_OUTPUT_MODE"
pp_kv "Agent details" "just follow (hidden from launcher)"
pp_kv "Push after commit" "$(pp_on_off "$PUSH_AFTER")"
pp_kv "PR/MR mode" "$PR_MODE"
if [[ "$PR_MODE" != "none" ]]; then
  pp_kv "PR/MR provider" "$PR_PROVIDER"
  pp_kv "PR/MR remote" "$PR_REMOTE_NAME"
  if [[ -n "$PR_BASE_BRANCH" ]]; then
    pp_kv "PR/MR base" "$PR_BASE_BRANCH"
  else
    pp_kv "PR/MR base" "auto"
  fi
fi
if [[ -n "$SELECT_BRANCH" ]]; then
  pp_kv "Select branch" "$SELECT_BRANCH"
elif [[ -n "$CREATE_BRANCH" ]]; then
  pp_kv "Create branch" "$CREATE_BRANCH"
  pp_kv "Branch start" "$BRANCH_START_POINT"
fi
pp_kv "Allow ahead" "$(pp_on_off "$ALLOW_AHEAD")"
pp_kv "State dir" "$BUILD_LOOP_STATE_DIR"
pp_kv "Launcher log" "$CURRENT_LOG"
pp_kv "Follow log" "$FOLLOW_LOG"
pp_kv "Cycle logs" "$LOG_DIR"
pp_kv "Follow" "just follow"
pp_kv "Progress monitor" "just monitor 10"
pp_kv "Graceful stop" "just stop"

if [[ -n "$SELECT_BRANCH" || -n "$CREATE_BRANCH" ]]; then
  pp_section "Branch setup"
  git_branch_prepare "$SELECT_BRANCH" "$CREATE_BRANCH" "$BRANCH_START_POINT" || exit $?
fi

pp_kv "Current branch" "$(git_branch_current)"

for file in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "$file" ]]; then
    pp_error "Required file missing: $file"
    exit 1
  fi
done

require_customised_template
configure_pr_automation

cycle=0
log_sequence=0

while (( cycle < MAX_CYCLES )); do
  stop_if_requested "Stop requested before cycle $((cycle + 1)); the loop is stopping now."
  set_loop_phase "checking-queue"

  automation_status="$(get_automation_status)"
  if [[ -z "$automation_status" ]]; then
    pp_error "Missing top-level AUTOMATION_STATUS line in BUILD_TICKETS.md."
    exit 1
  fi
  if [[ "$automation_status" == "DONE" ]]; then
    set_loop_phase "completed"
    pp_success "Build tickets marked done."
    exit 0
  fi

  cycle=$((cycle + 1))
  write_lock_value cycle "$cycle"
  pp_banner "Autonomous build cycle" "$cycle/$MAX_CYCLES"

  pp_section "Current work"
  next_ticket="$(get_next_ticket_summary)"
  write_lock_value ticket "$next_ticket"
  if [[ -n "$next_ticket" ]]; then
    pp_info "Now working on: ticket $next_ticket"
  else
    pp_warn "No TODO ticket found; agent will inspect BUILD_TICKETS.md."
  fi

  set_loop_phase "pre-flight"
  pp_section "Pre-flight checks"
  sync_before_cycle
  stop_if_requested "Stop requested during pre-flight; cycle $cycle did not launch an agent."

  before_head="$(git rev-parse HEAD)"
  mkdir -p "$LOG_DIR"
  log_sequence=$((log_sequence + 1))
  log_file="$LOG_DIR/cycle-$(date +%Y%m%d-%H%M%S)-$cycle-$log_sequence.log"

  pp_kv "Log file" "$log_file"
  pp_section "Agent run"
  set_loop_phase "agent"
  stop_if_requested "Stop requested before the agent launched; cycle $cycle did not start work."

  if run_agent_with_log "$PROMPT" "$log_file"; then
    agent_status=0
  else
    agent_status=$?
  fi

  if (( agent_status != 0 )); then
    if (( agent_status == 125 )); then
      pp_error "Agent output capture failed during cycle $cycle; stopping."
      pp_hint "See $log_file"
      exit 1
    fi

    if (( agent_status == 130 || agent_status == 143 )); then
      pp_error "Agent was interrupted during cycle $cycle; stopping."
      pp_hint "See $log_file"
      exit "$agent_status"
    fi

    pp_error "Agent failed during cycle $cycle with exit status $agent_status."
    pp_hint "See $log_file"

    set_loop_phase "failure-checkpoint"
    if ! checkpoint_failed_agent_run "$before_head" "agent failed with exit status $agent_status" "$log_file"; then
      exit 1
    fi
    stop_if_requested "Stop requested after the failed attempt was checkpointed; cycle $cycle will not be retried."

    if is_token_context_failure "$log_file"; then
      if split_current_ticket_after_context_failure; then
        stop_if_requested "Stop requested after ticket-split recovery; cycle $cycle will not be retried."
        pp_info "Continuing with the split ticket queue."
        cycle=$((cycle - 1))
        continue
      else
        split_recovery_status=$?
        if (( split_recovery_status == 2 )); then
          exit 1
        fi
        sleep_before_agent_retry "Ticket split recovery failed"
        cycle=$((cycle - 1))
        continue
      fi
    fi

    sleep_before_agent_retry "Agent failed; assuming a transient provider/server issue"
    cycle=$((cycle - 1))
    continue
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    pp_error "Agent reported success but left a dirty working tree."
    git status --short >&2
    checkpoint_failed_agent_run "$before_head" "agent left a dirty working tree after success" "$log_file" || exit 1
    sleep_before_agent_retry "Agent left a dirty working tree"
    cycle=$((cycle - 1))
    continue
  fi

  refuse_if_remote_advanced "$CYCLE_UPSTREAM_REF" "$CYCLE_UPSTREAM_HEAD"

  after_head="$(git rev-parse HEAD)"

  if [[ "$after_head" == "$before_head" ]]; then
    pp_error "Cycle completed without a new commit."
    sleep_before_agent_retry "Cycle produced no commit"
    cycle=$((cycle - 1))
    continue
  fi

  pp_success "Cycle committed $(git rev-parse --short HEAD)"

  set_loop_phase "publishing"
  publish_current_commit "cycle $cycle/$MAX_CYCLES" "$after_head"

  automation_status="$(get_automation_status)"
  if [[ "$automation_status" == "DONE" ]]; then
    set_loop_phase "completed"
    pp_success "Build tickets marked done."
    exit 0
  fi

  stop_if_requested "Cycle $cycle/$MAX_CYCLES finished; graceful stop requested."

  if (( SLEEP_SECONDS > 0 )); then
    set_loop_phase "cycle-wait"
    pp_info "Sleeping ${SLEEP_SECONDS}s before next cycle."
    sleep_with_stop_checks \
      "$SLEEP_SECONDS" \
      "Stop requested during the cycle pause after cycle $cycle/$MAX_CYCLES."
  fi
done

set_loop_phase "completed"
pp_success "Reached the configured maximum of $MAX_CYCLES cycle(s)."
