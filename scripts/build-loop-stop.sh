#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"
# shellcheck source=scripts/lib/build-loop-state.sh
source "$SCRIPT_DIR/lib/build-loop-state.sh"

usage() {
  cat <<'USAGE'
Usage: scripts/build-loop-stop.sh

Requests a graceful stop for the active repository build loop. The current
agent attempt and successful cycle publication are allowed to finish, but no
new cycle or failed-attempt retry is started.
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      pp_error "Unknown argument: $1"
      usage >&2
      exit 2
      ;;
  esac
fi

if ! command -v git >/dev/null 2>&1 || ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pp_error "Run this command from inside the repository."
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel)"
if ! state_dir="$(build_loop_resolve_state_dir "$repo_root")"; then
  pp_error "Unable to resolve the build-loop state directory."
  exit 1
fi

lock_dir="$(build_loop_lock_dir "$state_dir")"
stop_request_file="$(build_loop_stop_request_file "$state_dir")"
loop_pid="$(build_loop_active_pid "$state_dir" 2>/dev/null || true)"

if [[ -z "$loop_pid" ]]; then
  pp_info "No active build loop was found; nothing needs to stop."
  exit 0
fi

cycle="$(build_loop_read_lock_value "$state_dir" cycle 2>/dev/null || true)"
max_cycles="$(build_loop_read_lock_value "$state_dir" max-cycles 2>/dev/null || true)"
ticket="$(build_loop_read_lock_value "$state_dir" ticket 2>/dev/null || true)"
phase="$(build_loop_read_lock_value "$state_dir" phase 2>/dev/null || true)"

if [[ -f "$stop_request_file" ]]; then
  pp_info "A graceful stop has already been requested for build loop PID $loop_pid."
else
  temporary_file="$lock_dir/.stop-requested.$$"
  if ! printf 'requested_at=%s\nrequested_by_pid=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$$" > "$temporary_file" 2>/dev/null; then
    if ! kill -0 "$loop_pid" 2>/dev/null; then
      pp_info "The build loop exited before the stop request was recorded."
      exit 0
    fi
    pp_error "Could not prepare the graceful stop request: $stop_request_file"
    exit 1
  fi

  if ! mv "$temporary_file" "$stop_request_file" 2>/dev/null; then
    rm -f "$temporary_file"
    if ! kill -0 "$loop_pid" 2>/dev/null; then
      pp_info "The build loop exited before the stop request was recorded."
      exit 0
    fi
    pp_error "Could not record the graceful stop request: $stop_request_file"
    exit 1
  fi

  pp_success "Graceful stop requested for build loop PID $loop_pid."
fi

if [[ -n "$cycle" && -n "$max_cycles" ]]; then
  pp_kv "Current cycle" "$cycle/$max_cycles"
fi
if [[ -n "$ticket" ]]; then
  pp_kv "Ticket" "$ticket"
fi
if [[ -n "$phase" ]]; then
  pp_kv "Phase" "$phase"
fi
pp_info "The current attempt and successful publication may finish; no new cycle or retry will start."
pp_hint "Monitor it with: just follow"
