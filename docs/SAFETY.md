# Autonomous build safety

Wit's retained maintainer harness is designed to reduce the risk of autonomous build mistakes. These controls supplement, rather than replace, the product safety rules in `AGENTS.md` and `PROJECT_BRIEF.md`.

## Built-in safety behaviours

The build loop:

* refuses to start with a dirty working tree
* requires the project brief's completed customisation marker by default
* locks to avoid concurrent runs and refuses to overwrite or release state after lock ownership is lost
* supports graceful stop requests without terminating an active agent or skipping successful publication
* checks upstream before and after each cycle, while allowing branches that are already ahead of upstream
* can select an existing branch with `--branch` or create one with `--create-branch`
* pushes each successful cycle's commit by default unless `--no-push` is passed
* can create or create-and-merge a PR/MR after each successful cycle when explicitly enabled
* sets upstream on first push when the current branch has no upstream but `origin` exists
* restores failed agent runs back to the pre-run clean tree before automatic retry or ticket-splitting recovery
* stops if a failed run cannot be safely restored, or if a successful agent/recovery run leaves uncommitted changes
* splits the current ticket after token/context-length failures and retries other agent failures after the configured delay
* stops if no commit is produced by a successful implementation cycle
* checks only the top-level automation status
* delegates agent invocation to a wrapper script
* stores detailed logs and full Pi event sidecars outside the repository and creates sidecars with private file permissions
* gives the optional progress analyzer only the read tool and a bounded semantic event projection

## File safety

The default checks reject obvious:

* real `.env` files
* Terraform state
* Terraform plan files
* non-example `.tfvars`
* private keys
* access-key-looking values
* suspicious secret assignments in source files

## Limitations

These checks are not a replacement for human review.

Full Pi event sidecars and monitor projections can contain source excerpts, command output, provider-emitted reasoning, or secrets accidentally exposed during a run. Keep the external build-loop state directory private, never commit it, and use interpreted monitoring only with a trusted model provider.

Before making a repo public, manually review:

* commits
* README
* docs
* configuration files
* examples
* logs
* generated files
* CI workflows

## Remote repository and PR/MR safety

`scripts/create-remote-repo.sh` can create GitHub or GitLab repositories using the authenticated `gh` or `glab` CLI. It requires a clean working tree for non-dry runs, refuses to overwrite an existing local remote unless `--replace-remote` is passed, and supports `--dry-run` for previewing commands.

Review visibility (`private`, `public`, or `internal`) before creating a repository, especially before pushing autonomous or newly generated changes.

Build-loop PR/MR automation is opt-in. `--pr-each-cycle` creates or reuses a PR/MR after each successful cycle, and `--merge-pr-each-cycle` creates and immediately merges it. Use a work branch that differs from the base branch, and make sure branch protection, required checks, and merge permissions match the level of autonomy you want.

## Cloud safety

Do not add automated cloud mutation commands unless a project specifically requires it and has a clearly documented safety model.

Risky commands include:

* `terraform apply`
* `terraform destroy`
* `terraform import`
* cloud deploy commands
* destructive CLI operations
