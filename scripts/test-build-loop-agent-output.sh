#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

pp_step "Regression: Pi JSON events render as concise live output"
cat > "$tmp_dir/events.jsonl" <<'JSON'
{"type":"session","version":3,"id":"fixture","cwd":"/tmp"}
{"type":"agent_start"}
{"type":"message_start","message":{"role":"assistant"}}
{"type":"message_update","assistantMessageEvent":{"type":"thinking_start"}}
{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","delta":"private reasoning fixture"}}
{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Now working on fixture output.\nSecond progress line."}}
{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse"}}
{"type":"tool_execution_start","toolCallId":"read-1","toolName":"read","args":{"path":"tests/fixture.ts","offset":4,"limit":8}}
{"type":"tool_execution_start","toolCallId":"bash-1","toolName":"bash","args":{"command":"npm test -- fixture"}}
{"type":"tool_execution_end","toolCallId":"bash-1","toolName":"bash","result":{"content":[{"type":"text","text":"fixture command failed"}]},"isError":true}
{"type":"tool_execution_end","toolCallId":"read-1","toolName":"read","result":{"content":[{"type":"text","text":"large successful result that should stay hidden"}]},"isError":false}
{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop"}]}
JSON

AGENT_EVENT_HEARTBEAT_SECONDS=0 \
  node "$SCRIPT_DIR/render-agent-events.mjs" \
  < "$tmp_dir/events.jsonl" \
  > "$tmp_dir/rendered.log"

grep -Fq '• Agent started.' "$tmp_dir/rendered.log" \
  || fail "live renderer omitted the agent start event"
grep -Fq 'ℹ Now working on fixture output.' "$tmp_dir/rendered.log" \
  || fail "live renderer omitted assistant text deltas"
grep -Fq '→ [01] Read file — tests/fixture.ts (offset 4, limit 8)' "$tmp_dir/rendered.log" \
  || fail "live renderer omitted the identified read summary"
grep -Fq '→ [02] Run tests — npm test -- fixture' "$tmp_dir/rendered.log" \
  || fail "live renderer omitted the semantic shell summary"
grep -Fq '✕ [02] Run tests (' "$tmp_dir/rendered.log" \
  || fail "live renderer did not correlate the failed parallel tool completion"
grep -Fq '✓ [01] Read file (' "$tmp_dir/rendered.log" \
  || fail "live renderer did not correlate the successful parallel tool completion"
grep -Fq '[02] fixture command failed' "$tmp_dir/rendered.log" \
  || fail "live renderer omitted identified failed tool output"
if grep -Fq 'Thinking…' "$tmp_dir/rendered.log" || grep -Fq 'private reasoning fixture' "$tmp_dir/rendered.log"; then
  fail "live renderer exposed noisy thinking events or deltas"
fi
if grep -Fq 'large successful result that should stay hidden' "$tmp_dir/rendered.log"; then
  fail "live renderer exposed successful tool results"
fi

{
  printf '%s\n' '{"type":"agent_start"}'
  sleep 0.35
  printf '%s\n' '{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop"}]}'
} | AGENT_EVENT_HEARTBEAT_SECONDS=0.1 \
  node "$SCRIPT_DIR/render-agent-events.mjs" > "$tmp_dir/heartbeat.log"
grep -Fq 'Waiting for model…' "$tmp_dir/heartbeat.log" \
  || fail "live renderer omitted its idle heartbeat"

pp_step "Regression: run-agent selects JSON mode and propagates event failures"
cat > "$tmp_dir/fake-pi" <<'FAKE_PI'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_PI_ARGS_LOG:?FAKE_PI_ARGS_LOG must be set}"
: "${FAKE_PI_SCENARIO:?FAKE_PI_SCENARIO must be set}"
if [[ -n "${PI_AGENT_EVENT_LOG:-}" ]]; then
  printf '%s\n' 'run-agent leaked its private event sink into the agent environment' >&2
  exit 98
fi
printf '%s\n' "$*" > "$FAKE_PI_ARGS_LOG"

case "$FAKE_PI_SCENARIO" in
  success)
    printf '%s\n' \
      '{"type":"agent_start"}' \
      '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Live wrapper output."}}' \
      '{"type":"tool_execution_start","toolCallId":"read-full","toolName":"read","args":{"path":"fixture.ts"}}' \
      '{"type":"tool_execution_end","toolCallId":"read-full","toolName":"read","result":{"content":[{"type":"text","text":"FULL_SUCCESSFUL_TOOL_OUTPUT"}]},"isError":false}' \
      '{"type":"message_end","message":{"role":"assistant","stopReason":"stop"}}' \
      '{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop"}]}'
    ;;
  event-error)
    printf '%s\n' \
      '{"type":"agent_start"}' \
      '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"context_length_exceeded"}}' \
      '{"type":"agent_end","messages":[{"role":"assistant","stopReason":"error","errorMessage":"context_length_exceeded"}]}'
    ;;
  invalid-json)
    printf '%s\n' 'not-json'
    ;;
  final)
    printf '%s\n' 'Final-only wrapper output.'
    ;;
  *)
    exit 99
    ;;
esac
FAKE_PI
chmod +x "$tmp_dir/fake-pi"

PI_AGENT_COMMAND="$tmp_dir/fake-pi" \
PI_AGENT_OUTPUT_MODE=live \
PI_AGENT_EVENT_LOG="$tmp_dir/live.pi-events.jsonl" \
FAKE_PI_ARGS_LOG="$tmp_dir/args.log" \
FAKE_PI_SCENARIO=success \
AGENT_EVENT_HEARTBEAT_SECONDS=0 \
  "$SCRIPT_DIR/run-agent.sh" 'fixture prompt' > "$tmp_dir/live.log" 2>&1

grep -Fq -- '--no-session --mode json' "$tmp_dir/args.log" \
  || fail "run-agent did not select Pi JSON mode for live output"
grep -Fq 'Live wrapper output.' "$tmp_dir/live.log" \
  || fail "run-agent did not render live Pi output"
grep -Fq 'FULL_SUCCESSFUL_TOOL_OUTPUT' "$tmp_dir/live.pi-events.jsonl" \
  || fail "run-agent full event log omitted a successful tool result"
if grep -Fq 'FULL_SUCCESSFUL_TOOL_OUTPUT' "$tmp_dir/live.log"; then
  fail "run-agent concise renderer unexpectedly exposed successful tool output"
fi
if [[ "$(stat -c '%a' "$tmp_dir/live.pi-events.jsonl")" != "600" ]]; then
  fail "run-agent full Pi event log was not private"
fi

set +e
PI_AGENT_COMMAND="$tmp_dir/fake-pi" \
PI_AGENT_OUTPUT_MODE=live \
FAKE_PI_ARGS_LOG="$tmp_dir/error-args.log" \
FAKE_PI_SCENARIO=event-error \
AGENT_EVENT_HEARTBEAT_SECONDS=0 \
  "$SCRIPT_DIR/run-agent.sh" 'fixture prompt' > "$tmp_dir/error.log" 2>&1
error_status=$?
set -e

if [[ "$error_status" -ne 1 ]]; then
  fail "run-agent did not propagate an assistant error event (status $error_status)"
fi
grep -Fq 'context_length_exceeded' "$tmp_dir/error.log" \
  || fail "run-agent omitted the assistant event error"

set +e
PI_AGENT_COMMAND="$tmp_dir/fake-pi" \
PI_AGENT_OUTPUT_MODE=live \
FAKE_PI_ARGS_LOG="$tmp_dir/invalid-args.log" \
FAKE_PI_SCENARIO=invalid-json \
AGENT_EVENT_HEARTBEAT_SECONDS=0 \
  "$SCRIPT_DIR/run-agent.sh" 'fixture prompt' > "$tmp_dir/invalid.log" 2>&1
invalid_status=$?
set -e

if [[ "$invalid_status" -ne 125 ]]; then
  fail "run-agent did not distinguish a renderer failure (status $invalid_status)"
fi

set +e
PI_AGENT_COMMAND="$tmp_dir/fake-pi" \
PI_AGENT_OUTPUT_MODE=json \
FAKE_PI_ARGS_LOG="$tmp_dir/json-args.log" \
FAKE_PI_SCENARIO=event-error \
AGENT_EVENT_HEARTBEAT_SECONDS=0 \
  "$SCRIPT_DIR/run-agent.sh" 'fixture prompt' > "$tmp_dir/json.log" 2>&1
json_status=$?
set -e

if [[ "$json_status" -ne 1 ]]; then
  fail "run-agent raw JSON mode did not propagate an assistant error event (status $json_status)"
fi
grep -Fq '"type":"agent_start"' "$tmp_dir/json.log" \
  || fail "run-agent raw JSON mode omitted Pi events"

PI_AGENT_COMMAND="$tmp_dir/fake-pi" \
PI_AGENT_OUTPUT_MODE=final \
FAKE_PI_ARGS_LOG="$tmp_dir/final-args.log" \
FAKE_PI_SCENARIO=final \
  "$SCRIPT_DIR/run-agent.sh" 'fixture prompt' > "$tmp_dir/final.log" 2>&1

grep -Fq -- '--no-session -p' "$tmp_dir/final-args.log" \
  || fail "run-agent final mode did not preserve Pi print mode"
grep -Fq 'Final-only wrapper output.' "$tmp_dir/final.log" \
  || fail "run-agent final mode omitted command output"

pp_success "Build-loop agent-output regressions passed."
