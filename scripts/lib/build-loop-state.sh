#!/usr/bin/env bash
# Shared state-path and active-loop helpers for build-loop commands.
# Source this file; do not execute it directly.

if [[ -n "${_BUILD_LOOP_STATE_SH:-}" ]]; then
  return 0
fi
_BUILD_LOOP_STATE_SH=1

build_loop_sanitize_state_component() {
  local value="$1"
  local sanitized

  sanitized="$(printf '%s' "$value" | tr -c 'A-Za-z0-9._-' '-')"
  sanitized="${sanitized:0:80}"

  if [[ -z "$sanitized" ]]; then
    sanitized="repo"
  fi

  printf '%s\n' "$sanitized"
}

build_loop_resolve_state_dir() {
  local repo_root="$1"
  local repo_name
  local repo_slug
  local repo_hash
  local state_home

  if [[ -n "${AUTONOMOUS_BUILD_LOOP_STATE_DIR:-}" ]]; then
    printf '%s\n' "$AUTONOMOUS_BUILD_LOOP_STATE_DIR"
    return 0
  fi

  if [[ -n "${XDG_STATE_HOME:-}" ]]; then
    state_home="$XDG_STATE_HOME"
  elif [[ -n "${HOME:-}" ]]; then
    state_home="$HOME/.local/state"
  else
    printf '%s\n' 'HOME must be set when XDG_STATE_HOME and AUTONOMOUS_BUILD_LOOP_STATE_DIR are not set.' >&2
    return 1
  fi

  repo_name="$(basename "$repo_root")"
  repo_slug="$(build_loop_sanitize_state_component "$repo_name")"
  repo_hash="$(printf '%s' "$repo_root" | git hash-object --stdin | cut -c 1-12)"

  printf '%s/autonomous-build-template/build-loop/%s-%s\n' \
    "$state_home" \
    "$repo_slug" \
    "$repo_hash"
}

build_loop_lock_dir() {
  printf '%s/lock\n' "$1"
}

build_loop_log_dir() {
  printf '%s/logs\n' "$1"
}

build_loop_current_log() {
  printf '%s/current.log\n' "$1"
}

build_loop_follow_log() {
  printf '%s/follow.log\n' "$1"
}

build_loop_pi_event_log() {
  local rendered_log="$1"

  if [[ "$rendered_log" == *.log ]]; then
    printf '%s.pi-events.jsonl\n' "${rendered_log%.log}"
  else
    printf '%s.pi-events.jsonl\n' "$rendered_log"
  fi
}

build_loop_stop_request_file() {
  printf '%s/stop-requested\n' "$(build_loop_lock_dir "$1")"
}

build_loop_read_lock_value() {
  local state_dir="$1"
  local name="$2"
  local value_file
  local value=""

  value_file="$(build_loop_lock_dir "$state_dir")/$name"
  if [[ ! -f "$value_file" ]]; then
    return 1
  fi

  IFS= read -r value < "$value_file" || true
  printf '%s\n' "${value:-}"
}

build_loop_active_pid() {
  local state_dir="$1"
  local pid

  pid="$(build_loop_read_lock_value "$state_dir" pid 2>/dev/null || true)"
  if ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    return 1
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi

  printf '%s\n' "$pid"
}
