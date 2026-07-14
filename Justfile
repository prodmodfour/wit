# Show available just commands and common usage.
default:
    @printf '%s\n' \
      'Available just commands:' \
      '' \
      '  just' \
      '  just default' \
      '      Show this help.' \
      '' \
      '  just quality' \
      '      Run the autonomous build quality gate.' \
      '' \
      '  just autobuild' \
      '      Run one no-push autonomous ticket cycle from a clean tree.' \
      '' \
      '  just autobuild <cycles>' \
      '      Run multiple no-push autonomous ticket cycles.' \
      '' \
      '  just refresh <ticket-file-name>' \
      '      Refresh BUILD_TICKETS.md and PROJECT_BRIEF.md from a ticket planning file,' \
      '      delete the planning file, commit the refresh, and push the current branch.' \
      '' \
      '  just run' \
      '      Run the default 180-cycle loop with high-level status; use just follow for agent details.' \
      '' \
      '  just run <cycles>' \
      '      Run N autonomous build cycles with push enabled.' \
      '' \
      '  just follow' \
      '  just follow <lines>' \
      '      Follow the active build loop, showing 40 recent lines by default.' \
      '' \
      '  just monitor' \
      '  just monitor <minutes>' \
      '      Interpret a normalized Pi event view immediately, then repeat every 10 minutes by default.' \
      '' \
      '  just codex-usage' \
      '      Send one minimal request and report Codex usage windows.' \
      '' \
      '  just stop' \
      '      Gracefully stop the active build loop after its current attempt/cycle.'

help:
    @just default

# Run the autonomous build quality gate.
quality:
    bash scripts/quality-gate.sh

# Run the autonomous ticket loop locally without pushing.
autobuild cycles="1":
    bash scripts/build-loop.sh --max-cycles {{cycles}} --no-push

# Compatibility recipe from the autonomous build template.
run cycles="180":
    bash scripts/build-loop.sh --max-cycles {{cycles}}

# Follow the active autonomous build loop without interrupting it.
follow lines="40":
    bash scripts/build-loop-follow.sh --lines {{quote(lines)}}

# Periodically summarize and interpret active autonomous build progress.
monitor minutes="10":
    bash scripts/build-loop-monitor.sh --interval-minutes {{quote(minutes)}}

# Report Codex usage windows with one minimal request.
codex-usage:
    bash scripts/codex-usage.sh

# Request a graceful stop after the active attempt/cycle reaches a safe boundary.
stop:
    bash scripts/build-loop-stop.sh

# Refresh the autonomous queue and project brief from a ticket planning file.
refresh ticket_file:
    python3 scripts/refresh_build_queue.py {{quote(ticket_file)}}
