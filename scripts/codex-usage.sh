#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

usage() {
  cat <<'USAGE'
Usage: scripts/codex-usage.sh

Sends one minimal Codex request through a Pi-compatible command using a
process-local SSE transport override, then reports the active usage windows.

The command does not run login/logout, create a Pi session, or persist transport
settings. A normal OAuth access-token refresh may occur if Pi needs one.

Environment:
PI_CODEX_USAGE_COMMAND
                      Pi-compatible Codex command. Defaults to
                      PI_AGENT_COMMAND, then pi.
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

CODEX_COMMAND="${PI_CODEX_USAGE_COMMAND:-${PI_AGENT_COMMAND:-pi}}"

if ! command -v "$CODEX_COMMAND" >/dev/null 2>&1; then
  pp_error "Pi command not found: $CODEX_COMMAND"
  pp_hint "Install Pi or set PI_CODEX_USAGE_COMMAND for this invocation."
  exit 127
fi
if ! command -v node >/dev/null 2>&1; then
  pp_error "Node.js is required to format Codex usage headers."
  exit 127
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM

mkdir -p "$tmp_dir/.pi"
rate_log="$tmp_dir/rate-headers.jsonl"
stdout_log="$tmp_dir/pi.stdout"
stderr_log="$tmp_dir/pi.stderr"

cat > "$tmp_dir/.pi/settings.json" <<'JSON'
{
  "transport": "sse"
}
JSON

cat > "$tmp_dir/rate-headers.ts" <<'TYPESCRIPT'
import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const outputPath = process.env.CODEX_RATE_HEADER_LOG;
const allowedHeader = /^(?:retry-after(?:-ms)?|x-codex-(?:active-limit|plan-type|primary-.+|secondary-.+|credits-.+))$/i;

export default function (pi: ExtensionAPI) {
  pi.on("after_provider_response", (event) => {
    if (!outputPath) return;

    const headers = Object.fromEntries(
      Object.entries(event.headers)
        .filter(([name]) => allowedHeader.test(name))
        .sort(([left], [right]) => left.localeCompare(right)),
    );

    appendFileSync(
      outputPath,
      `${JSON.stringify({ status: event.status, headers })}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
  });
}
TYPESCRIPT

pp_banner "Codex usage"
pp_info "Sending one minimal SSE request; no login/logout or persistent setting change is performed."

set +e
(
  cd "$tmp_dir"
  PI_SKIP_VERSION_CHECK=1 \
  PI_TELEMETRY=0 \
  CODEX_RATE_HEADER_LOG="$rate_log" \
    "$CODEX_COMMAND" \
      --approve \
      --no-session \
      --no-context-files \
      --no-extensions \
      --extension "$tmp_dir/rate-headers.ts" \
      --no-skills \
      --no-prompt-templates \
      --no-themes \
      --no-tools \
      --thinking off \
      --print 'Reply only with OK.' \
      > "$stdout_log" \
      2> "$stderr_log"
)
pi_status=$?
set -e

if [[ ! -s "$rate_log" ]]; then
  pp_error "Pi did not expose Codex rate-limit headers (exit status $pi_status)."
  diagnostic="$(grep -E -i 'Codex error|usage limit|authentication|not logged in|API key|429' "$stderr_log" "$stdout_log" 2>/dev/null | tail -1 || true)"
  if [[ -n "$diagnostic" ]]; then
    pp_hint "${diagnostic#*:}"
  else
    pp_hint "No safe provider diagnostic was captured."
  fi
  exit 1
fi

node - "$rate_log" <<'NODE'
const { readFileSync } = require("node:fs");

const path = process.argv[2];
const records = readFileSync(path, "utf8")
  .split("\n")
  .filter(Boolean)
  .map((line) => JSON.parse(line));
const record = [...records].reverse().find((candidate) =>
  candidate?.headers?.["x-codex-primary-window-minutes"] !== undefined
  && candidate?.headers?.["x-codex-secondary-window-minutes"] !== undefined
);

if (!record) {
  console.error("Codex response omitted primary/secondary window headers.");
  process.exit(1);
}

const headers = record.headers;
const numericHeader = (name) => {
  const value = Number(headers[name]);
  return Number.isFinite(value) ? value : undefined;
};
const textHeader = (name) => headers[name] ?? "unknown";
const formatWindow = (minutes) => {
  if (minutes === 300) return "5 hours";
  if (minutes === 10080) return "7 days";
  return minutes === undefined ? "unknown" : `${minutes} minutes`;
};
const formatWindowName = (minutes) => {
  if (minutes === 300) return "5-hour";
  if (minutes === 10080) return "weekly";
  return minutes === undefined ? "unknown" : `${minutes}-minute`;
};
const isActiveWindow = (minutes) => minutes !== undefined && minutes > 0;
const formatDuration = (seconds) => {
  if (seconds === undefined) return "unknown";
  let remaining = Math.max(0, Math.round(seconds));
  const units = [
    ["d", 86400],
    ["h", 3600],
    ["m", 60],
    ["s", 1],
  ];
  const parts = [];
  for (const [label, size] of units) {
    const amount = Math.floor(remaining / size);
    remaining %= size;
    if (amount > 0 || (label === "s" && parts.length === 0)) parts.push(`${amount}${label}`);
    if (parts.length === 2) break;
  }
  return parts.join(" ");
};
const formatReset = (epochSeconds) => {
  if (epochSeconds === undefined || epochSeconds <= 0) return "unknown";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(new Date(epochSeconds * 1000));
};

const primaryWindow = numericHeader("x-codex-primary-window-minutes");
const primaryUsed = numericHeader("x-codex-primary-used-percent");
const primaryResetAfter = numericHeader("x-codex-primary-reset-after-seconds");
const primaryResetAt = numericHeader("x-codex-primary-reset-at");
const secondaryWindow = numericHeader("x-codex-secondary-window-minutes");
const secondaryUsed = numericHeader("x-codex-secondary-used-percent");
const secondaryResetAfter = numericHeader("x-codex-secondary-reset-after-seconds");
const secondaryResetAt = numericHeader("x-codex-secondary-reset-at");
const primaryActive = isActiveWindow(primaryWindow);
const secondaryActive = isActiveWindow(secondaryWindow);
const primaryLimited = primaryActive && primaryUsed !== undefined && primaryUsed >= 100;
const secondaryLimited = secondaryActive && secondaryUsed !== undefined && secondaryUsed >= 100;

let result;
if (primaryLimited && secondaryLimited) {
  result = `Both the ${formatWindowName(primaryWindow)} and ${formatWindowName(secondaryWindow)} windows are exhausted.`;
} else if (primaryLimited) {
  result = secondaryActive
    ? `The ${formatWindowName(primaryWindow)} window is exhausted; the ${formatWindowName(secondaryWindow)} window has remaining capacity.`
    : `The ${formatWindowName(primaryWindow)} window is exhausted.`;
} else if (secondaryLimited) {
  result = `The ${formatWindowName(secondaryWindow)} window is exhausted.`;
} else if (record.status === 429) {
  result = "Codex returned HTTP 429, but neither reported window is at 100%.";
} else {
  result = "Codex is currently available.";
}

const percent = (value) => value === undefined ? "unknown" : `${value}%`;
console.log(`  HTTP status:          ${record.status}`);
console.log(`  Plan:                 ${textHeader("x-codex-plan-type")}`);
console.log(`  Active limit:         ${textHeader("x-codex-active-limit")}`);
if (primaryActive) {
  console.log(`  Primary (${formatWindow(primaryWindow)}): ${percent(primaryUsed)} used`);
  console.log(`  Primary reset:        in ${formatDuration(primaryResetAfter)} at ${formatReset(primaryResetAt)}`);
} else {
  console.log("  Primary:              not active");
}
if (secondaryActive) {
  console.log(`  Secondary (${formatWindow(secondaryWindow)}): ${percent(secondaryUsed)} used`);
  console.log(`  Secondary reset:      in ${formatDuration(secondaryResetAfter)} at ${formatReset(secondaryResetAt)}`);
} else {
  console.log("  Secondary:            not active");
}
console.log(`  Result:               ${result}`);
NODE
