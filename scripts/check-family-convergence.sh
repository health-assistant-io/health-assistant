#!/usr/bin/env bash
# Family-convergence gate (plan 20 D9 / guidelines/reference-architecture.md).
#
# Enforces, per repo, the decided family architecture:
#   C1  tool config parity — `[tool.ruff*]` blocks byte-identical across the
#       family (mypy ratchets are the documented per-repo exception);
#       pytest carries the `contract` marker + testpaths
#   C2  prefixed-only env — no unprefixed/legacy env names in code or scripts
#   C3  migrations home — `command.upgrade` only in `app/local.py`
#   C4  no legacy shims/markers (`ca-*` schemes, `LEGACY_*` maps,
#       CourseAssistant names, dual-emit vocabulary)
#   C5  packaging shape — root `pyproject.toml` + `uv.lock` + backend
#       `pyproject.toml`; no `requirements*.txt`
#   C6  contract gates — `pytest -m contract` wired in each repo's CI
#
# Vendored copies in product repos must stay byte-identical to this file
# (sha-checked by scripts/verify-wiring.sh).
#
# Usage:
#   ./check-family-convergence.sh           # family scan from the dev repo
#   ./check-family-convergence.sh --self    # self-check one repo from its root
set -uo pipefail

DEV_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="$(dirname "$DEV_ROOT")"

# repo (relative to workspace) : pyproject path (repo-relative)
REPOS=(
  "study-assistant:backend/pyproject.toml"
  "career-assistant:backend/pyproject.toml"
  "health-assistant/core:backend/pyproject.toml"
  "auth-kit:pyproject.toml"
)

SELF=0
for arg in "$@"; do
  case "$arg" in
    --self) SELF=1 ;;
    *) echo "unknown arg: $arg (use --self)" >&2; exit 2 ;;
  esac
done

CANON_RUFF=''   # first repo's normalized ruff block becomes canonical
REPO_FAIL=0

ruff_block() {  # pyproject -> normalized [tool.ruff*] text on stdout
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
blocks = []
for name in ("tool.ruff", "tool.ruff.lint", "tool.ruff.lint.flake8-bugbear"):
    m = re.search(rf"^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    blocks.append(f"[{name}]\n" + (m.group(1).strip() if m else ""))
print("\n".join(blocks))
PY
}

check_repo() {  # repo_dir pyproject_rel -> sets REPO_FAIL=1 on findings
  local dir="$1" pyproject_rel="$2"
  REPO_FAIL=0

  if [ ! -f "$dir/$pyproject_rel" ]; then
    echo "FAIL C5: missing $pyproject_rel"
    REPO_FAIL=1
    return
  fi

  # C1 tool-config parity
  local block
  block="$(ruff_block "$dir/$pyproject_rel")"
  if [ "$CANON_RUFF" = "" ]; then
    CANON_RUFF="$block"
  elif [ "$block" != "$CANON_RUFF" ]; then
    echo "FAIL C1: [tool.ruff*] differs from the family block:"
    diff <(printf '%s\n' "$CANON_RUFF") <(printf '%s\n' "$block") | sed 's/^/    /' | head -12
    REPO_FAIL=1
  fi
  if ! grep -q '"contract' "$dir/$pyproject_rel"; then
    echo "FAIL C1: pytest config lacks the contract marker"
    REPO_FAIL=1
  fi

  # C2 prefixed-only env (code + scripts + compose; docs history excluded)
  local legacy
  legacy="$(grep -rInE "(os\.environ(\.get)?\(|getenv\(|setenv\(|environ\[|\bexport )[\"']?(DATABASE_URL|APP_ENV|DATA_DIR|UPLOAD_DIR|SPA_DIST|API_HOST|API_PORT|CORS_ORIGINS|IDENTITY_MODE|AUTH_MODE|DEMO_MODE|REGISTRATION_ENABLED|COOKIE_SECURE|TRUSTED_PROXY_COUNT|RATE_LIMIT_ENABLED|AUTH_RATE_LIMIT|AUTH_EMAIL_RATE_LIMIT|SESSION_KEY|REFRESH_KEY|DATA_KEY)[\"')=]" \
    "$dir/backend" "$dir/scripts" "$dir/docker" 2>/dev/null \
    | grep -v __pycache__ | grep -v "/venv/" | grep -v node_modules | grep -v "CAREER_\|SA_\|HA_" \
    | grep -v "alembic/versions" | head -10)"
  if [ -n "$legacy" ]; then
    echo "FAIL C2: unprefixed/legacy env names:"
    echo "$legacy" | sed 's/^/    /'
    REPO_FAIL=1
  fi

  # C3 migrations home (app code never migrates)
  local upgrades
  upgrades="$(grep -rIln "command.upgrade" "$dir/backend/app" 2>/dev/null | grep -v __pycache__ | grep -v "app/local.py" | head -5)"
  if [ -n "$upgrades" ]; then
    echo "FAIL C3: command.upgrade outside app/local.py:"
    echo "$upgrades" | sed 's/^/    /'
    REPO_FAIL=1
  fi

  # C4 legacy shims/markers in code
  local legacy_code
  legacy_code="$(grep -rInE "ca-drawing|ca-image|ca-material|ca-course|caq/v|ca-backup/v|ca-skills/v|x-ca-|LEGACY_[A-Z_]+_MAP|card_kind_from_legacy|legacy_kind_from_card" \
    "$dir/backend/app" "$dir/backend/tests" "$dir/frontend/src" "$dir/scripts" 2>/dev/null \
    | grep -v __pycache__ | grep -v node_modules | grep -v "\.venv" | head -8)"
  if [ -n "$legacy_code" ]; then
    echo "FAIL C4: legacy markers in code:"
    echo "$legacy_code" | sed 's/^/    /'
    REPO_FAIL=1
  fi

  # C5 packaging shape (root workspace + lockfile; no requirements files)
  [ -f "$dir/pyproject.toml" ] || { echo "FAIL C5: missing root pyproject.toml"; REPO_FAIL=1; }
  [ -f "$dir/uv.lock" ] || { echo "FAIL C5: missing uv.lock"; REPO_FAIL=1; }
  if compgen -G "$dir/backend/requirements*.txt" > /dev/null; then
    echo "FAIL C5: requirements*.txt still present:"
    compgen -G "$dir/backend/requirements*.txt" | sed 's/^/    /'
    REPO_FAIL=1
  fi

  # C6 contract gate wired in CI
  if ! grep -rqn -- "-m contract" "$dir/.github" "$dir/.gitea" 2>/dev/null; then
    echo "FAIL C6: no 'pytest -m contract' step in CI workflows"
    REPO_FAIL=1
  fi
}

if [ "$SELF" = 1 ]; then
  # Self mode: check the repo we are standing in.
  here="$(basename "$(pwd)")"
  rel="backend/pyproject.toml"
  [ "$here" = "auth-kit" ] && rel="pyproject.toml"
  echo "== family convergence: $(pwd) (self)"
  check_repo "." "$rel"
  if [ "$REPO_FAIL" = 1 ]; then
    echo "RESULT: FAIL"
    exit 1
  fi
  echo "RESULT: OK"
  exit 0
fi

fail_total=0
for entry in "${REPOS[@]}"; do
  repo="${entry%%:*}"
  rel="${entry#*:}"
  echo "== family convergence: $repo"
  check_repo "$WORKSPACE/$repo" "$rel"
  [ "$REPO_FAIL" = 1 ] && fail_total=1
done

if [ "$fail_total" = 1 ]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: OK — all repos aligned"
