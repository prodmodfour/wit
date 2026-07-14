#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"
# shellcheck source=scripts/lib/build-loop-state.sh
source "$SCRIPT_DIR/lib/build-loop-state.sh"

usage() {
  cat <<'USAGE'
Usage: scripts/build-loop-follow.sh [--lines N]

Follows the active repository build loop without controlling it.
Ctrl-C detaches the follower and does not stop the build loop.

Options:
--lines N   Show this many existing log lines before following. Default: 40.
-h, --help  Show this help.
USAGE
}

LINES=40

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lines)
      if [[ $# -lt 2 ]]; then
        pp_error "--lines requires a value"
        exit 2
      fi
      LINES="$2"
      shift 2
      ;;
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
done

if ! [[ "$LINES" =~ ^[0-9]+$ ]]; then
  pp_error "--lines must be a non-negative integer"
  exit 2
fi

if ! command -v git >/dev/null 2>&1 || ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pp_error "Run this command from inside the repository."
  exit 1
fi

if ! command -v tail >/dev/null 2>&1; then
  pp_error "Required command not found: tail"
  exit 127
fi

repo_root="$(git rev-parse --show-toplevel)"
if ! state_dir="$(build_loop_resolve_state_dir "$repo_root")"; then
  pp_error "Unable to resolve the build-loop state directory."
  exit 1
fi

lock_dir="$(build_loop_lock_dir "$state_dir")"
follow_log="$(build_loop_follow_log "$state_dir")"
stop_request_file="$(build_loop_stop_request_file "$state_dir")"
loop_pid="$(build_loop_active_pid "$state_dir" 2>/dev/null || true)"

if [[ -z "$loop_pid" ]]; then
  pp_warn "No active build loop was found for this repository."
  if [[ -f "$follow_log" ]]; then
    pp_kv "Latest follow log" "$follow_log"
    pp_hint "Inspect it with: tail -n $LINES '$follow_log'"
  else
    pp_hint "Start one with: just run"
  fi
  exit 0
fi

for _ in {1..50}; do
  if [[ -f "$follow_log" ]]; then
    break
  fi
  if ! kill -0 "$loop_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

if [[ ! -f "$follow_log" ]]; then
  pp_error "The active build loop has not created its follow log: $follow_log"
  exit 1
fi

cycle="$(build_loop_read_lock_value "$state_dir" cycle 2>/dev/null || true)"
max_cycles="$(build_loop_read_lock_value "$state_dir" max-cycles 2>/dev/null || true)"
ticket="$(build_loop_read_lock_value "$state_dir" ticket 2>/dev/null || true)"
phase="$(build_loop_read_lock_value "$state_dir" phase 2>/dev/null || true)"

pp_banner "Following autonomous build loop"
pp_kv "PID" "$loop_pid"
if [[ -n "$cycle" && -n "$max_cycles" ]]; then
  pp_kv "Cycle" "$cycle/$max_cycles"
fi
if [[ -n "$ticket" ]]; then
  pp_kv "Ticket" "$ticket"
fi
if [[ -n "$phase" ]]; then
  pp_kv "Phase" "$phase"
fi
pp_kv "Log" "$follow_log"
if [[ -f "$stop_request_file" ]]; then
  pp_kv "Status" "graceful stop requested"
fi
pp_info "Ctrl-C detaches this follower; the build loop keeps running."
pp_blank

tail -n "$LINES" -F "$follow_log" &
tail_pid=$!
stop_notice_shown=0
if [[ -f "$stop_request_file" ]]; then
  stop_notice_shown=1
fi

cleanup_tail() {
  if kill -0 "$tail_pid" 2>/dev/null; then
    kill "$tail_pid" 2>/dev/null || true
  fi
  wait "$tail_pid" 2>/dev/null || true
}

handle_interrupt() {
  cleanup_tail
  pp_blank
  pp_info "Follower detached; build loop PID $loop_pid was not interrupted."
  exit 130
}

trap handle_interrupt INT TERM

while [[ -d "$lock_dir" ]] && kill -0 "$loop_pid" 2>/dev/null; do
  if ! kill -0 "$tail_pid" 2>/dev/null; then
    pp_error "Log follower exited unexpectedly."
    wait "$tail_pid" 2>/dev/null || true
    exit 1
  fi

  if (( stop_notice_shown == 0 )) && [[ -f "$stop_request_file" ]]; then
    pp_warn "Graceful stop requested; the active cycle or attempt will finish before exit."
    stop_notice_shown=1
  fi

  sleep 1
done

sleep 0.2
cleanup_tail
trap - INT TERM
pp_blank
pp_success "Build loop exited; follower finished."
