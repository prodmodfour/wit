#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

fail() {
  pp_error "$*"
  exit 1
}

assert_file_contains() {
  local file="$1"
  local expected="$2"
  local message="$3"

  grep -Fq -- "$expected" "$file" || fail "$message"
}

assert_file_omits() {
  local file="$1"
  local unexpected="$2"
  local message="$3"

  if grep -Fq -- "$unexpected" "$file"; then
    fail "$message"
  fi
}

tmp_dir="$(mktemp -d)"
fake_loop_pid=""
cleanup() {
  if [[ -n "$fake_loop_pid" ]]; then
    kill "$fake_loop_pid" 2>/dev/null || true
    wait "$fake_loop_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

work_dir="$tmp_dir/work"
state_dir="$tmp_dir/state"
mkdir -p "$work_dir" "$state_dir/lock" "$state_dir/logs"

git init -q "$work_dir"
(
  cd "$work_dir"
  git config user.name "Build Monitor Test"
  git config user.email "build-monitor-test@example.invalid"
  printf '%s\n' 'initial fixture' > work.txt
  git add work.txt
  git commit -q -m "test: seed monitor fixture"
  printf '%s\n' 'PRIVATE_SOURCE_BODY_SHOULD_NOT_LEAVE_THE_REPOSITORY' >> work.txt
)

cat > "$tmp_dir/fake-analyzer" <<'ANALYZER'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_ANALYZER_ARGS_LOG:?}"
: "${FAKE_ANALYZER_INPUT_LOG:?}"
: "${FAKE_ANALYZER_COUNT_FILE:?}"

count=0
if [[ -f "$FAKE_ANALYZER_COUNT_FILE" ]]; then
  IFS= read -r count < "$FAKE_ANALYZER_COUNT_FILE"
fi
count=$((count + 1))
printf '%s\n' "$count" > "$FAKE_ANALYZER_COUNT_FILE"
printf '%s\n' "$@" > "$FAKE_ANALYZER_ARGS_LOG"
cat > "$FAKE_ANALYZER_INPUT_LOG"
cp "$FAKE_ANALYZER_INPUT_LOG" "$FAKE_ANALYZER_INPUT_LOG.$count"

if [[ -n "${FAKE_ANALYZER_EVENT_CAPTURE:-}" ]]; then
  analyzer_read_line="$(grep -E '^ANALYZER READ FILE: ' "$FAKE_ANALYZER_INPUT_LOG" | head -1)"
  analyzer_read_file="${analyzer_read_line#ANALYZER READ FILE: }"
  if [[ -n "$analyzer_read_file" && -f "$analyzer_read_file" ]]; then
    cat "$analyzer_read_file" > "$FAKE_ANALYZER_EVENT_CAPTURE"
  fi
fi

if [[ -n "${FAKE_ANALYZER_REMOVE_LOCK_AFTER_FIRST:-}" && "$count" == "1" ]]; then
  rm -rf "$FAKE_ANALYZER_REMOVE_LOCK_AFTER_FIRST"
fi

if [[ -n "${FAKE_ANALYZER_KILL_PID_AFTER_SECOND:-}" && "$count" == "2" ]]; then
  kill "$FAKE_ANALYZER_KILL_PID_AFTER_SECOND" 2>/dev/null || true
fi

if [[ "${FAKE_ANALYZER_FAIL:-0}" == "1" ]]; then
  printf '%s\n' 'fixture analyzer unavailable' >&2
  exit 23
fi

cat <<'REPORT'
**12:34 update — focused implementation is advancing.**

The fixture agent edited a tracked file and its recent rendered activity shows targeted validation rather than idle waiting.

**Interpretation:** The evidence represents active progress, not a ticket-title restatement.
REPORT
ANALYZER
chmod +x "$tmp_dir/fake-analyzer"

sleep 60 &
fake_loop_pid=$!
printf '%s\n' "$fake_loop_pid" > "$state_dir/lock/pid"
printf '%s\n' '3' > "$state_dir/lock/cycle"
printf '%s\n' '12' > "$state_dir/lock/max-cycles"
printf '%s\n' 'MA-123 — Fixture ticket (TODO)' > "$state_dir/lock/ticket"
printf '%s\n' 'agent' > "$state_dir/lock/phase"
cat > "$state_dir/logs/cycle-fixture.log" <<'LOG'
  00:01  • Agent started.
  00:05  → [01] Edit file — work.txt (1 replacement)
  00:07  ✓ [02] Run tests (2.0s)
LOG
cat > "$state_dir/logs/cycle-fixture.pi-events.jsonl" <<'JSONL'
{"type":"agent_start"}
{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","delta":"FULL_PI_THINKING_EVENT","partial":{"text":"REDUNDANT_PARTIAL_SNAPSHOT"}},"message":{"role":"assistant","content":[{"type":"thinking","thinking":"REDUNDANT_CUMULATIVE_MESSAGE"}]}}
{"type":"tool_execution_start","toolCallId":"read-1","toolName":"read","args":{"path":"work.txt","offset":1,"limit":20}}
{"type":"tool_execution_end","toolCallId":"read-1","toolName":"read","result":{"content":[{"type":"text","text":"FULL_SUCCESSFUL_PI_TOOL_RESULT"}]},"isError":false}
JSONL
node -e 'const fs = require("fs"); fs.appendFileSync(process.argv[1], JSON.stringify({ type: "tool_execution_end", result: "x".repeat(60000) + "FULL_LONG_EVENT_TAIL" }) + "\n")' "$state_dir/logs/cycle-fixture.pi-events.jsonl"
chmod 600 "$state_dir/logs/cycle-fixture.pi-events.jsonl"

pp_step "Regression: event projection stays bounded for a sparse/corrupt source line"
oversized_event_log="$tmp_dir/oversized.pi-events.jsonl"
printf '%s\n' '{"type":"agent_start"}' > "$oversized_event_log"
truncate -s $((128 * 1024 * 1024)) "$oversized_event_log"
printf '\n' >> "$oversized_event_log"
(
  ulimit -c 0
  node --max-old-space-size=32 \
    "$SCRIPT_DIR/prepare-pi-event-range.mjs" \
    "$oversized_event_log" \
    1 \
    2 \
    "$tmp_dir/oversized-events.txt"
)
assert_file_contains "$tmp_dir/oversized-events.txt" \
  'event-line-exceeds-safe-projection-limit' \
  "event projection did not bound an oversized source line"
if (( $(stat -c '%s' "$tmp_dir/oversized-events.txt") > 100000 )); then
  fail "event projection copied an oversized source line into the analyzer view"
fi

cat > "$state_dir/current.log" <<'LOG'
Autonomous build cycle 3/12
Agent run in progress.
LOG
cat > "$state_dir/follow.log" <<'LOG'
Detailed fixture activity.
LOG

common_env=(
  "NO_COLOR=1"
  "AUTONOMOUS_BUILD_LOOP_STATE_DIR=$state_dir"
  "PI_MONITOR_AGENT_COMMAND=$tmp_dir/fake-analyzer"
  "FAKE_ANALYZER_ARGS_LOG=$tmp_dir/analyzer-args.log"
  "FAKE_ANALYZER_INPUT_LOG=$tmp_dir/analyzer-input.log"
  "FAKE_ANALYZER_COUNT_FILE=$tmp_dir/analyzer-count"
  "FAKE_ANALYZER_EVENT_CAPTURE=$tmp_dir/analyzer-events.jsonl"
)

pp_step "Regression: monitor produces an immediate interpreted, read-only report"
(
  cd "$work_dir"
  env "${common_env[@]}" \
    bash "$SCRIPT_DIR/build-loop-monitor.sh" --interval-minutes 7 --once
) > "$tmp_dir/monitor.log" 2>&1

assert_file_contains "$tmp_dir/monitor.log" \
  'Monitoring autonomous build loop' \
  "monitor did not print its startup banner"
assert_file_contains "$tmp_dir/monitor.log" \
  'The first interpreted report is generated immediately.' \
  "monitor did not explain immediate first-report behavior"
assert_file_contains "$tmp_dir/monitor.log" \
  '**12:34 update — focused implementation is advancing.**' \
  "monitor omitted the analyzer's interpreted report"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'Ticket lock summary: MA-123 — Fixture ticket (TODO)' \
  "monitor snapshot omitted ticket context"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'Loop phase: agent' \
  "monitor snapshot omitted loop phase"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'Edit file — work.txt' \
  "monitor snapshot omitted recent rendered activity"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'FULL PI TERMINAL-EQUIVALENT EVENT STREAM:' \
  "monitor snapshot omitted full Pi event-stream access"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  "Source path: $state_dir/logs/cycle-fixture.pi-events.jsonl" \
  "monitor snapshot omitted the full Pi event-log path"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'SOURCE RANGE: lines 1 through 5 inclusive.' \
  "monitor did not require inspection of the complete Pi stream"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'ANALYZER READ FILE:' \
  "monitor did not provide a complete read-tool-safe event view"
assert_file_contains "$tmp_dir/analyzer-events.jsonl" \
  'FULL_PI_THINKING_EVENT' \
  "monitor analyzer could not access emitted Pi thinking events"
assert_file_contains "$tmp_dir/analyzer-events.jsonl" \
  'FULL_SUCCESSFUL_PI_TOOL_RESULT' \
  "monitor analyzer could not access successful Pi tool results"
assert_file_contains "$tmp_dir/analyzer-events.jsonl" \
  'FULL_LONG_EVENT_TAIL' \
  "monitor analyzer could not access the tail of an oversized Pi event"
assert_file_contains "$tmp_dir/analyzer-events.jsonl" \
  'PI EVENT LINE 5 SEGMENT 2/' \
  "monitor did not segment an oversized Pi event for complete read access"
assert_file_contains "$tmp_dir/analyzer-events.jsonl" \
  'NORMALIZED PI EVENT VIEW' \
  "monitor did not identify its normalized analyzer view"
assert_file_omits "$tmp_dir/analyzer-events.jsonl" \
  'REDUNDANT_PARTIAL_SNAPSHOT' \
  "monitor retained a redundant cumulative partial snapshot"
assert_file_omits "$tmp_dir/analyzer-events.jsonl" \
  'REDUNDANT_CUMULATIVE_MESSAGE' \
  "monitor retained a redundant cumulative message snapshot"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'work.txt | 1 +' \
  "monitor snapshot omitted the Git change summary"
assert_file_contains "$tmp_dir/analyzer-input.log" \
  'sleep' \
  "monitor snapshot omitted active process evidence"
assert_file_omits "$tmp_dir/analyzer-input.log" \
  'PRIVATE_SOURCE_BODY_SHOULD_NOT_LEAVE_THE_REPOSITORY' \
  "monitor sent source-file contents to the analyzer"

for required_arg in \
  --no-session \
  --tools \
  read \
  --no-context-files \
  --no-extensions \
  --no-skills \
  --no-prompt-templates \
  --no-themes \
  --no-approve \
  --print; do
  grep -Fxq -- "$required_arg" "$tmp_dir/analyzer-args.log" \
    || fail "monitor analyzer omitted safety argument $required_arg"
done
if grep -Fxq -- '--no-tools' "$tmp_dir/analyzer-args.log"; then
  fail "monitor disabled the read tool required for full Pi event access"
fi

if [[ "$(< "$tmp_dir/analyzer-count")" != "1" ]]; then
  fail "monitor did not invoke the analyzer exactly once in --once mode"
fi

pp_step "Regression: monitor distinguishes analyzer failure from build failure"
(
  cd "$work_dir"
  env "${common_env[@]}" FAKE_ANALYZER_FAIL=1 \
    bash "$SCRIPT_DIR/build-loop-monitor.sh" --interval-minutes 7 --once
) > "$tmp_dir/analyzer-failure.log" 2>&1
assert_file_contains "$tmp_dir/analyzer-failure.log" \
  'analyzer unavailable; loop still active' \
  "monitor did not provide a bounded analyzer-failure fallback"
assert_file_contains "$tmp_dir/analyzer-failure.log" \
  'This is a monitoring failure, not evidence that the build itself failed.' \
  "monitor fallback confused analyzer failure with build failure"

pp_step "Regression: monitor survives lock loss and notices the actual process exit"
mkdir -p "$tmp_dir/fake-bin"
cat > "$tmp_dir/fake-bin/sleep" <<'FAKE_SLEEP'
#!/usr/bin/env bash
exit 0
FAKE_SLEEP
chmod +x "$tmp_dir/fake-bin/sleep"
printf '%s\n' '0' > "$tmp_dir/analyzer-count"
(
  cd "$work_dir"
  env "${common_env[@]}" \
    "PATH=$tmp_dir/fake-bin:$PATH" \
    "FAKE_ANALYZER_REMOVE_LOCK_AFTER_FIRST=$state_dir/lock" \
    "FAKE_ANALYZER_KILL_PID_AFTER_SECOND=$fake_loop_pid" \
    bash "$SCRIPT_DIR/build-loop-monitor.sh" --interval-minutes 7
) > "$tmp_dir/repeating-monitor.log" 2>&1
if [[ "$(< "$tmp_dir/analyzer-count")" != "3" ]]; then
  fail "monitor did not retain the live process after lock loss, then report its exit"
fi
if [[ "$(grep -Fc '**12:34 update — focused implementation is advancing.**' "$tmp_dir/repeating-monitor.log")" != "3" ]]; then
  fail "monitor did not print the immediate, lock-loss, and final reports"
fi
assert_file_contains "$tmp_dir/analyzer-input.log.2" \
  'Loop active: yes' \
  "monitor mistook lock loss for process exit"
assert_file_contains "$tmp_dir/analyzer-input.log.2" \
  'Loop state lock: missing; original process identity remains active' \
  "monitor did not explain its process-identity fallback"
assert_file_contains "$tmp_dir/analyzer-input.log.3" \
  'Loop active: no' \
  "final monitor snapshot did not record the actual process exit"
assert_file_contains "$tmp_dir/analyzer-input.log.3" \
  'PREVIOUS INTERPRETED REPORT (comparison context only):' \
  "repeated monitor snapshot omitted prior-report comparison context"
assert_file_contains "$tmp_dir/repeating-monitor.log" \
  'Build loop exited; monitor finished after its final report.' \
  "monitor did not exit cleanly after its final report"

pp_step "Regression: monitor validates intervals and exits cleanly without a loop"
set +e
(
  cd "$work_dir"
  env "${common_env[@]}" \
    bash "$SCRIPT_DIR/build-loop-monitor.sh" --interval-minutes 0 --once
) > "$tmp_dir/invalid-interval.log" 2>&1
invalid_status=$?
set -e
if [[ "$invalid_status" -ne 2 ]]; then
  fail "monitor accepted a non-positive interval (status $invalid_status)"
fi
assert_file_contains "$tmp_dir/invalid-interval.log" \
  '--interval-minutes must be a positive integer' \
  "monitor did not explain interval validation"

kill "$fake_loop_pid" 2>/dev/null || true
wait "$fake_loop_pid" 2>/dev/null || true
fake_loop_pid=""
(
  cd "$work_dir"
  env "${common_env[@]}" \
    bash "$SCRIPT_DIR/build-loop-monitor.sh" --interval-minutes 7 --once
) > "$tmp_dir/no-loop.log" 2>&1
assert_file_contains "$tmp_dir/no-loop.log" \
  'No active build loop was found for this repository.' \
  "monitor did not report an inactive loop"
if [[ "$(< "$tmp_dir/analyzer-count")" != "3" ]]; then
  fail "monitor invoked the analyzer when no build loop was active"
fi

pp_success "Build-loop monitor regressions passed."
