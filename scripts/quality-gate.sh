#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

warn() {
  pp_warn "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run_cmd() {
  pp_cmd "$*"
  "$@"
}

pp_banner "Quality gate"

pp_section "Shell syntax checks"
while IFS= read -r -d '' script; do
  pp_step "bash -n $script"
  bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print0 | sort -z)
pp_success "Shell syntax checks passed."

mapfile -d '' python_scripts < <(
  find scripts -maxdepth 1 -type f -name '*.py' -print0 | sort -z
)

if (( ${#python_scripts[@]} > 0 )); then
  pp_section "Python script syntax checks"
  if have python3; then
    run_cmd python3 -m py_compile "${python_scripts[@]}"
  elif have python; then
    run_cmd python -m py_compile "${python_scripts[@]}"
  else
    warn "Python not installed; skipping Python script syntax checks"
  fi
fi

mapfile -d '' script_regression_tests < <(
  find scripts -maxdepth 1 -type f -name 'test-build-loop-*.sh' -print0 | sort -z
)

if (( ${#script_regression_tests[@]} > 0 )); then
  pp_section "Script regression tests"
  for test_script in "${script_regression_tests[@]}"; do
    run_cmd bash "$test_script"
  done
fi

if [[ -f scripts/check-no-secrets.sh ]]; then
  pp_section "Secret guardrail"
  run_cmd bash scripts/check-no-secrets.sh
fi

if [[ -f scripts/check-no-generated-private-files.sh ]]; then
  pp_section "Generated/private-file guardrail"
  run_cmd bash scripts/check-no-generated-private-files.sh
fi

if [[ -f Makefile ]] && grep -Eq '^[[:space:]]*quality:' Makefile; then
  pp_section "Make quality"
  run_cmd make quality
fi

if [[ -f package.json ]]; then
  pp_section "Node project"

  if have npm; then
    if [[ -f package-lock.json ]]; then
      run_cmd npm ci
    else
      run_cmd npm install
    fi

    run_cmd npm run lint --if-present
    run_cmd npm run typecheck --if-present
    run_cmd npm test --if-present
    run_cmd npm run build --if-present
  else
    warn "npm not installed; skipping Node checks"
  fi
fi

if [[ -f pyproject.toml ]]; then
  pp_section "Python project"

  if have uv; then
    if [[ -f uv.lock ]]; then
      run_cmd uv sync --locked --all-groups
    else
      run_cmd uv sync --all-groups
    fi

    if grep -Eq 'ruff' pyproject.toml; then
      run_cmd uv run ruff check .
      run_cmd uv run ruff format --check .
    else
      pp_info "ruff not configured; skipping ruff checks."
    fi

    if grep -Eq 'mypy' pyproject.toml; then
      run_cmd uv run mypy .
    else
      pp_info "mypy not configured; skipping type checks."
    fi

    if [[ -d tests ]] && grep -Eq 'pytest' pyproject.toml; then
      run_cmd uv run pytest
    else
      pp_info "pytest tests not detected; skipping pytest."
    fi
  elif have python; then
    warn "uv not installed; running minimal Python syntax checks only"
    run_cmd python -m compileall -q .
  else
    warn "Python tooling not installed; skipping Python checks"
  fi
fi

if find . -path ./.git -prune -o -name '*.tf' -print -quit | grep -q .; then
  pp_section "Terraform project"

  if have terraform; then
    run_cmd terraform fmt -recursive -check

    while IFS= read -r dir; do
      pp_step "terraform validate: $dir"
      (
        cd "$dir"
        run_cmd terraform init -backend=false
        run_cmd terraform validate
      )
    done < <(
      find . -path ./.git -prune -o -name '*.tf' -print \
        | while IFS= read -r tf_file; do dirname "$tf_file"; done \
        | sort -u \
        | while IFS= read -r dir; do
            if [[ -f "$dir/main.tf" ]] || [[ -f "$dir/providers.tf" ]]; then
              printf '%s\n' "$dir"
            fi
          done
    )
  else
    warn "terraform not installed; skipping Terraform validation"
  fi
fi

if [[ -f docker-compose.yml ]] || [[ -f compose.yml ]]; then
  pp_section "Docker Compose validation"
  if have docker; then
    if [[ -f docker-compose.yml ]]; then
      pp_cmd "docker compose -f docker-compose.yml config >/dev/null"
      docker compose -f docker-compose.yml config >/dev/null
    fi
    if [[ -f compose.yml ]]; then
      pp_cmd "docker compose -f compose.yml config >/dev/null"
      docker compose -f compose.yml config >/dev/null
    fi
    if [[ -f scripts/check-compose-config.sh ]]; then
      run_cmd bash scripts/check-compose-config.sh
    fi
  else
    warn "docker not installed; skipping Docker Compose validation"
  fi
fi

pp_section "Summary"
pp_success "Quality gate passed."
