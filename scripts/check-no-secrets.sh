#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pretty-print.sh
source "$SCRIPT_DIR/lib/pretty-print.sh"

pp_step "Scanning tracked and untracked files for obvious committed secrets."

fail=0
match_file="$(mktemp)"
trap 'rm -f "$match_file"' EXIT

aws_key_regex='AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}'
private_key_regex='-----BEGIN (RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----'
secret_assignment_regex='(password|passwd|secret|api[_-]?key|private[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_./+=-]{16,}'

# Search tracked and untracked non-ignored files, excluding .git and agent logs.
while IFS= read -r file; do
  [[ -f "$file" ]] || continue

  case "$file" in
    .git/*|.agent/*|.pi/*|node_modules/*|.venv/*|.terraform/*)
      continue
      ;;
  esac

  # Avoid false positives and noisy grep output from PDFs, images, sqlite files,
  # and other binary/static assets tracked by this project.
  if ! LC_ALL=C grep -Iq . "$file" 2>/dev/null; then
    continue
  fi

  if grep -nE -- "$aws_key_regex" "$file" >"$match_file" 2>/dev/null; then
    pp_error "Possible AWS access key found."
    pp_kv "File" "$file" >&2
    cat "$match_file" >&2
    fail=1
  fi

  if grep -nE -- "$private_key_regex" "$file" >"$match_file" 2>/dev/null; then
    pp_error "Private key material found."
    pp_kv "File" "$file" >&2
    cat "$match_file" >&2
    fail=1
  fi

  if grep -nEi -- "$secret_assignment_regex" "$file" >"$match_file" 2>/dev/null; then
    case "$file" in
      PROJECT_BRIEF.md|AGENTS.md|BUILD_TICKETS.md|README.md|docs/*|scripts/check-no-secrets.sh)
        # These files contain instructional examples. Do not fail on generic documentation.
        ;;
      *)
        pp_error "Possible secret assignment found."
        pp_kv "File" "$file" >&2
        cat "$match_file" >&2
        fail=1
        ;;
    esac
  fi
done < <(git ls-files --cached --others --exclude-standard)

if (( fail != 0 )); then
  pp_error "Secret scan failed."
  exit 1
fi

pp_success "No obvious secrets found."
