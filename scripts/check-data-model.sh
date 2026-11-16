#!/usr/bin/env bash
# Family data-model convergence gate (guidelines/data-model.md).
#
# Enforces, per schema-bearing repo (FastAPI + SQLAlchemy + Alembic), the
# family data-model constitution:
#   D1  naming_convention on the declarative metadata — the five family
#       keys (pk fk ck uq ix) render every constraint name
#   D2  alembic chain hygiene — sequential zero-padded revision ids
#       (NNNN or NNNN_slug), unique, every down_revision resolves, exactly
#       one head, linear (no forks)
#   D3  model⇄DB drift gate present — a test asserting the empty
#       autogenerate diff (guidelines/data-model.md §3)
#   D4  enum/CHECK parity gate present — a test asserting Python enum
#       members match the mirrored DB CHECKs (§6)
#   D5  JSONB inventory — every JSON/JSONB model column is classified
#       keep-open|typed-out in contracts/data-model.jsonb-inventory.txt (§5)
#   D6  lifecycle inventory — every mapped table is classified
#       record|operational|transient in contracts/data-model.lifecycle.txt (§7)
#   D7  no legacy naming (vocabulary: check-data-model.patterns.txt) in
#       backend/app + frontend/src (§8). Binary/data payloads and
#       byte-identical vendored copies are not host vocabulary and are
#       skipped (their words are owned by the family canonical).
#
# Modes: strict (findings fail) or transition (findings reported, exit 0) —
# repos mid-migration run transition until their schema work lands
# (guidelines/testing.md "Drift guards"); flip the mode in BOTH the REPOS
# table below and the repo's data-model.yml when the debt clears. The
# family scan carries the per-repo mode table; --self defaults to strict.
#
# Honest limits (guidelines/data-model.md §9): D3/D4 are presence checks —
# the parity assertions themselves run in each repo's pytest suite against
# a migrated DB; D5/D6 discovery is AST-textual over declarative classes
# (mapped_column/Column) and does not see Table()-style DDL or alembic
# revisions.
#
# Vocabulary (D7) is DATA, not code (plan-23 discipline): the patterns
# live in scripts/check-data-model.patterns.txt and this script builds its
# matcher from that file at runtime — no marker literals here, so the gate
# can never match its own patterns.
#
# Vendored copies in product repos must stay byte-identical to this file
# (sha-checked by dev's scripts/verify-wiring.sh — the self-test and the
# patterns data are vendored and checked with it). Paths resolve from
# BASH_SOURCE, so the gate is truthful from any cwd.
#
# Usage:
#   ./check-data-model.sh                 # family scan from the dev repo
#   ./check-data-model.sh --strict        # fail on every repo (post-migration)
#   ./check-data-model.sh --self [--mode transition|strict]
#                                         # self-check one repo from its root
#   ./check-data-model.sh --self-test     # run the gate's own test suite
set -uo pipefail

GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_ROOT="$(cd "$GATE_DIR/.." && pwd)"
WORKSPACE="$(dirname "$DEV_ROOT")"
PATTERNS="$GATE_DIR/check-data-model.patterns.txt"

# repo (relative to workspace) : mode
REPOS=(
  "study-assistant:strict"
  "career-assistant:transition"
  "health-assistant/core:strict"
)

STRICT_ALL=0; SELF=0; MODE_OVERRIDE=""
PREV_ARG=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT_ALL=1 ;;
    --self) SELF=1 ;;
    --mode) PREV_ARG="--mode" ;;
    --self-test) : ;;
    transition|strict)
      if [ "$PREV_ARG" = "--mode" ]; then MODE_OVERRIDE="$arg"; else
        echo "unknown arg: $arg (use --strict, --self, --mode transition|strict)" >&2; exit 2
      fi ;;
    *) echo "unknown arg: $arg (use --strict, --self, --mode transition|strict)" >&2; exit 2 ;;
  esac
  [ "$PREV_ARG" = "--mode" ] && [ "$arg" != "--mode" ] && PREV_ARG=""
done

REPO_FAIL=0

# ---------------------------------------------------------------------------
# shared model scan (python AST) — prints one record per line:
#   table<TAB>name    json<TAB>table<TAB>column    err<TAB>path<TAB>message
# ---------------------------------------------------------------------------
model_records() { # dir
  python3 - "$1" <<'PY'
import ast, os, re, sys

root = sys.argv[1]
base = os.path.join(root, "backend", "app")
tables, jsoncols, errs = [], [], []
json_name = re.compile(r"JSONB?$")

for dirpath, dirnames, filenames in os.walk(base):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv", "venv")]
    for name in sorted(filenames):
        if not name.endswith(".py"):
            continue
        path = os.path.join(dirpath, name)
        with open(path, "rb") as fh:
            blob = fh.read()
        if b"\0" in blob or b"__tablename__" not in blob:
            continue
        rel = os.path.relpath(path, root)
        try:
            tree = ast.parse(blob.decode("utf-8", errors="replace"))
        except SyntaxError as exc:
            errs.append((rel, f"syntax error: {exc.msg} (line {exc.lineno})"))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            table = None
            columns = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    targets, value = stmt.targets, stmt.value
                elif isinstance(stmt, ast.AnnAssign):
                    targets, value = [stmt.target], stmt.value
                else:
                    continue
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if target.id == "__tablename__" and isinstance(value, ast.Constant) \
                            and isinstance(value.value, str):
                        table = value.value
                        continue
                    if not isinstance(value, ast.Call):
                        continue
                    func = value.func
                    fname = func.id if isinstance(func, ast.Name) else (
                        func.attr if isinstance(func, ast.Attribute) else "")
                    if fname not in ("mapped_column", "Column"):
                        continue
                    is_json = any(
                        (isinstance(n, ast.Name) and json_name.fullmatch(n.id))
                        or (isinstance(n, ast.Attribute) and json_name.fullmatch(n.attr))
                        for n in ast.walk(value)
                    )
                    if not is_json:
                        continue
                    if value.args and isinstance(value.args[0], ast.Constant) \
                            and isinstance(value.args[0].value, str):
                        columns.append(value.args[0].value)
                    else:
                        columns.append(target.id)
            if table:
                tables.append(table)
                for col in columns:
                    jsoncols.append((table, col))

for name in sorted(set(tables)):
    print(f"table\t{name}")
for table, col in sorted(set(jsoncols)):
    print(f"json\t{table}\t{col}")
for rel, msg in errs:
    print(f"err\t{rel}\t{msg}")
PY
}

# ---------------------------------------------------------------------------
# inventory files (guidelines/data-model.md §5/§7 — policy is data):
#   contracts/data-model.jsonb-inventory.txt   table.column :: verdict
#   contracts/data-model.lifecycle.txt         table :: class
# ---------------------------------------------------------------------------
jsonb_inventory_check() { # dir -> findings, exit 1 on any
  local dir="$1" rec rc
  rec="$(mktemp)"
  model_records "$dir" > "$rec"
  python3 - "$dir" "$rec" <<'PY'
import sys

root = sys.argv[1]
inv_path = root + "/contracts/data-model.jsonb-inventory.txt"
VERDICTS = {"keep-open", "typed-out"}

tables, columns, errs = set(), set(), []
for line in open(sys.argv[2], encoding="utf-8"):
    parts = line.rstrip("\n").split("\t")
    if parts[0] == "table":
        tables.add(parts[1])
    elif parts[0] == "json":
        columns.add((parts[1], parts[2]))
    elif parts[0] == "err":
        errs.append((parts[1], parts[2]))

inventory = {}
malformed = []
try:
    with open(inv_path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "::" not in line:
                malformed.append(line)
                continue
            key, rest = line.split("::", 1)
            inventory[key.strip()] = rest.strip().split()[0] if rest.strip() else ""
except FileNotFoundError:
    print("FAIL D5: missing contracts/data-model.jsonb-inventory.txt")
    print("     (guidelines/data-model.md §5 — classify every JSON/JSONB column)")
    sys.exit(1)

out = []
for rel, msg in errs:
    out.append(f"FAIL D5: cannot parse model file {rel}: {msg}")
for line in malformed:
    out.append(f"FAIL D5: malformed inventory line (want `table.column :: verdict`): {line}")
for table, col in sorted(columns):
    key = f"{table}.{col}"
    verdict = inventory.get(key)
    if verdict is None:
        out.append(f"FAIL D5: unclassified JSON/JSONB column {key} — add to the inventory "
                   f"with verdict keep-open|typed-out")
    elif verdict not in VERDICTS:
        out.append(f"FAIL D5: {key} has unknown verdict `{verdict}` (want keep-open|typed-out)")
for key in sorted(inventory):
    table = key.split(".", 1)[0]
    if table not in tables:
        out.append(f"FAIL D5: stale inventory entry {key} — table `{table}` has no mapped model")

print("\n".join(out))
sys.exit(1 if out else 0)
PY
  rc=$?
  rm -f "$rec"
  return $rc
}

lifecycle_inventory_check() { # dir -> findings, exit 1 on any
  local dir="$1" rec rc
  rec="$(mktemp)"
  model_records "$dir" > "$rec"
  python3 - "$dir" "$rec" <<'PY'
import sys

root = sys.argv[1]
inv_path = root + "/contracts/data-model.lifecycle.txt"
CLASSES = {"record", "operational", "transient"}

tables, errs = set(), []
for line in open(sys.argv[2], encoding="utf-8"):
    parts = line.rstrip("\n").split("\t")
    if parts[0] == "table":
        tables.add(parts[1])
    elif parts[0] == "err":
        errs.append((parts[1], parts[2]))

inventory = {}
malformed = []
try:
    with open(inv_path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "::" not in line:
                malformed.append(line)
                continue
            key, rest = line.split("::", 1)
            inventory[key.strip()] = rest.strip().split()[0] if rest.strip() else ""
except FileNotFoundError:
    print("FAIL D6: missing contracts/data-model.lifecycle.txt")
    print("     (guidelines/data-model.md §7 — classify every mapped table)")
    sys.exit(1)

out = []
for rel, msg in errs:
    out.append(f"FAIL D6: cannot parse model file {rel}: {msg}")
for line in malformed:
    out.append(f"FAIL D6: malformed inventory line (want `table :: class`): {line}")
for table in sorted(tables):
    cls = inventory.get(table)
    if cls is None:
        out.append(f"FAIL D6: unclassified table {table} — add to the lifecycle inventory "
                   f"with class record|operational|transient")
    elif cls not in CLASSES:
        out.append(f"FAIL D6: {table} has unknown lifecycle class `{cls}` "
                   f"(want record|operational|transient)")
for table in sorted(inventory):
    if table not in tables:
        out.append(f"FAIL D6: stale lifecycle entry {table} — no mapped model declares it")

print("\n".join(out))
sys.exit(1 if out else 0)
PY
  rc=$?
  rm -f "$rec"
  return $rc
}

# ---------------------------------------------------------------------------
# C-checks — each prints findings on stdout and returns non-zero when it
# found any (self-tests assert *which* gate fired, via the `FAIL Dn:` lines).
# ---------------------------------------------------------------------------

d1_naming_convention() { # dir -> findings
  local dir="$1" out
  out="$(python3 - "$dir" <<'PY'
import ast, os, sys

root = sys.argv[1]
base = os.path.join(root, "backend", "app")
KEYS = {"pk", "fk", "ck", "uq", "ix"}
declared, used = set(), False

for dirpath, dirnames, filenames in os.walk(base):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv", "venv")]
    for name in sorted(filenames):
        if not name.endswith(".py"):
            continue
        path = os.path.join(dirpath, name)
        with open(path, "rb") as fh:
            blob = fh.read()
        if b"naming_convention" not in blob and b"NAMING_CONVENTION" not in blob:
            continue
        try:
            tree = ast.parse(blob.decode("utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if KEYS & keys:
                    declared |= keys & KEYS
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "naming_convention":
                        used = True
                        if isinstance(kw.value, ast.Dict):
                            keys = {k.value for k in kw.value.keys
                                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                            declared |= keys & KEYS

missing = sorted(KEYS - declared)
if not used and not declared:
    print("FAIL D1: no naming_convention on the declarative metadata")
    print("     (guidelines/data-model.md §2 — declarative_base/MetaData carries")
    print("      the five-key convention; constraint names are short in models)")
elif missing:
    print("FAIL D1: naming_convention lacks keys: " + ", ".join(missing))
PY
)"
  if [ -n "$out" ]; then
    echo "$out"
    REPO_FAIL=1
    return 1
  fi
  return 0
}

d2_alembic_chain() { # dir -> findings
  local dir="$1" out
  out="$(python3 - "$dir" <<'PY'
import os, re, sys

root = sys.argv[1]
versions = os.path.join(root, "backend", "alembic", "versions")
ID_RE = re.compile(r"^\d{4}(_[a-z0-9_]+)?$")

if not os.path.isdir(versions):
    print("FAIL D2: no backend/alembic/versions — the data-model gate applies "
          "to schema-bearing repos")
    sys.exit(0)

rev_re = re.compile(r"^revision\s*(?::[^=]+)?=\s*[\"']([^\"']+)[\"']", re.M)
down_re = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*(None|[\"']([^\"']+)[\"'])", re.M)

revs = {}
out_lines = []
for name in sorted(os.listdir(versions)):
    if not name.endswith(".py") or name == "__pycache__":
        continue
    text = open(os.path.join(versions, name), encoding="utf-8", errors="replace").read()
    m = rev_re.search(text)
    if not m:
        out_lines.append(f"FAIL D2: {name} declares no revision id")
        continue
    rid = m.group(1)
    d = down_re.search(text)
    down = None
    if d and d.group(1) != "None":
        down = d.group(2)
    if rid in revs:
        out_lines.append(f"FAIL D2: duplicate revision id {rid}")
    revs[rid] = (down, name)

for rid, (down, name) in sorted(revs.items()):
    if not ID_RE.match(rid):
        out_lines.append(f"FAIL D2: revision id `{rid}` is not NNNN[_slug] "
                         f"(guidelines/data-model.md §4)")
    if down is not None and down not in revs:
        out_lines.append(f"FAIL D2: {name} ({rid}) has unresolvable down_revision `{down}`")

children = {}
for rid, (down, _name) in revs.items():
    if down is not None:
        children.setdefault(down, []).append(rid)
for parent, kids in sorted(children.items()):
    if len(kids) > 1:
        out_lines.append(f"FAIL D2: fork at {parent} — {', '.join(sorted(kids))} "
                         f"all chain onto it (one linear chain only)")
heads = sorted(set(revs) - set(children))
if len(revs) > 0 and len(heads) != 1:
    out_lines.append("FAIL D2: expected exactly one head, found "
                     + (", ".join(heads) if heads else "none"))

print("\n".join(out_lines))
PY
)"
  if [ -n "$out" ]; then
    echo "$out"
    REPO_FAIL=1
    return 1
  fi
  return 0
}

d3_drift_gate() { # dir -> findings
  local dir="$1"
  if ! grep -rqi "autogenerate" "$dir/backend/tests" 2>/dev/null; then
    echo "FAIL D3: no model-DB drift gate test (tests must assert the empty"
    echo "     autogenerate diff — guidelines/data-model.md §3)"
    REPO_FAIL=1
    return 1
  fi
  return 0
}

d4_enum_parity_gate() { # dir -> findings
  local dir="$1"
  if ! grep -rqi "enum" "$dir/backend/tests" 2>/dev/null \
     || ! grep -rqiE "check_constraint|pg_constraint|information_schema" "$dir/backend/tests" 2>/dev/null; then
    echo "FAIL D4: no enum/CHECK parity test (tests must assert Python enum"
    echo "     members match the mirrored DB CHECKs — guidelines/data-model.md §6)"
    REPO_FAIL=1
    return 1
  fi
  return 0
}

d7_legacy_naming() { # dir -> findings
  local dir="$1" found
  found="$(python3 - "$dir" "$PATTERNS" <<'PY'
import os, re, sys

root, vocab_path = sys.argv[1], sys.argv[2]

entries = []
for raw in open(vocab_path, encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    pattern, _, _witness = line.partition("::")
    entries.append(pattern.strip())
compiled = [re.compile(e) for e in entries]

pragma_re = re.compile(r"#\s*gate-allow:\s*(.+?)\s*$")
findings = []

# D7 owns HOST vocabulary. Binary/data payloads are not identifiers, and a
# byte-identical vendored copy is family-owned (its words are checked in the
# canonical repo, never here) — both are skipped rather than exempted by name.
BINARY_SUFFIXES = (
    ".gz", ".zip", ".tar", ".tgz", ".whl", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4",
    ".wav", ".ogg", ".opus", ".wasm", ".db", ".sqlite3", ".dump", ".parquet",
)
VENDORED_MARKERS = (
    "VENDORED FILE",
    "Canonical copy lives in the family standards",
)

for scan in ("backend/app", "frontend/src"):
    base = os.path.join(root, scan)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules")]
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in BINARY_SUFFIXES:
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    blob = fh.read()
                if b"\0" in blob:
                    continue
                text = blob.decode("utf-8", errors="replace")
            except OSError:
                continue
            head = text[:600]
            if any(marker in head for marker in VENDORED_MARKERS):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                allowed = set()
                m = pragma_re.search(line)
                if m:
                    allowed = {x.strip() for x in m.group(1).split(",")}
                for entry, rx in zip(entries, compiled):
                    if entry in allowed or "*" in allowed:
                        continue
                    if rx.search(line):
                        findings.append(
                            f"{os.path.relpath(path, root)}:{lineno}:{line.strip()}")
                        break

print("\n".join(findings))
PY
)"
  if [ -n "$found" ]; then
    echo "FAIL D7: legacy naming in code:"
    echo "$found" | head -10 | sed 's/^/    /'
    REPO_FAIL=1
    return 1
  fi
  return 0
}

check_repo() { # repo_dir mode -> sets REPO_FAIL=1 on findings (strict only)
  local dir="$1" mode="$2"
  REPO_FAIL=0

  if [ ! -d "$dir/backend/app" ]; then
    echo "FAIL D2: no backend/app — not a schema-bearing repo (fix the REPOS table)"
    REPO_FAIL=1
    return
  fi

  d1_naming_convention "$dir"
  d2_alembic_chain "$dir"
  d3_drift_gate "$dir"
  d4_enum_parity_gate "$dir"
  jsonb_inventory_check "$dir" || REPO_FAIL=1
  lifecycle_inventory_check "$dir" || REPO_FAIL=1
  d7_legacy_naming "$dir"

  if [ "$REPO_FAIL" = 1 ] && [ "$mode" = "transition" ]; then
    echo "NOTE: transition mode — findings reported, not failing (guidelines/data-model.md §10)"
    REPO_FAIL=0
  fi
}

# ---------------------------------------------------------------------------
# dispatch (thin main — the self-test sources this file for the in-process
# cases; running it executes main).
# ---------------------------------------------------------------------------
main() {
  local self_test=0 arg
  for arg in "$@"; do
    [ "$arg" = "--self-test" ] && self_test=1
  done
  if [ "$self_test" = 1 ]; then
    exec bash "$GATE_DIR/check-data-model.selftest.sh"
  fi

  if [ "$SELF" = 1 ]; then
    # Self mode: check the repo this vendored copy lives in (its root sits
    # one level up from the script) — cwd-independent by construction.
    local here mode
    here="$(cd "$GATE_DIR/.." && pwd)"
    mode="${MODE_OVERRIDE:-strict}"
    echo "== data model: $here (self, $mode)"
    check_repo "$here" "$mode"
    if [ "$REPO_FAIL" = 1 ]; then
      echo "RESULT: FAIL"
      return 1
    fi
    echo "RESULT: OK"
    return 0
  fi

  local fail_total=0 entry repo mode
  for entry in "${REPOS[@]}"; do
    repo="${entry%%:*}"
    mode="${entry#*:}"
    [ "$STRICT_ALL" = 1 ] && mode=strict
    [ -n "$MODE_OVERRIDE" ] && mode="$MODE_OVERRIDE"
    echo "== data model: $repo ($mode)"
    check_repo "$WORKSPACE/$repo" "$mode"
    [ "$REPO_FAIL" = 1 ] && fail_total=1
  done

  if [ "$fail_total" = 1 ]; then
    echo "RESULT: FAIL"
    return 1
  fi
  echo "RESULT: OK — all repos aligned"
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
  exit $?
fi
