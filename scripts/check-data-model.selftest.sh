#!/usr/bin/env bash
# Self-test suite for the family data-model gate (guidelines/data-model.md §11).
#
# Fixtures are materialized at runtime into mktemp -d from heredoc builders —
# no committed fixture trees, so vendoring three copies stays cheap and
# byte-identity stays meaningful. Coverage is per-check: every D-check has a
# conforming tree that must pass and a violating tree that must fail
# (asserting *which* gate fired and that the exit code says so), plus the
# marker-completeness loop planting one string per vocabulary entry
# ("declared ⇒ detected" — the planted list is pinned TEST DATA and must
# stay in 1:1 correspondence with check-data-model.patterns.txt), the
# false-positive guard for the old_ look-behind, the gate-allow pragma,
# transition-mode semantics, and cwd-independence (the gate resolves paths
# from BASH_SOURCE).
#
# Usage: bash check-data-model.selftest.sh   (or the gate's --self-test)
# Exit: non-zero on any surprise; one `ok`/`FAIL` line per case.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/check-data-model.sh"
PATTERNS="$HERE/check-data-model.patterns.txt"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

ok_case()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail_case() { FAIL=$((FAIL + 1)); echo "FAIL $1"; echo "$2" | sed 's/^/     /'; }

# run_self DIR [ARGS...] -- run the gate's --self from a NEUTRAL cwd (the
# fixture's own vendored copy, like the family-convergence selftest). Sets
# RUN_OUT/RUN_RC.
run_self() {
  local dir="$1"
  shift
  RUN_OUT="$(cd "$WORK" && bash "$dir/scripts/check-data-model.sh" --self "$@" 2>&1)"
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

# ---------------------------------------------------------------------------
# pinned planted strings (test data — must mirror check-data-model.patterns.txt)
# ---------------------------------------------------------------------------
PLANTED_MARKERS=(
  "legacy" "deprecated" "old_marker" "thing_v2" "TODO(ai-refactor)"
)
PLANTED_GUARD=(
  "household_id" "threshold_value"
)

# ---------------------------------------------------------------------------
# fixture builders (heredoc materialization into mktemp -d)
# ---------------------------------------------------------------------------

# gate_artifacts DIR -- copy the gate's own artifacts into the fixture
# (same discipline as the family-convergence selftest): a conformant tree
# that contains the gate scripts must still pass — self-match is
# structurally impossible (D7 scans backend/app + frontend/src only).
gate_artifacts() {
  mkdir -p "$1/scripts"
  cp "$GATE" "$HERE/check-data-model.selftest.sh" "$PATTERNS" "$1/scripts/"
}

# fixture_conformant DIR -- a tree that passes D1–D7.
fixture_conformant() {
  local d="$1"
  mkdir -p "$d/backend/app/models" "$d/backend/alembic/versions" \
           "$d/backend/tests" "$d/frontend/src" "$d/contracts"
  cat > "$d/backend/app/models/base.py" <<'EOF'
from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))
EOF
  cat > "$d/backend/app/models/widget_model.py" <<'EOF'
from sqlalchemy import JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
EOF
  cat > "$d/backend/alembic/versions/0001_baseline.py" <<'EOF'
"""fixture baseline

Revision ID: 0001_baseline
"""

revision: str = "0001_baseline"
down_revision: str | None = None
EOF
  cat > "$d/backend/alembic/versions/0002_widgets.py" <<'EOF'
"""fixture widgets table"""

revision: str = "0002_widgets"
down_revision: str | None = "0001_baseline"
EOF
  cat > "$d/backend/tests/test_data_model.py" <<'EOF'
def test_model_db_drift_gate():
    # asserts the empty autogenerate diff against a migrated DB
    assert autogenerate_diff == []


def test_enum_check_parity():
    # asserts StrEnum members match the mirrored check_constraint rows
    assert enum_members == check_members
EOF
  cat > "$d/contracts/data-model.jsonb-inventory.txt" <<'EOF'
# table.column :: verdict (keep-open | typed-out)
widgets.payload :: keep-open fixture payload
EOF
  cat > "$d/contracts/data-model.lifecycle.txt" <<'EOF'
# table :: class (record | operational | transient)
widgets :: operational
EOF
  cat > "$d/frontend/src/app.ts" <<'EOF'
export const app = 1;
EOF
  gate_artifacts "$d"
}

# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

# T1 — the conformant tree (with gate artifacts) passes strict.
fixture_conformant "$WORK/t1"
run_self "$WORK/t1" --mode strict
expect "T1 conformant tree passes strict" 0 "RESULT: OK"

# D1 — naming_convention presence and completeness.
fixture_conformant "$WORK/d1a"
cat > "$WORK/d1a/backend/app/models/base.py" <<'EOF'
from sqlalchemy.orm import declarative_base

Base = declarative_base()
EOF
run_self "$WORK/d1a" --mode strict
expect "D1a missing naming_convention fires" 1 "FAIL D1: no naming_convention"

fixture_conformant "$WORK/d1b"
cat > "$WORK/d1b/backend/app/models/base.py" <<'EOF'
from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))
EOF
run_self "$WORK/d1b" --mode strict
expect "D1b missing convention key fires" 1 "FAIL D1: naming_convention lacks keys: ck"

# D2 — chain hygiene: id format, forks/heads, resolvable down_revisions.
fixture_conformant "$WORK/d2a"
cat > "$WORK/d2a/backend/alembic/versions/0002_widgets.py" <<'EOF'
"""fixture widgets table"""

revision: str = "w1i2d3g4e5t6_widgets"
down_revision: str | None = "0001_baseline"
EOF
run_self "$WORK/d2a" --mode strict
expect "D2a mnemonic revision id fires" 1 \
  "FAIL D2: revision id \`w1i2d3g4e5t6_widgets\` is not NNNN"

fixture_conformant "$WORK/d2b"
cat > "$WORK/d2b/backend/alembic/versions/0003_gadgets.py" <<'EOF'
"""fixture gadgets table"""

revision: str = "0003_gadgets"
down_revision: str | None = "0002_widgets"
EOF
cat > "$WORK/d2b/backend/alembic/versions/0003_widgets_extra.py" <<'EOF'
"""fixture widgets extras"""

revision: str = "0003_widgets_extra"
down_revision: str | None = "0002_widgets"
EOF
run_self "$WORK/d2b" --mode strict
expect "D2b fork/two heads fires" 1 "FAIL D2: (fork at 0002_widgets|expected exactly one head)"

fixture_conformant "$WORK/d2c"
cat > "$WORK/d2c/backend/alembic/versions/0002_widgets.py" <<'EOF'
"""fixture widgets table"""

revision: str = "0002_widgets"
down_revision: str | None = "0001_missing"
EOF
run_self "$WORK/d2c" --mode strict
expect "D2c unresolvable down_revision fires" 1 \
  "FAIL D2: 0002_widgets.py \(0002_widgets\) has unresolvable down_revision \`0001_missing\`"

fixture_conformant "$WORK/d2d"
cat > "$WORK/d2d/backend/alembic/versions/0001_baseline.py" <<'EOF'
"""fixture baseline (unannotated assignment form)"""

revision = "0001_baseline"
down_revision = None
EOF
cat > "$WORK/d2d/backend/alembic/versions/0002_widgets.py" <<'EOF'
"""fixture widgets table (unannotated assignment form)"""

revision = "0002_widgets"
down_revision = "0001_baseline"
EOF
run_self "$WORK/d2d" --mode strict
expect "D2d unannotated revision assignments parse" 0 "RESULT: OK"

# D3 — drift-gate test presence.
fixture_conformant "$WORK/d3"
cat > "$WORK/d3/backend/tests/test_data_model.py" <<'EOF'
def test_something():
    assert True
EOF
run_self "$WORK/d3" --mode strict
expect "D3 missing drift-gate test fires" 1 "FAIL D3: no model-DB drift gate test"

# D4 — enum/CHECK parity test presence.
fixture_conformant "$WORK/d4"
cat > "$WORK/d4/backend/tests/test_data_model.py" <<'EOF'
def test_model_db_drift_gate():
    assert autogenerate_diff == []


def test_nothing_here():
    assert True
EOF
run_self "$WORK/d4" --mode strict
expect "D4 missing enum-parity test fires" 1 "FAIL D4: no enum/CHECK parity test"

# D5 — JSONB inventory coverage, verdicts, staleness.
fixture_conformant "$WORK/d5a"
cat > "$WORK/d5a/backend/app/models/widget_model.py" <<'EOF'
from sqlalchemy import JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
EOF
run_self "$WORK/d5a" --mode strict
expect "D5a unclassified JSON column fires" 1 \
  "FAIL D5: unclassified JSON/JSONB column widgets.meta"

fixture_conformant "$WORK/d5b"
echo "ghosts.meta :: keep-open stale entry" >> "$WORK/d5b/contracts/data-model.jsonb-inventory.txt"
run_self "$WORK/d5b" --mode strict
expect "D5b stale inventory entry fires" 1 \
  "FAIL D5: stale inventory entry ghosts.meta"

fixture_conformant "$WORK/d5c"
sed -i 's/widgets.payload :: keep-open.*/widgets.payload :: maybe-open/' \
  "$WORK/d5c/contracts/data-model.jsonb-inventory.txt"
run_self "$WORK/d5c" --mode strict
expect "D5c unknown verdict fires" 1 "FAIL D5: widgets.payload has unknown verdict \`maybe-open\`"

# D6 — lifecycle inventory coverage, classes, staleness.
fixture_conformant "$WORK/d6a"
cat > "$WORK/d6a/backend/app/models/gadget_model.py" <<'EOF'
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Gadget(Base):
    __tablename__ = "gadgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
EOF
run_self "$WORK/d6a" --mode strict
expect "D6a unclassified table fires" 1 \
  "FAIL D6: unclassified table gadgets"

fixture_conformant "$WORK/d6b"
sed -i 's/widgets :: operational/widgets :: archival/' \
  "$WORK/d6b/contracts/data-model.lifecycle.txt"
run_self "$WORK/d6b" --mode strict
expect "D6b unknown lifecycle class fires" 1 \
  "FAIL D6: widgets has unknown lifecycle class \`archival\`"

fixture_conformant "$WORK/d6c"
echo "ghosts :: transient" >> "$WORK/d6c/contracts/data-model.lifecycle.txt"
run_self "$WORK/d6c" --mode strict
expect "D6c stale lifecycle entry fires" 1 "FAIL D6: stale lifecycle entry ghosts"

# D7 — legacy naming, false-positive guard, pragma.
fixture_conformant "$WORK/d7a"
cat > "$WORK/d7a/frontend/src/housekeeping.ts" <<'EOF'
export const household_id = 1;
export const threshold_value = 2;
EOF
run_self "$WORK/d7a" --mode strict
expect "D7a old_ look-behind passes innocent words" 0 "RESULT: OK"

fixture_conformant "$WORK/d7b"
cat > "$WORK/d7b/frontend/src/retired.ts" <<'EOF'
export const old_marker = 1;
EOF
run_self "$WORK/d7b" --mode strict
expect "D7b planted marker fires" 1 "FAIL D7: legacy naming in code:"
expect "D7b finding names the line" 1 "retired.ts:1:export const old_marker = 1;"

fixture_conformant "$WORK/d7c"
cat > "$WORK/d7c/frontend/src/retired.ts" <<'EOF'
export const old_marker = 1;  # gate-allow: (?<![A-Za-z0-9_])old_[a-z]
EOF
run_self "$WORK/d7c" --mode strict
expect "D7c gate-allow pragma suppresses" 0 "RESULT: OK"

# Transition mode — findings reported, exit 0.
fixture_conformant "$WORK/tmode"
sed -i 's/widgets :: operational/widgets :: archival/' \
  "$WORK/tmode/contracts/data-model.lifecycle.txt"
run_self "$WORK/tmode" --mode transition
expect "transition mode reports but passes" 0 "FAIL D6: widgets has unknown lifecycle class"
expect "transition mode is named in output" 0 "transition mode — findings reported, not failing"

# Marker completeness — every declared vocabulary entry is detected
# ("declared ⇒ detected"), and the planted list mirrors the patterns file.
vocab_witnesses="$(grep -vE '^\s*(#|$)' "$PATTERNS" | sed 's/.*:: *//' | sort)"
planted_sorted="$(printf '%s\n' "${PLANTED_MARKERS[@]}" | sort)"
if [ "$vocab_witnesses" = "$planted_sorted" ]; then
  ok_case "vocabulary/planted correspondence"
else
  fail_case "vocabulary/planted correspondence" \
    "patterns: $vocab_witnesses != planted: $planted_sorted"
fi

for witness in "${PLANTED_MARKERS[@]}"; do
  fixture_conformant "$WORK/mc"
  printf 'export const %s = 1;\n' "$witness" > "$WORK/mc/frontend/src/planted.ts"
  run_self "$WORK/mc" --mode strict
  expect "marker trips D7: $witness" 1 "planted.ts:1:"
done

# cwd-independence — same findings from two different cwds.
fixture_conformant "$WORK/cwd"
sed -i 's/widgets :: operational/widgets :: archival/' \
  "$WORK/cwd/contracts/data-model.lifecycle.txt"
run_self "$WORK/cwd" --mode strict
out_a="$RUN_OUT" rc_a="$RUN_RC"
RUN_OUT="$(cd "$WORK/cwd" && bash "$WORK/cwd/scripts/check-data-model.sh" --self --mode strict 2>&1)"
RUN_RC=$?
if [ "$RUN_OUT" = "$out_a" ] && [ "$RUN_RC" = "$rc_a" ]; then
  ok_case "cwd-independent output"
else
  fail_case "cwd-independent output" "neutral-cwd run and in-tree run disagree"
fi

echo "--"
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" = 0 ]
