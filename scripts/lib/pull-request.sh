#!/usr/bin/env bash
# Shared pull-request / merge-request helpers for repository scripts.
# Source this file after scripts/lib/pretty-print.sh and scripts/lib/git-branch.sh.

if [[ -n "${_PULL_REQUEST_SH:-}" ]]; then
  return 0
fi
_PULL_REQUEST_SH=1

pr_have() {
  command -v "$1" >/dev/null 2>&1
}

pr_print_command() {
  local rendered=""
  printf -v rendered '%q ' "$@"
  pp_cmd "${rendered% }"
}

pr_validate_provider() {
  local provider="$1"

  case "$provider" in
    auto|github|gitlab) ;;
    *)
      pp_error "PR provider must be auto, github, or gitlab: $provider"
      return 2
      ;;
  esac
}

pr_resolve_provider() {
  local requested="$1"
  local remote_name="$2"
  local remote_url=""
  local have_gh=0
  local have_glab=0

  pr_validate_provider "$requested" || return $?

  if [[ "$requested" != "auto" ]]; then
    printf '%s\n' "$requested"
    return 0
  fi

  remote_url="$(git remote get-url "$remote_name" 2>/dev/null || true)"
  case "$remote_url" in
    *github.com[:/]*|*://github.com/*)
      printf 'github\n'
      return 0
      ;;
    *gitlab.com[:/]*|*://gitlab.com/*|*gitlab*)
      printf 'gitlab\n'
      return 0
      ;;
  esac

  if pr_have gh; then
    have_gh=1
  fi
  if pr_have glab; then
    have_glab=1
  fi

  if (( have_gh == 1 && have_glab == 0 )); then
    printf 'github\n'
    return 0
  fi

  if (( have_glab == 1 && have_gh == 0 )); then
    printf 'gitlab\n'
    return 0
  fi

  pp_error "Could not auto-detect PR provider for remote '$remote_name'."
  if [[ -n "$remote_url" ]]; then
    pp_hint "Remote URL: $remote_url"
  fi
  pp_hint "Pass --pr-provider github or --pr-provider gitlab."
  return 1
}

pr_require_provider_cli() {
  local provider="$1"

  case "$provider" in
    github)
      if ! pr_have gh; then
        pp_error "GitHub PR automation requires the GitHub CLI: gh"
        pp_hint "Install gh and run gh auth login, or disable PR automation."
        return 127
      fi
      ;;
    gitlab)
      if ! pr_have glab; then
        pp_error "GitLab MR automation requires the GitLab CLI: glab"
        pp_hint "Install glab and authenticate, or disable PR automation."
        return 127
      fi
      ;;
    *)
      pp_error "Unsupported PR provider: $provider"
      return 2
      ;;
  esac
}

pr_require_remote() {
  local remote_name="$1"

  if ! git remote get-url "$remote_name" >/dev/null 2>&1; then
    pp_error "PR remote does not exist: $remote_name"
    pp_hint "Add the remote, use scripts/create-remote-repo.sh, or pass --pr-remote NAME."
    return 1
  fi
}

pr_detect_base_branch() {
  local remote_name="$1"
  local remote_head=""

  pr_require_remote "$remote_name" || return $?

  git fetch "$remote_name" --quiet

  remote_head="$(git symbolic-ref -q --short "refs/remotes/$remote_name/HEAD" 2>/dev/null || true)"
  if [[ -n "$remote_head" ]]; then
    printf '%s\n' "${remote_head#"$remote_name"/}"
    return 0
  fi

  if git show-ref --verify --quiet "refs/remotes/$remote_name/main"; then
    printf 'main\n'
    return 0
  fi

  if git show-ref --verify --quiet "refs/remotes/$remote_name/master"; then
    printf 'master\n'
    return 0
  fi

  pp_error "Could not detect the PR base branch for remote '$remote_name'."
  pp_hint "Pass --pr-base main, --pr-base master, or another target branch."
  return 1
}

pr_current_branch() {
  local current_branch

  current_branch="$(git branch --show-current 2>/dev/null || true)"
  if [[ -z "$current_branch" ]]; then
    pp_error "PR automation requires a named branch; detached HEAD is not supported."
    return 1
  fi

  printf '%s\n' "$current_branch"
}

pr_validate_current_branch() {
  local base_branch="$1"
  local current_branch

  current_branch="$(pr_current_branch)" || return $?

  if [[ -n "$base_branch" && "$current_branch" == "$base_branch" ]]; then
    pp_error "PR automation requires a work branch different from the base branch '$base_branch'."
    pp_hint "Use --create-branch feature/autonomous-build or --branch NAME."
    return 1
  fi
}

pr_existing_review_current_branch() {
  local provider="$1"
  local base_branch="$2"
  local current_branch
  local output=""
  local compact_output=""
  local status
  local -a cmd

  current_branch="$(pr_current_branch)" || return $?

  set +e
  case "$provider" in
    github)
      cmd=(gh pr list --head "$current_branch" --state open --json number --jq '.[0].number')
      if [[ -n "$base_branch" ]]; then
        cmd+=(--base "$base_branch")
      fi
      output="$("${cmd[@]}" 2>/dev/null)"
      status=$?
      ;;
    gitlab)
      cmd=(glab mr list --source-branch "$current_branch" --output json --per-page 1)
      if [[ -n "$base_branch" ]]; then
        cmd+=(--target-branch "$base_branch")
      fi
      output="$("${cmd[@]}" 2>/dev/null)"
      status=$?
      ;;
    *)
      set -e
      pp_error "Unsupported PR provider: $provider"
      return 2
      ;;
  esac
  set -e

  if (( status != 0 )); then
    return 1
  fi

  case "$provider" in
    github)
      if [[ -z "$output" || "$output" == "null" ]]; then
        return 1
      fi
      ;;
    gitlab)
      compact_output="${output//$'\n'/}"
      if [[ -z "$compact_output" || "$compact_output" == "[]" ]]; then
        return 1
      fi
      ;;
  esac

  pp_info "Existing PR/MR found for branch $current_branch; reusing it."
  return 0
}

pr_create_current_branch() {
  local provider="$1"
  local base_branch="$2"
  local title="$3"
  local body="$4"
  local current_branch
  local existing_status
  local -a cmd

  current_branch="$(pr_current_branch)" || return $?
  pr_validate_current_branch "$base_branch" || return $?

  if pr_existing_review_current_branch "$provider" "$base_branch"; then
    return 0
  else
    existing_status=$?
    if (( existing_status != 1 )); then
      return "$existing_status"
    fi
  fi

  case "$provider" in
    github)
      cmd=(gh pr create --head "$current_branch" --title "$title" --body "$body")
      if [[ -n "$base_branch" ]]; then
        cmd+=(--base "$base_branch")
      fi
      ;;
    gitlab)
      cmd=(glab mr create --source-branch "$current_branch" --title "$title" --description "$body" --yes)
      if [[ -n "$base_branch" ]]; then
        cmd+=(--target-branch "$base_branch")
      fi
      ;;
    *)
      pp_error "Unsupported PR provider: $provider"
      return 2
      ;;
  esac

  pr_print_command "${cmd[@]}"
  "${cmd[@]}"
}

pr_merge_current_branch() {
  local provider="$1"
  local head_sha="$2"
  local current_branch
  local -a cmd

  current_branch="$(pr_current_branch)" || return $?

  case "$provider" in
    github)
      cmd=(gh pr merge "$current_branch" --merge --match-head-commit "$head_sha")
      ;;
    gitlab)
      cmd=(glab mr merge "$current_branch" --yes --sha "$head_sha" --auto-merge=false)
      ;;
    *)
      pp_error "Unsupported PR provider: $provider"
      return 2
      ;;
  esac

  pr_print_command "${cmd[@]}"
  "${cmd[@]}"
}
