# Usage

## 1. Create a new project from this template

Use GitHub's template feature, or copy this repository manually.

## 2. Customise the project brief

Edit `PROJECT_BRIEF.md`.

Set:

```text
TEMPLATE_CUSTOMISED: true
```

Fill in:

* project name
* project type
* project goal
* audience
* success criteria
* non-goals
* technology preferences
* architecture expectations
* quality expectations
* documentation expectations
* safety constraints

## 3. Replace the tickets

Edit `BUILD_TICKETS.md`.

Keep the top-level line:

```text
AUTOMATION_STATUS: NOT_DONE
```

Then replace the example tickets with project-specific tickets. Keep tickets limited to descriptions plus a `Status: TODO` or `Status: DONE` line; do not use the ticket file for cycle notes or blocker commentary.

Supported `##` ticket headings include numeric IDs (`## 003 — ...`), plain labels (`## Ticket 3 — ...`), and prefixed IDs containing a number (`## LP-S5-003 — ...`). The build loop preserves the displayed ID and uses file order: the first ticket heading with `Status: TODO` is next.

Good tickets are:

* small
* ordered
* testable
* clear about expected files/behaviour
* clear about docs and validation
* scoped to one change

## 4. Preview script output formatting

Run the mock output demo to see the coloured, delineated section style without invoking an agent, validation, git, or network commands:

```bash
scripts/mock-output.sh
```

## 5. Choose or create a work branch

Run on the current branch, select an existing branch, or create a new branch before the loop starts.

Select an existing local branch, or a unique remote branch:

```bash
scripts/build-loop.sh --branch feature/autonomous-build --max-cycles 20
```

Create a branch from `HEAD`:

```bash
scripts/build-loop.sh --create-branch feature/autonomous-build --max-cycles 20
```

Create a branch from a specific start point:

```bash
scripts/build-loop.sh --create-branch feature/autonomous-build --branch-start main --max-cycles 20
```

`--branch` and `--create-branch` require a clean working tree and cannot be used together.

## 6. Optionally create a GitHub or GitLab repository

Use `scripts/create-remote-repo.sh` after authenticating the relevant CLI.

GitHub:

```bash
gh auth login
scripts/create-remote-repo.sh --github --name OWNER/REPO --visibility private --branch feature/autonomous-build
```

GitLab:

```bash
glab auth login
scripts/create-remote-repo.sh --gitlab --name GROUP/REPO --visibility private --branch feature/autonomous-build
```

The helper requires a clean working tree for real runs. It creates the remote repository, adds the selected local remote name (`origin` by default), and pushes the current branch by default. Use `--no-push` to create only the remote repository.

If `origin` already points somewhere else, either choose another remote name:

```bash
scripts/create-remote-repo.sh --github --name OWNER/REPO --remote project-origin
```

or intentionally replace it:

```bash
scripts/create-remote-repo.sh --github --name OWNER/REPO --replace-remote
```

Preview without network or git changes:

```bash
scripts/create-remote-repo.sh --gitlab --name GROUP/REPO --dry-run --no-push
```

## 7. Run one cycle

```bash
scripts/build-loop.sh
```

## 8. Run multiple autonomous cycles

```bash
scripts/build-loop.sh --max-cycles 20
```

At the start of each cycle, the loop prints the current ticket it is working on.
The loop pushes each successful cycle's commit by default.

## 9. Run convenience Just recipes

```bash
just quality          # run scripts/quality-gate.sh
just autobuild        # one local no-push cycle
just autobuild 5      # five local no-push cycles
just run              # default 180-cycle loop with push enabled
just run 40           # 40-cycle loop with push enabled
just follow           # follow concise live activity
just monitor 10       # interpreted update now and every 10 minutes
just stop             # request a graceful stop
just codex-usage      # optional Codex usage-window report
```

Refresh a queue from a planning markdown file when you have one:

```bash
just refresh sprint-plan.md
```

`just refresh` rewrites `BUILD_TICKETS.md` and `PROJECT_BRIEF.md`, deletes the planning file, commits the refresh, and pushes the current branch. For more control, run `scripts/refresh_build_queue.py --help` directly.

## 10. Follow, monitor, and gracefully stop a long run

### Live agent output

The default `live` output mode asks Pi for JSON events. The launcher terminal stays focused on cycle-level state, while the external logs receive concise assistant progress, safe tool summaries, stable tool identifiers, provider retries, compaction, durations, and idle heartbeats. Thinking deltas and successful tool results are hidden from this concise view; failed tool output is bounded.

Every `live` or `json` attempt is also copied byte-for-byte to a private `*.pi-events.jsonl` sidecar beside its rendered `*.log` file. Sidecars are created with mode `0600` and can contain source excerpts, full tool arguments/results, command output, and provider-emitted reasoning. They remain outside the repository and must never be committed.

Select another output mode when needed:

```bash
# Preserve final-response-only Pi print mode; no live event sidecar is available.
scripts/build-loop.sh --max-cycles 1 --agent-output final --no-push

# Render the raw JSONL stream in the detailed log as well as its private sidecar.
scripts/build-loop.sh --max-cycles 1 --agent-output json --no-push
```

### Follow active work

Every active loop writes high-level launcher output to `current.log`, concise agent activity to `follow.log`, and per-attempt rendered/full-event files in the external state directory. Detailed activity is not mirrored to the launcher terminal.

```bash
just follow       # show the latest 40 lines, then follow
just follow 100   # show the latest 100 lines, then follow
```

The follower reports the active PID, cycle, ticket, and phase, follows cycle transitions, and exits when the loop exits. Ctrl-C detaches only the follower.

### Interpret progress

```bash
just monitor       # report immediately, then every 10 minutes
just monitor 5     # report immediately, then every 5 minutes
```

Each report uses a fresh Pi invocation with only the read tool enabled; project context, extensions, and skills are disabled. The monitor projects each new range of the active private event sidecar into a bounded semantic view. Cumulative snapshots are reduced to deltas and metadata, while authoritative tool starts, arguments/results, retries, failures, and lifecycle events remain. Malformed or exceptionally large event lines become bounded warning records rather than exhausting memory.

Set `PI_MONITOR_AGENT_COMMAND` to choose a Pi-compatible analyzer or `PI_MONITOR_THINKING_LEVEL` to override the default `low` thinking level. The normalized event view is sent to that analyzer's model provider and can still contain sensitive data; use this feature only with a trusted provider. Ctrl-C detaches the monitor without affecting the loop, and the monitor emits a final report after observing loop exit.

### Request a graceful stop

```bash
just stop
```

A graceful stop does not terminate an active Pi process. The current attempt may finish, and a successful cycle completes its normal commit and push or PR/MR publication. No next cycle starts. If the attempt fails, failure-checkpoint guardrails finish, but the loop exits before ticket-split recovery or another retry. Stop requests during retry or between-cycle sleeps end the wait without starting more work. Repeated requests are harmless.

`just follow`, `just monitor`, and `just stop` resolve the same per-repository state directory as the loop. If the loop was started with `AUTONOMOUS_BUILD_LOOP_STATE_DIR`, pass the same variable to those commands.

### Optionally inspect Codex usage windows

For a Pi command backed by OpenAI Codex and exposing Codex rate-limit headers:

```bash
just codex-usage
```

The helper makes one minimal request with a process-local SSE setting, reports active usage/reset windows, handles inactive zero-length window sentinels, and removes its diagnostic files. It does not log in/out, create a session, or persist Pi settings. Set `PI_CODEX_USAGE_COMMAND` to select a different Pi-compatible Codex command; it otherwise uses `PI_AGENT_COMMAND`, then `pi`.

## 11. Create and merge PRs/MRs as the loop progresses

Use `--pr-each-cycle` to create a GitHub pull request or GitLab merge request after a successful cycle commit. If an open PR/MR already exists for the work branch, later cycles reuse it and push more commits to it:

```bash
scripts/build-loop.sh --branch feature/autonomous-build --pr-each-cycle --pr-base main --max-cycles 20
```

Use `--merge-pr-each-cycle` to create and immediately merge each PR/MR:

```bash
scripts/build-loop.sh --branch feature/autonomous-build --merge-pr-each-cycle --pr-base main --max-cycles 20
```

PR/MR automation requires:

* pushing enabled; do not combine it with `--no-push`
* a configured remote, `origin` by default or `--pr-remote NAME`
* an authenticated GitHub CLI (`gh`) or GitLab CLI (`glab`)
* a work branch that is different from the base/target branch

The provider is auto-detected from the remote URL when possible. Otherwise pass `--pr-provider github` or `--pr-provider gitlab`. The base branch is detected from the remote default branch when possible. Otherwise pass `--pr-base main`, `--pr-base master`, or another target branch.

The merge mode asks the platform CLI for a normal merge and keeps the source branch so the next autonomous cycle can continue on it. Branch protection, required checks, merge conflicts, repository merge-strategy settings, or missing permissions can still stop the loop.

## 12. Run without pushing

```bash
scripts/build-loop.sh --max-cycles 20 --no-push
```

The legacy `--push` flag is still accepted, but pushing is already enabled by default.

## 13. Build-loop logs and lock files

Active build-loop state is kept outside the repository by default:

```text
${XDG_STATE_HOME:-$HOME/.local/state}/autonomous-build-template/build-loop/<repo-key>/
```

This directory contains `current.log`, `follow.log`, per-attempt rendered logs, private `*.pi-events.jsonl` sidecars, and the active lock directory. Keeping it outside `.agent/` prevents private/runtime cleanup from deleting the parent directory needed by `tee` between cycles. Keep this state private; full event logs must not be committed.

Override the per-repository state directory when needed:

```bash
AUTONOMOUS_BUILD_LOOP_STATE_DIR=/path/to/build-loop-state scripts/build-loop.sh --max-cycles 20
```

## 14. Automatic agent failure recovery

If an implementation run fails after creating commits or leaving uncommitted changes, the loop first preserves that failed-run state before retrying:

1. restore `BUILD_TICKETS.md` to its pre-run state so the same ticket stays `TODO`
2. run lightweight secret/generated-file guardrails
3. commit uncommitted changes as `chore: checkpoint failed autonomous cycle`
4. push the current branch unless `--no-push` is set
5. retry the same build cycle

If the failure produced no commits or file changes, there is nothing to checkpoint and the loop simply retries.

If an implementation run fails with a token or context-length error, the loop asks the configured agent wrapper to split the first `TODO` ticket in file order into two smaller tickets. The split is committed, and the same cycle is retried so `--max-cycles 1` can still complete one implementation cycle after recovery.

If an implementation run fails for another reason, the loop assumes a transient provider/server issue and retries the same cycle after 10 minutes.

Override the retry delay when needed:

```bash
AUTONOMOUS_BUILD_RETRY_SECONDS=60 scripts/build-loop.sh --max-cycles 20
```

Use `AUTONOMOUS_BUILD_RETRY_SECONDS=0` for immediate retries in tests.

## 15. If branch is already ahead

Branches that are already ahead of upstream are allowed by default. The loop still refuses to start when the branch is behind upstream, and still stops if upstream advances during a cycle.

The legacy `--allow-ahead` flag is still accepted for older scripts, but it is no longer required.

## 16. Changing the agent

The main build loop is agent-agnostic.

To change the agent command while keeping the same invocation shape, set `PI_AGENT_COMMAND`:

```bash
PI_AGENT_COMMAND=pi-dan-rinse scripts/build-loop.sh --max-cycles 20
```

For a different invocation shape, edit:

```text
scripts/run-agent.sh
```

The default wrapper uses:

```bash
"${PI_AGENT_COMMAND:-pi}" --no-session -p @AGENTS.md @PROJECT_BRIEF.md @BUILD_TICKETS.md "$PROMPT"
```

It intentionally does not pass model or thinking-level flags. The bundled `live`/`json` renderer and interpreted monitor expect Pi-compatible JSON events and CLI flags; adapt `scripts/run-agent.sh` and `scripts/build-loop-monitor.sh` together when switching to an agent with a different protocol.

## 17. Completion

The final ticket should set the top-level status in `BUILD_TICKETS.md` to:

```text
AUTOMATION_STATUS: DONE
```

The build loop checks only the first top-level status line.
