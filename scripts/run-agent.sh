#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

if [[ $# -ne 1 ]]; then
  pp_error "Usage: scripts/run-agent.sh '<prompt>'"
  exit 2
fi

PROMPT="$1"
AGENT_COMMAND="${PI_AGENT_COMMAND:-pi}"
AGENT_OUTPUT_MODE="${PI_AGENT_OUTPUT_MODE:-live}"
AGENT_EVENT_LOG="${PI_AGENT_EVENT_LOG:-}"
# The sink belongs to this wrapper. Do not leak it into the agent or commands
# launched by the agent, where a nested run-agent invocation could truncate it.
unset PI_AGENT_EVENT_LOG
EVENT_RENDERER="$SCRIPT_DIR/render-agent-events.mjs"

if ! command -v "$AGENT_COMMAND" >/dev/null 2>&1; then
  pp_error "Required command not found: $AGENT_COMMAND"
  pp_hint "Set PI_AGENT_COMMAND or edit scripts/run-agent.sh if this project should use a different agent command."
  exit 127
fi

case "$AGENT_OUTPUT_MODE" in
  live|final|json) ;;
  *)
    pp_error "PI_AGENT_OUTPUT_MODE must be live, final, or json: $AGENT_OUTPUT_MODE"
    exit 2
    ;;
esac

# Intentionally no model or thinking-level flags.
# This relies on the selected local agent command configuration.

run_json_agent() {
  local render_mode="$1"
  local -a renderer_args=()
  local -a pipeline_status
  local agent_status
  local event_log_status
  local renderer_status
  local event_sink="/dev/null"

  if ! command -v node >/dev/null 2>&1; then
    pp_error "Node.js is required for $AGENT_OUTPUT_MODE agent output."
    return 127
  fi
  if ! command -v tee >/dev/null 2>&1; then
    pp_error "tee is required to capture $AGENT_OUTPUT_MODE agent output."
    return 127
  fi
  if [[ ! -f "$EVENT_RENDERER" ]]; then
    pp_error "Agent event renderer not found: $EVENT_RENDERER"
    return 127
  fi
  if [[ "$render_mode" == "raw" ]]; then
    renderer_args+=(--raw)
  fi

  if [[ -n "$AGENT_EVENT_LOG" ]]; then
    if [[ ! -d "$(dirname "$AGENT_EVENT_LOG")" ]]; then
      pp_error "Pi event-log directory does not exist: $(dirname "$AGENT_EVENT_LOG")"
      return 125
    fi
    if ! (umask 077 && : > "$AGENT_EVENT_LOG") || ! chmod 600 "$AGENT_EVENT_LOG"; then
      pp_error "Could not prepare private Pi event log: $AGENT_EVENT_LOG"
      return 125
    fi
    event_sink="$AGENT_EVENT_LOG"
  fi

  set +e
  "$AGENT_COMMAND" --no-session --mode json @AGENTS.md @PROJECT_BRIEF.md @BUILD_TICKETS.md "$PROMPT" \
    | tee "$event_sink" \
    | node "$EVENT_RENDERER" "${renderer_args[@]}"
  pipeline_status=("${PIPESTATUS[@]}")
  set -e

  agent_status="${pipeline_status[0]}"
  event_log_status="${pipeline_status[1]}"
  renderer_status="${pipeline_status[2]}"

  if (( agent_status == 130 || agent_status == 143 )); then
    return "$agent_status"
  fi
  if (( event_log_status != 0 )); then
    pp_error "Failed to capture the full Pi event stream."
    return 125
  fi
  if (( renderer_status > 1 )); then
    pp_error "Agent output renderer failed with exit status $renderer_status."
    return 125
  fi
  if (( agent_status != 0 )); then
    return "$agent_status"
  fi

  return "$renderer_status"
}

pp_step "Launching agent via $AGENT_COMMAND."

case "$AGENT_OUTPUT_MODE" in
  final)
    pp_cmd "$AGENT_COMMAND --no-session -p @AGENTS.md @PROJECT_BRIEF.md @BUILD_TICKETS.md '<prompt>'"
    "$AGENT_COMMAND" --no-session -p @AGENTS.md @PROJECT_BRIEF.md @BUILD_TICKETS.md "$PROMPT"
    ;;

  json)
    pp_cmd "$AGENT_COMMAND --no-session --mode json @AGENTS.md @PROJECT_BRIEF.md @BUILD_TICKETS.md '<prompt>'"
    run_json_agent raw
    ;;

  live)
    pp_cmd "$AGENT_COMMAND --no-session --mode json @AGENTS.md @PROJECT_BRIEF.md @BUILD_TICKETS.md '<prompt>'"
    run_json_agent live
    ;;
esac
