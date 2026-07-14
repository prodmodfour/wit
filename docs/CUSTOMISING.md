# Wit Autonomous Build Configuration

Wit was initialised from the Autonomous Build Template and is already customised.

The source of truth for the build is:

```text
PROJECT_BRIEF.md
BUILD_TICKETS.md
AGENTS.md
```

Do not replace the queue with broad feature requests. Every successful ticket must remain small enough for one focused conventional commit and must leave the quality gate passing.

To change scope before implementation:

1. update `PROJECT_BRIEF.md`
2. split the change into ordered, testable tickets in `BUILD_TICKETS.md`
3. make the planning change as its own reviewed commit
4. run `scripts/quality-gate.sh`

Do not edit completed ticket descriptions to conceal scope changes. Add a new ticket instead.

The generic loop scripts are intentionally retained. See [`USAGE.md`](USAGE.md) for loop controls and [`TICKET_WRITING.md`](TICKET_WRITING.md) for ticket structure.
