#!/usr/bin/env bash
# Self-test suite for check-no-legacy.sh (plan 9 W6).
#
# Fixtures are materialized at runtime into mktemp -d — no committed fixture
# trees. Every check gets a conforming tree that must pass and a red host that
# must fail with *its own* gate named (N1–N7), plus cwd-independence. The
# planted red hosts are the non-vacuity evidence: the gate is not green because
# it looks at nothing.
#
# Usage: bash scripts/check-no-legacy.selftest.sh
# Exit: non-zero on any surprise; one `ok`/`FAIL` line per case.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/check-no-legacy.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

ok_case()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail_case() { FAIL=$((FAIL + 1)); echo "FAIL $1"; echo "$2" | sed 's/^/     /'; }

run_gate() {
  RUN_OUT="$(bash "$GATE" --root "$1" 2>&1)"
  RUN_RC=$?
}

expect() {
  local name="$1" want_rc="$2" want_re="$3" bad=""
  [ "$RUN_RC" != "$want_rc" ] && bad="rc=$RUN_RC want=$want_rc"
  grep -qE -- "$want_re" <<<"$RUN_OUT" || bad="$bad; missing /$want_re/"
  if [ -n "$bad" ]; then fail_case "$name" "$bad"$'\n'"$RUN_OUT"; else ok_case "$name"; fi
}

RESOURCES=(Patient Observation Condition EpisodeOfCare Encounter AllergyIntolerance \
  MedicationStatement MedicationRequest Medication Substance Immunization DiagnosticReport \
  DocumentReference Device Communication Organization Practitioner Provenance CodeSystem ValueSet)

# fixture_clean DIR — a tree that passes N1–N7.
fixture_clean() {
  local d="$1" i n decorators
  mkdir -p "$d/scripts/lib" "$d/scripts/gate-patterns" \
           "$d/backend/app/facade" "$d/backend/app/api/v1/endpoints" \
           "$d/backend/app/models" "$d/backend/scripts" \
           "$d/docs" "$d/frontend/src/components" "$d/frontend/src/tests"

  cp "$GATE" "$d/scripts/"
  cp "$HERE/check-no-legacy.selftest.sh" "$d/scripts/"
  cp "$HERE/lib/check_frontend_importers.py" "$d/scripts/lib/"
  cp "$HERE"/gate-patterns/*.txt "$d/scripts/gate-patterns/"
  cp "$HERE/../backend/scripts/check_docs_counts.py" "$d/backend/scripts/"

  local joined
  joined="$(IFS=', '; echo "${RESOURCES[*]}")"
  printf 'exposes **20 resources** (%s)\n' "$joined" > "$d/README.md"
  {
    printf '> 347 HTTP/WS handlers across 42 router modules (fixture).\n'
    printf -- '- [FHIR R4 facade](#fhir) — canonical interop surface (20 resource types)\n'
    printf '**20 registered resource types:** %s\n' "$joined"
  } > "$d/docs/API.md"

  {
    echo 'class ResourceEntry:'
    echo '    pass'
    for i in "${!RESOURCES[@]}"; do
      printf 'ResourceEntry(resource_type="%s")\n' "${RESOURCES[$i]}"
    done
  } > "$d/backend/app/facade/registry.py"

  # 42 router modules carrying 347 handlers (11·9 + 31·8).
  for i in $(seq 1 42); do
    if [ "$i" -le 11 ]; then decorators=9; else decorators=8; fi
    {
      echo 'from fastapi import APIRouter'
      echo 'router = APIRouter()'
      for n in $(seq 1 "$decorators"); do
        printf '@router.get("/r%s-h%s")\nasync def h%s():\n    return {}\n' "$i" "$n" "$n"
      done
    } > "$d/backend/app/api/v1/endpoints/module_$i.py"
  done

  echo 'class AiTaskAssignment:' > "$d/backend/app/models/ai_task_model.py"
  echo '    pass' >> "$d/backend/app/models/ai_task_model.py"

  : > "$d/frontend/pnpm-lock.yaml"
  echo "import App from './App';" > "$d/frontend/src/main.tsx"
  cat > "$d/frontend/src/App.tsx" <<'TSX'
import { useRegisterSW } from 'virtual:pwa-register/react';
import Used from './components/Used';
const { updateServiceWorker } = useRegisterSW({});
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Used />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
TSX
  : > "$d/frontend/src/sw.ts"
  echo 'export default function Used() { return null; }' > "$d/frontend/src/components/Used.tsx"
  : > "$d/frontend/src/tests/setup.ts"
}

WORKDIR="$WORK/fixture"
fixture_clean "$WORKDIR"

# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------
run_gate "$WORKDIR"
expect "clean tree passes" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N1 — a zero-importer component
# ---------------------------------------------------------------------------
echo 'export default function Orphan() { return null; }' > "$WORKDIR/frontend/src/components/Orphan.tsx"
run_gate "$WORKDIR"
expect "N1 red host: zero-importer component" 1 "N1 zero-importer component"
grep -q "Orphan.tsx" <<<"$RUN_OUT" || fail_case "N1 names the orphan" "orphan not listed"
rm "$WORKDIR/frontend/src/components/Orphan.tsx"
run_gate "$WORKDIR"
expect "N1 restored" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N2 — a second lockfile
# ---------------------------------------------------------------------------
: > "$WORKDIR/frontend/package-lock.json"
run_gate "$WORKDIR"
expect "N2 red host: npm lockfile" 1 "N2 second frontend lockfile"
rm "$WORKDIR/frontend/package-lock.json"
run_gate "$WORKDIR"
expect "N2 restored" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N3 — documented count drift
# ---------------------------------------------------------------------------
sed -i 's/\*\*20 resources\*\*/**19 resources**/' "$WORKDIR/README.md"
run_gate "$WORKDIR"
expect "N3 red host: stale FHIR count" 1 "N3 documented counts drift"
sed -i 's/\*\*19 resources\*\*/**20 resources**/' "$WORKDIR/README.md"
sed -i 's/347 HTTP\/WS handlers across 42 router modules/330+ HTTP\/WS handlers across 41 router modules/' "$WORKDIR/docs/API.md"
run_gate "$WORKDIR"
expect "N3 red host: stale router counts" 1 "N3 documented counts drift"
sed -i 's/330+ HTTP\/WS handlers across 41 router modules/347 HTTP\/WS handlers across 42 router modules/' "$WORKDIR/docs/API.md"
run_gate "$WORKDIR"
expect "N3 restored" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N4 — a retired plans 3–5 shape
# ---------------------------------------------------------------------------
printf 'async def set_sync_cursor(user, key, value):\n    return value\n' > "$WORKDIR/backend/app/sync_cursor.py"
run_gate "$WORKDIR"
expect "N4 red host: JSONB cursor helper" 1 "N4 a retired plans"
rm "$WORKDIR/backend/app/sync_cursor.py"
run_gate "$WORKDIR"
expect "N4 restored" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N5 — a resurrected deleted surface
# ---------------------------------------------------------------------------
mkdir -p "$WORKDIR/frontend/src/pages/Settings"
echo 'export default function Preferences() { return null; }' > "$WORKDIR/frontend/src/pages/Settings/Preferences.tsx"
run_gate "$WORKDIR"
expect "N5 red host: resurrected Preferences" 1 "N5 a deleted duplicate"
rm "$WORKDIR/frontend/src/pages/Settings/Preferences.tsx"
run_gate "$WORKDIR"
expect "N5 restored" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N6 — service worker + 404 catch-all
# ---------------------------------------------------------------------------
echo "useRegisterSW({});" >> "$WORKDIR/frontend/src/App.tsx"
run_gate "$WORKDIR"
expect "N6 red host: duplicate SW registration" 1 "N6 expected exactly one service-worker"
sed -i '$ d' "$WORKDIR/frontend/src/App.tsx"
sed -i 's|element={<NotFoundPage />}|element={<Dashboard />}|' "$WORKDIR/frontend/src/App.tsx"
run_gate "$WORKDIR"
expect "N6 red host: Dashboard catch-all" 1 "N6 the catch-all route renders the Dashboard"
sed -i 's|element={<Dashboard />}|element={<NotFoundPage />}|' "$WORKDIR/frontend/src/App.tsx"
run_gate "$WORKDIR"
expect "N6 restored" 0 "RESULT: OK"

# ---------------------------------------------------------------------------
# N7 — a retired supports_* probe
# ---------------------------------------------------------------------------
printf '# supports_documents gone\ndef pull_documents(self):\n    return []\n' > "$WORKDIR/backend/app/probe.py"
run_gate "$WORKDIR"
expect "N7 red host: retired probe vocabulary" 1 "N7 retired supports_"
# the family gate-allow pragma is honoured
printf '# supports_documents gone  # gate-allow: history\ndef pull_documents(self):\n    return []\n' > "$WORKDIR/backend/app/probe.py"
run_gate "$WORKDIR"
expect "N7 gate-allow pragma skips the line" 0 "RESULT: OK"
rm "$WORKDIR/backend/app/probe.py"

# ---------------------------------------------------------------------------
# cwd independence
# ---------------------------------------------------------------------------
RUN_OUT="$(cd "$WORK" && bash "$GATE" --root "$WORKDIR" 2>&1)"
RUN_RC=$?
expect "gate is truthful from any cwd" 0 "RESULT: OK"

echo "no-legacy self-test: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
