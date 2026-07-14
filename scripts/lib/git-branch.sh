#!/usr/bin/env bash
# Shared git branch helpers for repository scripts.
# Source this file after scripts/lib/pretty-print.sh; do not execute it directly.

if [[ -n "${_GIT_BRANCH_SH:-}" ]]; then
  return 0
fi
_GIT_BRANCH_SH=1

git_branch_current() {
  local branch
  local short_head

  branch="$(git branch --show-current 2>/dev/null || true)"
  if [[ -n "$branch" ]]; then
    printf '%s\n' "$branch"
    return 0
  fi

  short_head="$(git rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -n "$short_head" ]]; then
    printf 'DETACHED@%s\n' "$short_head"
  else
    printf 'unknown\n'
  fi
}

git_branch_validate_name() {
  local branch="$1"
  local label="${2:-branch}"

  if [[ -z "$branch" ]]; then
    pp_error "$label must not be empty."
    return 2
  fi

  if [[ "$branch" == -* ]]; then
    pp_error "$label must not start with '-': $branch"
    return 2
  fi

  if [[ "$branch" =~ [[:space:]] ]]; then
    pp_error "$label must not contain whitespace: $branch"
    return 2
  fi

  if ! git check-ref-format --branch "$branch" >/dev/null 2>&1; then
    pp_error "Invalid $label: $branch"
    pp_hint "Use a valid git branch name, for example: feature/autonomous-build."
    return 2
  fi
}

git_branch_require_clean_tree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    pp_error "Working tree is dirty; refusing to continue."
    git status --short >&2
    return 1
  fi
}

git_branch_fetch_remotes() {
  if git remote | grep -q .; then
    pp_cmd "git fetch --all --prune --quiet"
    git fetch --all --prune --quiet
  fi
}

git_branch_remote_matches() {
  local branch="$1"

  git for-each-ref --format='%(refname:short)' "refs/remotes/*/$branch" \
    | grep -Ev '(^|/)HEAD$' \
    || true
}

git_branch_select() {
  local branch="$1"
  local current_branch
  local matches

  git_branch_validate_name "$branch" "branch" || return $?
  git_branch_require_clean_tree || return $?

  current_branch="$(git branch --show-current 2>/dev/null || true)"
  if [[ "$current_branch" == "$branch" ]]; then
    pp_success "Already on branch $branch."
    return 0
  fi

  if git show-ref --verify --quiet "refs/heads/$branch"; then
    pp_cmd "git switch $branch"
    git switch "$branch"
    pp_success "Selected existing branch $branch."
    return 0
  fi

  git_branch_fetch_remotes || return $?

  mapfile -t matches < <(git_branch_remote_matches "$branch")
  if (( ${#matches[@]} == 1 )); then
    pp_cmd "git switch --track ${matches[0]}"
    git switch --track "${matches[0]}"
    pp_success "Selected remote-tracking branch ${matches[0]}."
    return 0
  fi

  if (( ${#matches[@]} > 1 )); then
    pp_error "Branch $branch exists on multiple remotes; select or create it manually."
    printf 'Matching remote branches:\n' >&2
    printf '  %s\n' "${matches[@]}" >&2
    return 1
  fi

  pp_error "Branch not found: $branch"
  pp_hint "Create it with --create-branch $branch, or create/select it manually."
  return 1
}

git_branch_create() {
  local branch="$1"
  local start_point="${2:-HEAD}"

  git_branch_validate_name "$branch" "branch" || return $?
  git_branch_require_clean_tree || return $?

  if git show-ref --verify --quiet "refs/heads/$branch"; then
    pp_error "Branch already exists: $branch"
    pp_hint "Use --branch $branch to select it."
    return 1
  fi

  if ! git rev-parse --verify --quiet "${start_point}^{commit}" >/dev/null; then
    pp_error "Branch start point not found or not a commit: $start_point"
    return 1
  fi

  pp_cmd "git switch -c $branch $start_point"
  git switch -c "$branch" "$start_point"
  pp_success "Created and selected branch $branch."
}

git_branch_prepare() {
  local select_branch="$1"
  local create_branch="$2"
  local start_point="${3:-HEAD}"

  if [[ -n "$select_branch" && -n "$create_branch" ]]; then
    pp_error "--branch and --create-branch cannot be used together."
    return 2
  fi

  if [[ -n "$select_branch" ]]; then
    git_branch_select "$select_branch"
  elif [[ -n "$create_branch" ]]; then
    git_branch_create "$create_branch" "$start_point"
  fi
}

git_branch_push_current() {
  local remote_name="${1:-origin}"
  local current_branch
  local upstream_ref

  current_branch="$(git branch --show-current 2>/dev/null || true)"
  if [[ -z "$current_branch" ]]; then
    pp_cmd "git push"
    git push
    return $?
  fi

  upstream_ref="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [[ -n "$upstream_ref" ]]; then
    pp_cmd "git push"
    git push
    return $?
  fi

  if git remote get-url "$remote_name" >/dev/null 2>&1; then
    pp_cmd "git push -u $remote_name $current_branch"
    git push -u "$remote_name" "$current_branch"
    return $?
  fi

  pp_error "Branch $current_branch has no upstream and remote '$remote_name' does not exist."
  pp_hint "Create or add a remote, choose a different push remote, or rerun with --no-push."
  return 1
}

git_branch_push_current_to_remote() {
  local remote_name="${1:-origin}"
  local current_branch

  current_branch="$(git branch --show-current 2>/dev/null || true)"
  if [[ -z "$current_branch" ]]; then
    pp_error "Cannot push a detached HEAD to a named PR branch."
    return 1
  fi

  if ! git remote get-url "$remote_name" >/dev/null 2>&1; then
    pp_error "Remote '$remote_name' does not exist."
    pp_hint "Create or add the remote, choose a different remote, or rerun with --no-push."
    return 1
  fi

  pp_cmd "git push -u $remote_name $current_branch"
  git push -u "$remote_name" "$current_branch"
}
