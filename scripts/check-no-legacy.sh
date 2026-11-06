#!/usr/bin/env bash
# check-no-legacy — the repo-specific no-legacy extras (plan 9 part 2, W6).
#
# D7 (legacy naming — `legacy`/`deprecated`/`old_*`/`*_v2`/`TODO(ai-refactor)`)
# is the data-model gate's job. This gate adds ONLY what D7 does not cover:
#
#   N1  zero-importer components — every file under frontend/src/components/**
#       is reached from a real entry point (main/App/sw/tests), never a dead
#       surface kept alive by its own test        (scripts/lib/check_frontend_importers.py)
#   N2  exactly one frontend lockfile — pnpm only; no package-lock/yarn/bun
#   N3  documented counts match the code — FHIR resources, router modules,
#       HTTP/WS handlers                           (backend/scripts/check_docs_counts.py)
#   N4  retired plans 3–5 shapes stay deleted — no JSONB cursor code, no
#       `chat_messages.tasks`, no hand-maintained task maps, no assignment
#       `priority` column (static floor; the DB-level pins live in
#       backend/tests/test_retired_shapes.py)
#   N5  deleted duplicate/dead surfaces stay deleted
#       (scripts/gate-patterns/deleted-surfaces.txt)
#   N6  one service-worker registration and a real 404 catch-all (no route
#       silently rendering the Dashboard for unknown paths)
#   N7  the retired `supports_*` provider-probe vocabulary never returns
#       (scripts/gate-patterns/retired-probes.txt — patterns are DATA, so this
#       script can never match its own vocabulary)
#
# Usage:
#   bash scripts/check-no-legacy.sh              # this repo
#   bash scripts/check-no-legacy.sh --root DIR   # any tree (fixtures, CI)
#   bash scripts/check-no-legacy.sh --self-test  # the gate's own test suite
set -uo pipefail

GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$GATE_DIR/.." && pwd)"

SELF_TEST=0

# Simple, explicit arg parsing (--root takes a value; --self is a no-op alias).
ARGS=("$@")
i=0
while [ "$i" -lt "${#ARGS[@]}" ]; do
  case "${ARGS[$i]}" in
    --root)
      i=$((i + 1))
      [ "$i" -lt "${#ARGS[@]}" ] || { echo "--root needs a directory" >&2; exit 2; }
      ROOT="$(cd "${ARGS[$i]}" && pwd)"
      ;;
    --self-test) SELF_TEST=1 ;;
    --self) ;;
    *)
      echo "unknown argument: ${ARGS[$i]} (use --self, --self-test, --root DIR)" >&2
      exit 2
      ;;
  esac
  i=$((i + 1))
done

if [ "$SELF_TEST" = 1 ]; then
  exec bash "$GATE_DIR/check-no-legacy.selftest.sh"
fi

FAIL=0
note() { echo "  - $1"; }
bad()  { echo "  ✗ $1" >&2; FAIL=1; }
good() { echo "  ✓ $1"; }

echo "no-legacy gate: $ROOT"

# ---------------------------------------------------------------------------
# N1 — zero-importer components
# ---------------------------------------------------------------------------
if [ -d "$ROOT/frontend/src/components" ]; then
  N1_OUT="$(python3 "$GATE_DIR/lib/check_frontend_importers.py" --root "$ROOT" 2>&1)"
  if [ $? -eq 0 ]; then
    good "N1 every frontend component has an importer"
  else
    bad "N1 zero-importer component(s) — delete them or wire them to a real entry point:"
    echo "$N1_OUT" | grep -v "^  ✓" | sed 's/^/      /' >&2
  fi
else
  bad "N1 missing frontend/src/components"
fi

# ---------------------------------------------------------------------------
# N2 — exactly one frontend lockfile (pnpm)
# ---------------------------------------------------------------------------
N2_EXTRA=()
for lockfile in package-lock.json npm-shrinkwrap.json yarn.lock bun.lockb bun.lock; do
  [ -f "$ROOT/frontend/$lockfile" ] && N2_EXTRA+=("frontend/$lockfile")
done
if [ ! -f "$ROOT/frontend/pnpm-lock.yaml" ]; then
  bad "N2 frontend/pnpm-lock.yaml is missing — pnpm is the one package manager"
elif [ "${#N2_EXTRA[@]}" -gt 0 ]; then
  bad "N2 second frontend lockfile(s) present: ${N2_EXTRA[*]} (pnpm-only: delete them)"
else
  good "N2 one frontend lockfile (pnpm)"
fi

# ---------------------------------------------------------------------------
# N3 — documented counts match the code
# ---------------------------------------------------------------------------
if [ -f "$ROOT/backend/scripts/check_docs_counts.py" ]; then
  N3_OUT="$(python3 "$ROOT/backend/scripts/check_docs_counts.py" "$ROOT" 2>&1)"
  if [ $? -eq 0 ]; then
    good "N3 $N3_OUT"
  else
    bad "N3 documented counts drift from the code:"
    echo "$N3_OUT" | sed 's/^/      /' >&2
  fi
else
  bad "N3 missing backend/scripts/check_docs_counts.py"
fi

# ---------------------------------------------------------------------------
# N4 — retired plans 3–5 shapes stay deleted
# ---------------------------------------------------------------------------
scan_repo() {
  # scan_repo PATTERN [GLOBS...] — repo-relative matches, gate-allow aware.
  local pattern="$1"
  shift
  grep -rEn "$pattern" "$@" 2>/dev/null | grep -v "gate-allow:" || true
}

N4_HITS="$(
  {
    # The retired JSONB cursor helpers (plan 3): a call or definition, not a
    # docstring mention of the history.
    grep -rEn "(def (get|set)_sync_cursor|(get|set)_sync_cursor\()" \
      --include='*.py' "$ROOT/backend/app" 2>/dev/null || true
    # The retired HITL JSONB column (plan 5): the models are where a
    # resurrected column would live (the migrated schema is pinned by
    # backend/tests/test_retired_shapes.py).
    grep -rEn "chat_messages[._]tasks" --include='*.py' \
      "$ROOT/backend/app/models" 2>/dev/null || true
    # The retired hand-maintained task maps (plan 4) — the registry and its
    # generated projections are the one source.
    grep -rEn "TASK_REQUIRED_CAPABILITY|_SLOT_TASK_TYPES|OTHER_TASKS" \
      --include='*.py' --include='*.ts' --include='*.tsx' \
      "$ROOT/backend/app" "$ROOT/frontend/src" 2>/dev/null \
      | grep -v "task_registry.py" | grep -v "/generated/" || true
    # The retired assignment priority column (plan 4).
    grep -rEn "priority" --include='*.py' \
      "$ROOT/backend/app/models/ai_task_model.py" 2>/dev/null || true
  } | grep -v "gate-allow:" || true
)"
if [ -n "$N4_HITS" ]; then
  bad "N4 a retired plans 3–5 shape reappeared (ADR: no backwards compatibility):"
  echo "$N4_HITS" | sed 's/^/      /' >&2
else
  good "N4 retired plans 3–5 shapes stay deleted"
fi

# ---------------------------------------------------------------------------
# N5 — deleted surfaces stay deleted
# ---------------------------------------------------------------------------
N5_PATTERNS="$GATE_DIR/gate-patterns/deleted-surfaces.txt"
N5_HITS=""
if [ -f "$N5_PATTERNS" ]; then
  while IFS= read -r path; do
    case "$path" in ''|\#*) continue ;; esac
    if [ -d "$ROOT/$path" ]; then
      # A deleted package may leave bytecode behind; only source counts.
      if find "$ROOT/$path" -name '*.py' -not -path '*__pycache__*' | grep -q .; then
        N5_HITS="$N5_HITS$path"$'\n'
      fi
    elif [ -e "$ROOT/$path" ]; then
      N5_HITS="$N5_HITS$path"$'\n'
    fi
  done < "$N5_PATTERNS"
fi
if [ -n "$N5_HITS" ]; then
  bad "N5 a deleted duplicate/dead surface came back (plan 9 W1/W2/W5 decision):"
  echo "$N5_HITS" | sed '/^$/d;s/^/      /' >&2
else
  good "N5 deleted surfaces stay deleted"
fi

# ---------------------------------------------------------------------------
# N6 — one service-worker registration + a real 404 catch-all
# ---------------------------------------------------------------------------
N6_SW_COUNT="$(grep -rEn "(registerSW|useRegisterSW|serviceWorker\.register)[[:space:]]*\(" "$ROOT/frontend/src" 2>/dev/null | grep -v "\.test\." | grep -v "\.d\.ts" | wc -l)"
if [ "$N6_SW_COUNT" -ne 1 ]; then
  bad "N6 expected exactly one service-worker registration, found $N6_SW_COUNT"
else
  good "N6 one service-worker registration"
fi
if grep -qE 'path="\*"[^>]*Dashboard' "$ROOT/frontend/src/App.tsx" 2>/dev/null; then
  bad "N6 the catch-all route renders the Dashboard — unknown paths must render NotFound"
elif grep -qE 'path="\*"[^>]*NotFound' "$ROOT/frontend/src/App.tsx" 2>/dev/null; then
  good "N6 catch-all renders NotFound"
else
  bad "N6 no NotFound catch-all route in frontend/src/App.tsx"
fi

# ---------------------------------------------------------------------------
# N7 — retired supports_* probe vocabulary
# ---------------------------------------------------------------------------
N7_PATTERNS="$GATE_DIR/gate-patterns/retired-probes.txt"
N7_HITS=""
if [ -f "$N7_PATTERNS" ]; then
  N7_HITS="$(
    grep -rEn -f <(grep -vE '^[[:space:]]*(#|$)' "$N7_PATTERNS") \
      --include='*.py' --include='*.ts' --include='*.tsx' \
      "$ROOT/backend/app" "$ROOT/frontend/src" 2>/dev/null \
      | grep -v "gate-allow:" || true
  )"
fi
if [ -n "$N7_HITS" ]; then
  bad "N7 retired supports_* probe vocabulary reappeared (declare the capability instead):"
  echo "$N7_HITS" | sed 's/^/      /' >&2
else
  good "N7 no retired supports_* probe vocabulary"
fi

if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: OK"
  exit 0
fi
echo "RESULT: FAIL"
exit 1
