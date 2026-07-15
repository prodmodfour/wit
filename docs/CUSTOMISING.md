# Changing Wit scope safely

Wit's product scope, architecture, and safety constraints are already defined. The source of truth for implementation work is:

```text
PROJECT_BRIEF.md
BUILD_TICKETS.md
AGENTS.md
```

Do not replace the queue with broad feature requests or use a completed ticket description to conceal a later scope change. Every successful ticket must remain small enough for one focused conventional commit, include its required tests and documentation, pass `scripts/quality-gate.sh`, and leave a clean working tree.

To propose a scope change before implementation:

1. explain and review the change against `PROJECT_BRIEF.md`, including non-goals and safety boundaries;
2. update the brief only when the product contract genuinely changes;
3. add new ordered, independently testable tickets to `BUILD_TICKETS.md` without rewriting completed history;
4. make the planning change as its own reviewed commit; and
5. run `scripts/quality-gate.sh`.

Do not silently broaden Wit into movie automation, public hosting, source/indexer provisioning, direct qBittorrent control, DRM workarounds, destructive cleanup, or arbitrary command execution. A proposed change that crosses those boundaries needs explicit project-level review, not an implementation shortcut.

The autonomous loop scripts are intentionally retained for maintainers. See [autonomous build operations](USAGE.md), [ticket writing](TICKET_WRITING.md), and [build safety](SAFETY.md).
