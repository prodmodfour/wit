#!/usr/bin/env bash
# Shared, lightweight formatting helpers for repository scripts.
# Source this file; do not execute it directly.

if [[ -n "${_PRETTY_PRINT_SH:-}" ]]; then
  return 0
fi
_PRETTY_PRINT_SH=1

_PP_COLOR=0
if [[ -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" && ( -t 1 || -t 2 ) ]]; then
  _PP_COLOR=1
fi

if (( _PP_COLOR == 1 )); then
  PP_RESET=$'\033[0m'
  PP_BOLD=$'\033[1m'
  PP_DIM=$'\033[2m'
  PP_RED=$'\033[31m'
  PP_GREEN=$'\033[32m'
  PP_YELLOW=$'\033[33m'
  PP_BLUE=$'\033[34m'
  PP_CYAN=$'\033[36m'
else
  PP_RESET=''
  PP_BOLD=''
  PP_DIM=''
  PP_RED=''
  PP_GREEN=''
  PP_YELLOW=''
  PP_BLUE=''
  PP_CYAN=''
fi

pp_blank() {
  printf '\n'
}

pp_banner() {
  local title="$1"
  local subtitle="${2:-}"

  printf '\n%s%s%s\n' "$PP_BOLD" "$title" "$PP_RESET"
  if [[ -n "$subtitle" ]]; then
    printf '%s\n' "$subtitle"
  fi
  printf '%s\n' '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
}

pp_section() {
  local title="$*"
  local rule='────────────────────────────────────────────────────────────'

  printf '\n%s%s%s\n' "$PP_BLUE" "$rule" "$PP_RESET"
  printf '%s%s▶ %s%s\n' "$PP_BLUE" "$PP_BOLD" "$title" "$PP_RESET"
  printf '%s%s%s\n' "$PP_BLUE" "$rule" "$PP_RESET"
}

pp_step() {
  printf '  %s•%s %s\n' "$PP_DIM" "$PP_RESET" "$*"
}

pp_info() {
  printf '  %sℹ%s %s\n' "$PP_CYAN" "$PP_RESET" "$*"
}

pp_success() {
  printf '  %s✓%s %s\n' "$PP_GREEN" "$PP_RESET" "$*"
}

pp_warn() {
  printf '  %s⚠%s %s\n' "$PP_YELLOW" "$PP_RESET" "$*" >&2
}

pp_error() {
  printf '  %s✕%s %s\n' "$PP_RED" "$PP_RESET" "$*" >&2
}

pp_hint() {
  printf '    %s↳%s %s\n' "$PP_DIM" "$PP_RESET" "$*" >&2
}

pp_kv() {
  local label="$1"
  local value="$2"

  printf '  %s%-20s%s %s\n' "$PP_DIM" "${label}:" "$PP_RESET" "$value"
}

pp_cmd() {
  printf '  %s$%s %s\n' "$PP_DIM" "$PP_RESET" "$*"
}

pp_on_off() {
  if (( ${1:-0} == 1 )); then
    printf 'on'
  else
    printf 'off'
  fi
}
