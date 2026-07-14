#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"
# shellcheck source=scripts/lib/git-branch.sh
source "$SCRIPT_DIR/lib/git-branch.sh"

usage() {
  cat <<'USAGE'
Usage: scripts/create-remote-repo.sh --provider github|gitlab --name NAME [options]

Creates a GitHub or GitLab repository for the current git repository, optionally
prepares a branch, adds a local remote, and pushes the current branch.

Required:
--provider github|gitlab  Repository host to create on.
--name NAME               Repository name/path. Examples: my-app, owner/my-app, group/my-app.

Provider shortcuts:
--github                  Same as --provider github.
--gitlab                  Same as --provider gitlab.

Options:
--visibility VALUE        private, public, or internal. Default: private.
--description TEXT        Repository description.
--remote NAME             Local remote name to add. Default: origin.
--replace-remote          Replace an existing local remote with the selected remote name.
--branch NAME             Select an existing local branch, or a unique remote branch, first.
--create-branch NAME      Create and select a new branch first.
--branch-start REF        Start point for --create-branch. Default: HEAD.
--no-push                 Create the remote repository but do not push local commits.
--dry-run                 Print commands without creating repositories or changing branches.
--gitlab-group GROUP      GitLab namespace/group when --name is just a repository name.
--gitlab-host HOST        GitLab host for glab, for example gitlab.example.com.
-h, --help                Show this help.

Requirements:
* GitHub creation uses the authenticated GitHub CLI: gh auth login
* GitLab creation uses the authenticated GitLab CLI: glab auth login
USAGE
}

PROVIDER=""
REPO_NAME=""
VISIBILITY="private"
DESCRIPTION=""
REMOTE_NAME="origin"
REPLACE_REMOTE=0
PUSH_AFTER=1
DRY_RUN=0
SELECT_BRANCH=""
CREATE_BRANCH=""
BRANCH_START_POINT="HEAD"
BRANCH_START_SET=0
GITLAB_GROUP=""
GITLAB_HOST_VALUE=""
REMOTE_REMOVED=0
REMOTE_OLD_URL=""
REMOTE_OLD_PUSH_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --provider)
      if [[ $# -lt 2 ]]; then
        pp_error "--provider requires a value"
        usage >&2
        exit 2
      fi
      PROVIDER="$2"
      shift 2
      ;;
    --github)
      PROVIDER="github"
      shift
      ;;
    --gitlab)
      PROVIDER="gitlab"
      shift
      ;;
    --name)
      if [[ $# -lt 2 ]]; then
        pp_error "--name requires a value"
        usage >&2
        exit 2
      fi
      REPO_NAME="$2"
      shift 2
      ;;
    --visibility)
      if [[ $# -lt 2 ]]; then
        pp_error "--visibility requires a value"
        usage >&2
        exit 2
      fi
      VISIBILITY="$2"
      shift 2
      ;;
    --description)
      if [[ $# -lt 2 ]]; then
        pp_error "--description requires a value"
        usage >&2
        exit 2
      fi
      DESCRIPTION="$2"
      shift 2
      ;;
    --remote)
      if [[ $# -lt 2 ]]; then
        pp_error "--remote requires a value"
        usage >&2
        exit 2
      fi
      REMOTE_NAME="$2"
      shift 2
      ;;
    --replace-remote)
      REPLACE_REMOTE=1
      shift
      ;;
    --branch)
      if [[ $# -lt 2 ]]; then
        pp_error "--branch requires a value"
        usage >&2
        exit 2
      fi
      SELECT_BRANCH="$2"
      shift 2
      ;;
    --create-branch)
      if [[ $# -lt 2 ]]; then
        pp_error "--create-branch requires a value"
        usage >&2
        exit 2
      fi
      CREATE_BRANCH="$2"
      shift 2
      ;;
    --branch-start)
      if [[ $# -lt 2 ]]; then
        pp_error "--branch-start requires a value"
        usage >&2
        exit 2
      fi
      BRANCH_START_POINT="$2"
      BRANCH_START_SET=1
      shift 2
      ;;
    --no-push)
      PUSH_AFTER=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --gitlab-group)
      if [[ $# -lt 2 ]]; then
        pp_error "--gitlab-group requires a value"
        usage >&2
        exit 2
      fi
      GITLAB_GROUP="$2"
      shift 2
      ;;
    --gitlab-host)
      if [[ $# -lt 2 ]]; then
        pp_error "--gitlab-host requires a value"
        usage >&2
        exit 2
      fi
      GITLAB_HOST_VALUE="$2"
      shift 2
      ;;
    *)
      pp_error "Unknown argument: $1"
      usage >&2
      exit 2
      ;;
  esac
done

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    pp_error "Required command not found: $1"
    exit 127
  fi
}

print_command() {
  local rendered=""
  printf -v rendered '%q ' "$@"
  pp_cmd "${rendered% }"
}

run_cmd() {
  print_command "$@"
  if (( DRY_RUN == 0 )); then
    "$@"
  fi
}

run_glab_cmd() {
  if [[ -n "$GITLAB_HOST_VALUE" ]]; then
    print_command env "GITLAB_HOST=$GITLAB_HOST_VALUE" "$@"
    if (( DRY_RUN == 0 )); then
      GITLAB_HOST="$GITLAB_HOST_VALUE" "$@"
    fi
  else
    run_cmd "$@"
  fi
}

validate_remote_name() {
  if [[ -z "$REMOTE_NAME" ]]; then
    pp_error "--remote must not be empty"
    exit 2
  fi

  if ! [[ "$REMOTE_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
    pp_error "Invalid remote name: $REMOTE_NAME"
    pp_hint "Use letters, numbers, dots, underscores, or dashes."
    exit 2
  fi
}

restore_removed_remote() {
  if (( REMOTE_REMOVED == 1 )) && ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    pp_warn "Restoring previous remote '$REMOTE_NAME' after failed setup."
    git remote add "$REMOTE_NAME" "$REMOTE_OLD_URL" || true
    if [[ -n "$REMOTE_OLD_PUSH_URL" && "$REMOTE_OLD_PUSH_URL" != "$REMOTE_OLD_URL" ]]; then
      git remote set-url --push "$REMOTE_NAME" "$REMOTE_OLD_PUSH_URL" || true
    fi
  fi
}

prepare_remote_slot() {
  if ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    return 0
  fi

  if (( REPLACE_REMOTE != 1 )); then
    pp_error "Remote '$REMOTE_NAME' already exists."
    pp_hint "Use --remote NAME to choose a different remote, or --replace-remote to replace it."
    exit 1
  fi

  REMOTE_OLD_URL="$(git remote get-url "$REMOTE_NAME")"
  REMOTE_OLD_PUSH_URL="$(git remote get-url --push "$REMOTE_NAME" 2>/dev/null || true)"
  REMOTE_REMOVED=1
  trap restore_removed_remote EXIT

  run_cmd git remote remove "$REMOTE_NAME"
}

validate_inputs() {
  if [[ -z "$PROVIDER" ]]; then
    pp_error "--provider is required"
    usage >&2
    exit 2
  fi

  case "$PROVIDER" in
    github|gitlab) ;;
    *)
      pp_error "--provider must be github or gitlab"
      exit 2
      ;;
  esac

  if [[ -z "$REPO_NAME" ]]; then
    pp_error "--name is required"
    usage >&2
    exit 2
  fi

  case "$VISIBILITY" in
    private|public|internal) ;;
    *)
      pp_error "--visibility must be private, public, or internal"
      exit 2
      ;;
  esac

  validate_remote_name

  if [[ -n "$SELECT_BRANCH" && -n "$CREATE_BRANCH" ]]; then
    pp_error "--branch and --create-branch cannot be used together"
    exit 2
  fi

  if (( BRANCH_START_SET == 1 )) && [[ -z "$CREATE_BRANCH" ]]; then
    pp_error "--branch-start requires --create-branch"
    exit 2
  fi

  if [[ -n "$SELECT_BRANCH" ]]; then
    git_branch_validate_name "$SELECT_BRANCH" "branch" || exit $?
  fi

  if [[ -n "$CREATE_BRANCH" ]]; then
    git_branch_validate_name "$CREATE_BRANCH" "branch" || exit $?
  fi
}

prepare_branch() {
  if [[ -z "$SELECT_BRANCH" && -z "$CREATE_BRANCH" ]]; then
    return 0
  fi

  pp_section "Branch setup"
  if (( DRY_RUN == 1 )); then
    if [[ -n "$SELECT_BRANCH" ]]; then
      pp_cmd "git switch $SELECT_BRANCH"
    else
      pp_cmd "git switch -c $CREATE_BRANCH $BRANCH_START_POINT"
    fi
    return 0
  fi

  git_branch_prepare "$SELECT_BRANCH" "$CREATE_BRANCH" "$BRANCH_START_POINT" || exit $?
}

ensure_pushable_head() {
  if (( PUSH_AFTER == 0 || DRY_RUN == 1 )); then
    return 0
  fi

  if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
    pp_error "No commits exist yet; refusing to create a remote with --push enabled."
    pp_hint "Create an initial commit first, or rerun with --no-push."
    exit 1
  fi
}

create_github_repo() {
  local cmd=(gh repo create "$REPO_NAME" "--$VISIBILITY" --source=. "--remote=$REMOTE_NAME")

  if [[ -n "$DESCRIPTION" ]]; then
    cmd+=(--description "$DESCRIPTION")
  fi

  if (( PUSH_AFTER == 1 )); then
    cmd+=(--push)
  fi

  run_cmd "${cmd[@]}"
}

create_gitlab_repo() {
  local cmd=(glab repo create "$REPO_NAME" "--$VISIBILITY" --remoteName "$REMOTE_NAME")

  if [[ -n "$DESCRIPTION" ]]; then
    cmd+=(--description "$DESCRIPTION")
  fi

  if [[ -n "$GITLAB_GROUP" ]]; then
    cmd+=(--group "$GITLAB_GROUP")
  fi

  run_glab_cmd "${cmd[@]}"

  if (( PUSH_AFTER == 1 )); then
    pp_section "Push"
    if (( DRY_RUN == 1 )); then
      pp_cmd "git push -u $REMOTE_NAME $(git_branch_current)"
    else
      git_branch_push_current "$REMOTE_NAME"
    fi
  fi
}

require_command git

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pp_error "Not inside a git work tree."
  exit 1
fi

validate_inputs

if (( DRY_RUN == 0 )); then
  case "$PROVIDER" in
    github) require_command gh ;;
    gitlab) require_command glab ;;
  esac
fi

pp_banner "Remote repository setup"
pp_kv "Provider" "$PROVIDER"
pp_kv "Repository" "$REPO_NAME"
pp_kv "Visibility" "$VISIBILITY"
pp_kv "Remote" "$REMOTE_NAME"
pp_kv "Push" "$(pp_on_off "$PUSH_AFTER")"
pp_kv "Dry run" "$(pp_on_off "$DRY_RUN")"

if (( DRY_RUN == 0 )); then
  git_branch_require_clean_tree || exit $?
fi

prepare_branch
ensure_pushable_head

pp_section "Remote setup"
if (( DRY_RUN == 0 )); then
  prepare_remote_slot
elif git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  if (( REPLACE_REMOTE == 1 )); then
    pp_cmd "git remote remove $REMOTE_NAME"
  else
    pp_warn "Remote '$REMOTE_NAME' already exists; non-dry run would fail unless --replace-remote is used."
  fi
fi

case "$PROVIDER" in
  github) create_github_repo ;;
  gitlab) create_gitlab_repo ;;
esac

REMOTE_REMOVED=0
trap - EXIT

pp_section "Summary"
pp_success "Remote repository setup complete."
pp_kv "Current branch" "$(git_branch_current)"
if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  pp_kv "Remote URL" "$(git remote get-url "$REMOTE_NAME")"
elif (( DRY_RUN == 1 )); then
  pp_info "Dry run did not create or modify a remote."
fi
