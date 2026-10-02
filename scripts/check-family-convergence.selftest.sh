#!/usr/bin/env bash
# Self-test suite for the family-convergence gate (plan 23 D4 / T1–T6, T13–T14).
#
# Fixtures are materialized at runtime into mktemp -d from heredoc builders —
# no committed fixture trees, so vendoring four copies stays cheap and
# byte-identity stays meaningful. Coverage is per-gate and per-marker:
# every C-check has a conforming tree that must pass and a violating tree
# that must fail (asserting *which* gate fired and that the exit code says
# so), and the marker-completeness loop plants one string per vocabulary
# entry and asserts the matching gate trips ("declared ⇒ detected").
#
# The planted lists below are pinned TEST DATA on purpose: they must stay in
# 1:1 correspondence with scripts/gate-patterns/*.txt (asserted at runtime).
# Deleting a vocabulary line therefore breaks two cases — the correspondence
# and that entry's trip — so the loop has teeth.
#
# Usage: bash check-family-convergence.selftest.sh   (or the gate's --self-test)
# Exit: non-zero on any surprise; one `ok`/`FAIL` line per case.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/check-family-convergence.sh"
PATTERNS_DIR="$HERE/gate-patterns"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Sourcing exposes check_repo / c*_ / vocab_file for the in-process cases
# (the gate's main() is guarded against source-time execution).
# shellcheck source=check-family-convergence.sh
source "$GATE"

PASS=0
FAIL=0

ok_case()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail_case() { FAIL=$((FAIL + 1)); echo "FAIL $1"; echo "$2" | sed 's/^/     /'; }

# run_self DIR -- run the gate's --self mode inside DIR; sets RUN_OUT/RUN_RC.
run_self() {
  RUN_OUT="$(cd "$1" && bash "$GATE" --self 2>&1)"
  RUN_RC=$?
}

# expect NAME WANT_RC WANT_RE [REJECT_RE] -- assert on RUN_OUT/RUN_RC.
expect() {
  local name="$1" want_rc="$2" want_re="$3" reject_re="${4:-}" bad=""
  [ "$RUN_RC" != "$want_rc" ] && bad="rc=$RUN_RC want=$want_rc"
  grep -qE -- "$want_re" <<<"$RUN_OUT" || bad="$bad; missing /$want_re/"
  if [ -n "$reject_re" ] && grep -qE -- "$reject_re" <<<"$RUN_OUT"; then
    bad="$bad; unexpected /$reject_re/"
  fi
  if [ -n "$bad" ]; then fail_case "$name" "$bad"$'\n'"$RUN_OUT"; else ok_case "$name"; fi
}

# expect_lines NAME NEEDLE... -- assert every NEEDLE appears in RUN_OUT.
expect_lines() {
  local name="$1" bad="" needle
  shift
  for needle in "$@"; do
    grep -qF -- "$needle" <<<"$RUN_OUT" || bad="$bad; missing '$needle'"
  done
  if [ -n "$bad" ]; then fail_case "$name" "$bad"$'\n'"$RUN_OUT"; else ok_case "$name"; fi
}

# ---------------------------------------------------------------------------
# pinned planted strings (test data — must mirror scripts/gate-patterns/*.txt)
# ---------------------------------------------------------------------------
PLANTED_MARKERS=(
  "ca-drawing" "ca-image" "ca-material" "ca-course" "caq/v" "ca-backup/v"
  "ca-skills/v" "x-ca-" "LEGACY_KIND_MAP" "card_kind_from_legacy"
  "legacy_kind_from_card"
)
PLANTED_ENV=(
  "DATABASE_URL" "APP_ENV" "DATA_DIR" "UPLOAD_DIR" "SPA_DIST" "API_HOST"
  "API_PORT" "CORS_ORIGINS" "IDENTITY_MODE" "AUTH_MODE" "DEMO_MODE"
  "REGISTRATION_ENABLED" "COOKIE_SECURE" "TRUSTED_PROXY_COUNT"
  "RATE_LIMIT_ENABLED" "AUTH_RATE_LIMIT" "AUTH_EMAIL_RATE_LIMIT"
  "SESSION_KEY" "REFRESH_KEY" "DATA_KEY"
)

# ---------------------------------------------------------------------------
# fixture builders (heredoc materialization into mktemp -d)
# ---------------------------------------------------------------------------

# gate_artifacts DIR -- copy the gate's own artifacts into the fixture, so
# every case doubles as the F2 regression case (T1): a conformant tree that
# *contains copies of the gate scripts with their pattern vocabulary* must
# still pass C4 — the plan-20 self-match defect is structurally impossible.
gate_artifacts() {
  mkdir -p "$1/scripts/gate-patterns"
  cp "$GATE" "$HERE/check-family-convergence.selftest.sh" "$1/scripts/"
  cp "$PATTERNS_DIR/"*.txt "$1/scripts/gate-patterns/"
}

# fixture_conformant DIR -- a tree that passes C1–C6 (incl. T3 prefixed-env
# negatives and T1/T6).
fixture_conformant() {
  local d="$1"
  mkdir -p "$d/backend/app" "$d/backend/tests" "$d/frontend/src" \
           "$d/scripts" "$d/docker" "$d/.github/workflows"
  cat > "$d/pyproject.toml" <<'EOF'
[project]
name = "fixture"
EOF
  cat > "$d/uv.lock" <<'EOF'
version = 1
EOF
  cat > "$d/backend/pyproject.toml" <<'EOF'
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F"]

[tool.ruff.lint.flake8-bugbear]

[tool.pytest.ini_options]
markers = ["contract: family contract drift gate"]
EOF
  cat > "$d/backend/app/local.py" <<'EOF'
def run_migrations():
    command.upgrade  # migrations live here and nowhere else (C3)
EOF
  cat > "$d/backend/app/other.py" <<'EOF'
VALUE = 1
EOF
  cat > "$d/backend/tests/test_ok.py" <<'EOF'
def test_ok():
    assert True
EOF
  cat > "$d/scripts/run.sh" <<'EOF'
#!/usr/bin/env bash
# T3: product-prefixed names are fine (boundary anchoring, no blanket filter).
export SA_APP_ENV=development
export SA_AUTH_MODE=authenticated
export CAREER_DATABASE_URL=postgresql://x/y
export HA_APP_ENV=production
EOF
  cat > "$d/docker/docker-compose.yml" <<'EOF'
services:
  app:
    environment:
      HA_APP_ENV: production
      SA_DATABASE_URL: postgresql://x/y
  worker:
    environment:
      - CAREER_AUTH_MODE=authenticated
EOF
  cat > "$d/.github/workflows/ci.yml" <<'EOF'
jobs:
  test:
    steps:
      - run: pytest -m contract
EOF
  gate_artifacts "$d"
}

# fixture_unprefixed_env DIR -- T4: the three unprefixed read/write forms.
fixture_unprefixed_env() {
  local d="$1"
  fixture_conformant "$d"
  cat > "$d/backend/app/reader.py" <<'EOF'
import os
DATA_KEY = os.environ["DATA_KEY"]
EOF
  cat > "$d/scripts/exporter.sh" <<'EOF'
#!/usr/bin/env bash
export APP_ENV=production
EOF
  cat > "$d/backend/app/getter.py" <<'EOF'
import os
MODE = os.getenv("AUTH_MODE")
EOF
}

# fixture_compose_env DIR -- T13: YAML/compose assignment forms (the exact
# surface the HA_APP_ENV rename spread through).
fixture_compose_env() {
  local d="$1"
  fixture_conformant "$d"
  cat > "$d/docker/docker-compose.yml" <<'EOF'
services:
  app:
    environment:
      APP_ENV: production
      SA_DATABASE_URL: postgresql://x/y
EOF
  cat > "$d/docker/docker-compose.db.yml" <<'EOF'
services:
  db:
    environment:
      - DATA_KEY=abc123
EOF
}

# fixture_gate_allow_pragma DIR WITH_PRAGMA -- T14: a bare name on a
# pragma'd line passes; the same line without the pragma trips C2.
fixture_gate_allow_pragma() {
  local d="$1" with="$2" pragma=""
  fixture_conformant "$d"
  [ "$with" = 1 ] && pragma='  # gate-allow: COOKIE_SECURE'
  cat > "$d/backend/tests/test_inert.py" <<EOF
def test_old_name_is_inert():
    monkeypatch.setenv("COOKIE_SECURE", "0")${pragma}
    monkeypatch.setenv("COOKIE_SECURE", "0")
EOF
}

# fixture_migrations_elsewhere DIR -- T5/C3.
fixture_migrations_elsewhere() {
  local d="$1"
  fixture_conformant "$d"
  cat > "$d/backend/app/migrate.py" <<'EOF'
def upgrade():
    command.upgrade
EOF
}

# fixture_requirements_txt DIR -- T5/C5.
fixture_requirements_txt() {
  local d="$1"
  fixture_conformant "$d"
  echo "fastapi==0.115" > "$d/backend/requirements.txt"
}

# fixture_missing_contract_ci DIR -- T5/C6.
fixture_missing_contract_ci() {
  local d="$1"
  fixture_conformant "$d"
  cat > "$d/.github/workflows/ci.yml" <<'EOF'
jobs:
  test:
    steps:
      - run: pytest -q
EOF
}

# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

echo "== family-convergence gate self-test =="

# T1 + T3 + T6: conformant tree (prefixed env, gate-script copies present)
# passes every gate, exit 0, RESULT: OK.
d="$WORK/t1-conformant"; fixture_conformant "$d"
run_self "$d"
expect "T1+T3+T6 conformant tree with gate copies -> RESULT: OK" 0 "RESULT: OK" "FAIL"

# T2a: marker completeness — one planted string per vocabulary entry trips C4.
for marker in "${PLANTED_MARKERS[@]}"; do
  d="$WORK/t2-c4"; rm -rf "$d"; fixture_conformant "$d"
  printf 'PLANTED = %s\n' "\"$marker\"" > "$d/backend/app/planted.py"
  run_self "$d"
  expect "T2 C4 trips for planted marker '$marker'" 1 "FAIL C4"
done

# T2b: env-name completeness — one planted string per vocabulary entry trips C2.
for name in "${PLANTED_ENV[@]}"; do
  d="$WORK/t2-c2"; rm -rf "$d"; fixture_conformant "$d"
  printf 'VALUE = os.environ["%s"]\n' "$name" > "$d/backend/app/planted.py"
  run_self "$d"
  expect "T2 C2 trips for planted env name '$name'" 1 "FAIL C2"
done

# T2c: correspondence — the pinned planted lists must stay 1:1 with the
# vocabulary files (this is the case that fails when a vocabulary line is
# deleted — the completeness loop must have teeth).
vocab_markers="$(vocab_file "$PATTERNS_DIR/legacy-markers.txt" | cut -f2 | sort)"
vocab_env="$(vocab_file "$PATTERNS_DIR/env-names.txt" | cut -f2 | sort)"
pinned_markers="$(printf '%s\n' "${PLANTED_MARKERS[@]}" | sort)"
pinned_env="$(printf '%s\n' "${PLANTED_ENV[@]}" | sort)"
if [ "$vocab_markers" = "$pinned_markers" ] && [ "$vocab_env" = "$pinned_env" ]; then
  ok_case "T2 vocabulary <-> planted-list correspondence (1:1)"
else
  fail_case "T2 vocabulary <-> planted-list correspondence (1:1)" \
    "markers vocab: $vocab_markers / pinned: $pinned_markers
env vocab: $vocab_env / pinned: $pinned_env"
fi

# T4: unprefixed read/write forms trip C2 and name file:line.
d="$WORK/t4-unprefixed"; fixture_unprefixed_env "$d"
run_self "$d"
expect "T4 unprefixed env forms -> FAIL C2" 1 "FAIL C2"
expect_lines "T4 output names file:line for all three forms" \
  "backend/app/reader.py:2:" "scripts/exporter.sh:2:" "backend/app/getter.py:2:"

# T13: YAML/compose assignment forms trip C2 (the F1 root-cause surface).
d="$WORK/t13-compose"; fixture_compose_env "$d"
run_self "$d"
expect "T13 compose YAML env forms -> FAIL C2" 1 "FAIL C2"
expect_lines "T13 output names both compose lines" \
  "docker/docker-compose.yml:4:" "docker/docker-compose.db.yml:4:"

# T14a: without the pragma the bare name trips C2 and the finding names it.
d="$WORK/t14-no-pragma"; fixture_gate_allow_pragma "$d" 0
run_self "$d"
expect "T14 bare name without pragma -> FAIL C2" 1 "FAIL C2"
expect_lines "T14 finding names the offending line" "backend/tests/test_inert.py:3:"

# T14b: with the pragma the same line passes; the un-pragma'd twin is still
# caught (the pragma names one line, it does not silence the file).
d="$WORK/t14-pragma"; fixture_gate_allow_pragma "$d" 1
run_self "$d"
expect "T14 pragma'd line passes, twin still caught" 1 "FAIL C2"
expect_lines "T14 output names only the un-pragma'd line" "backend/tests/test_inert.py:3:"
if grep -qF "backend/tests/test_inert.py:2:" <<<"$RUN_OUT"; then
  fail_case "T14 pragma'd line 2 is not reported" "$RUN_OUT"
else
  ok_case "T14 pragma'd line 2 is not reported"
fi

# T14c: a pragma on a comment-only line covers the line below it (for
# surfaces where trailing comments are unsafe — Dockerfile ENV lines).
for with in 1 0; do
  d="$WORK/t14-above"; rm -rf "$d"; fixture_conformant "$d"
  pragma=""
  [ "$with" = 1 ] && pragma='# gate-allow: SPA_DIST — declared deferral
'
  cat > "$d/docker/Dockerfile" <<EOF
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \\
${pragma}    SPA_DIST=/app/frontend/dist
EOF
  run_self "$d"
  if [ "$with" = 1 ]; then
    expect "T14 comment-line pragma covers the next line" 0 "RESULT: OK" "FAIL C2"
  else
    expect "T14 next line without the comment pragma -> FAIL C2" 1 "FAIL C2"
  fi
done

# T5/C1: tool-config drift between two repos is a named C1 finding
# (in-process: family parity needs two check_repo calls in one process).
d="$WORK/t5-c1-a"; fixture_conformant "$d"
e="$WORK/t5-c1-b"; fixture_conformant "$e"
sed -i 's/line-length = 100/line-length = 88/' "$e/backend/pyproject.toml"
CANON_RUFF=''
combined="$(
  CANON_RUFF=''
  check_repo "$d" backend/pyproject.toml 2>&1
  printf '<<<C1SEP>>>\n'
  check_repo "$e" backend/pyproject.toml 2>&1
)"
out_a="${combined%%<<<C1SEP>>>*}"
out_b="${combined#*<<<C1SEP>>>}"
RUN_OUT="$out_a"; RUN_RC=0
expect "T5/C1 conformant block compared first" 0 "^$" "FAIL C1"
RUN_OUT="$out_b"
expect "T5/C1 ruff block drift -> FAIL C1" 0 "FAIL C1"

# T5/C3: command.upgrade outside app/local.py.
d="$WORK/t5-c3"; fixture_migrations_elsewhere "$d"
run_self "$d"
expect "T5/C3 migrations outside app/local.py -> FAIL C3" 1 "FAIL C3"

# T5/C5: requirements.txt present.
d="$WORK/t5-c5"; fixture_requirements_txt "$d"
run_self "$d"
expect "T5/C5 requirements.txt present -> FAIL C5" 1 "FAIL C5"

# T5/C6: no '-m contract' in CI.
d="$WORK/t5-c6"; fixture_missing_contract_ci "$d"
run_self "$d"
expect "T5/C6 missing contract gate in CI -> FAIL C6" 1 "FAIL C6"

# D2/F2 structural: the gate script must contain no vocabulary literal —
# patterns are data, so the gate can never match its own patterns.
gate_text="$(cat "$GATE")"
missing_lit=""
for marker in "${PLANTED_MARKERS[@]}" "${PLANTED_ENV[@]}"; do
  if grep -qF -- "$marker" <<<"$gate_text"; then
    missing_lit="$missing_lit $marker"
  fi
done
if [ -z "$missing_lit" ]; then
  ok_case "D2 gate script contains no vocabulary literals"
else
  fail_case "D2 gate script contains no vocabulary literals" "literals:$missing_lit"
fi

echo "--"
echo "self-test: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] || exit 1
exit 0
