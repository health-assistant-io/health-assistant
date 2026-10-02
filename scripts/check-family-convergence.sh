#!/usr/bin/env bash
# Family-convergence gate (plan 20 D9 / plan 23 D1–D4 / guidelines/reference-architecture.md).
#
# Enforces, per repo, the decided family architecture:
#   C1  tool config parity — `[tool.ruff*]` blocks byte-identical across the
#       family (mypy ratchets are the documented per-repo exception);
#       pytest carries the `contract` marker + testpaths
#   C2  prefixed-only env — no unprefixed/legacy env names in code or scripts
#   C3  migrations home — migration entry points only in `app/local.py`
#       (+ the declared, pragma'd restore site: reference-architecture §4)
#   C4  no legacy shims/markers (`ca-*` schemes, `LEGACY_*` maps,
#       CourseAssistant names, dual-emit vocabulary)
#   C5  packaging shape — root `pyproject.toml` + `uv.lock` + backend
#       `pyproject.toml`; no `requirements*.txt`
#   C6  contract gates — `pytest -m contract` wired in each repo's CI
#
# The marker/env vocabularies are DATA, not code (plan 23 D2): they live in
# scripts/gate-patterns/*.txt and this script builds its matchers from those
# files at runtime. This file therefore contains no marker literals and can
# never match its own patterns (the plan-20 F2 self-match defect).
#
# Matching is boundary-anchored (plan 23 D3): a bare name never matches
# inside a product-prefixed name (CAREER_/SA_/HA_), there are no blanket
# `grep -v` filters, and legitimate bare-name lines carry an explicit
# `# gate-allow: NAME` pragma that the matcher skips.
#
# Vendored copies in product repos must stay byte-identical to this file
# (sha-checked by scripts/verify-wiring.sh — the selftest and the
# gate-patterns/ data are vendored and checked with it).
#
# Usage:
#   ./check-family-convergence.sh             # family scan from the dev repo
#   ./check-family-convergence.sh --self      # self-check one repo from its root
#   ./check-family-convergence.sh --self-test # run the gate's own test suite
set -uo pipefail

GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_ROOT="$(cd "$GATE_DIR/.." && pwd)"
WORKSPACE="$(dirname "$DEV_ROOT")"
PATTERNS_DIR="$GATE_DIR/gate-patterns"

# Declared scan exclusions (plan 23 D2/D3) — the ONLY lines the scans skip:
#   * the gate's own artifacts, whose contents *define* the patterns (a
#     marker literal in them is structural, not a violation);
#   * generated/vendored noise.
# Every entry is a hiding-spot candidate: the self-test covers the
# gate-artifact exclusion (T1) and the marker-completeness loop (T2) proves
# declared ⇒ detected outside these paths. Never add product code here.
SCAN_EXCLUDES=(
  "__pycache__"
  "/venv/"
  "/.venv/"
  "node_modules"
  "alembic/versions"
  "scripts/check-family-convergence.sh"
  "scripts/check-family-convergence.selftest.sh"
  "scripts/gate-patterns/"
)

# repo (relative to workspace) : pyproject path (repo-relative)
REPOS=(
  "study-assistant:backend/pyproject.toml"
  "career-assistant:backend/pyproject.toml"
  "health-assistant/core:backend/pyproject.toml"
  "auth-kit:pyproject.toml"
)

CANON_RUFF=''   # first repo's normalized ruff block becomes canonical
REPO_FAIL=0

# ---------------------------------------------------------------------------
# vocabulary loading (D2: patterns are data)
# ---------------------------------------------------------------------------
# vocab_file FILE -- prints `pattern<TAB>witness` per entry (witness defaults
# to the pattern; `#` comments and blank lines ignored).
vocab_file() {
  python3 - "$1" <<'PY'
import sys
for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "::" in line:
        pattern, witness = line.split("::", 1)
    else:
        pattern, witness = line, line
    print(pattern.strip() + "\t" + witness.strip())
PY
}

# ---------------------------------------------------------------------------
# C-checks — each is a named function printing findings on stdout and
# returning non-zero when it found any (plan 23: self-tests assert *which*
# gate fired, via the `FAIL Cn:` lines kept from the original format).
# ---------------------------------------------------------------------------

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

c1_tool_config() {  # dir pyproject_rel -> findings
  local dir="$1" pyproject_rel="$2" block
  block="$(ruff_block "$dir/$pyproject_rel")"
  if [ "$CANON_RUFF" = "" ]; then
    CANON_RUFF="$block"
  elif [ "$block" != "$CANON_RUFF" ]; then
    echo "FAIL C1: [tool.ruff*] differs from the family block:"
    diff <(printf '%s\n' "$CANON_RUFF") <(printf '%s\n' "$block") | sed 's/^/    /' | head -12
    REPO_FAIL=1
    return 1
  fi
  if ! grep -q '"contract' "$dir/$pyproject_rel"; then
    echo "FAIL C1: pytest config lacks the contract marker"
    REPO_FAIL=1
    return 1
  fi
  return 0
}

# scan_matcher MODE FILE SCAN_DIR... -- the shared boundary-anchored,
# pragma-aware matcher (python). MODE is the C2 or C4 form set.
scan_matcher() {
  local vocab="$1" mode="$2" dir="$3"
  shift 3
  python3 - "$dir" "$vocab" "$mode" "${SCAN_EXCLUDES[@]}" -- "$@" <<'PY'
import os, re, sys

root, vocab_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]
sep = args.index("--")
excludes, scan_dirs = args[:sep], args[sep + 1:]

entries = []
for raw in open(vocab_path, encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    pattern, _, _witness = line.partition("::")
    entries.append(pattern.strip())

def forms(pattern):
    """Boundary-anchored match forms for one vocabulary entry."""
    if mode == "c2":
        # env names are literal; boundary-anchored so a bare name never
        # matches inside a product-prefixed name (CAREER_/SA_/HA_).
        n = re.escape(pattern)
        return [
            # env-file / shell assignment, YAML mapping, compose list item,
            # python call kwarg at line start: NAME=..., NAME: ..., - NAME=...
            re.compile(r"^\s*(?:-\s+)?(?:export\s+)?%s\s*[=:]" % n),
            # python call kwarg anywhere: Settings(NAME=...), f(NAME=...)
            re.compile(r"[(,]\s*%s\s*=" % n),
            # shell export by name: export NAME
            re.compile(r"\bexport\s+%s\b" % n),
            # env access calls with the name quoted
            re.compile(
                r"(?:os\.environ(?:\.get)?\(|os\.environ\[|environ\[|environ\.get\("
                r"|getenv\(|os\.getenv\(|setenv\()\s*[\"']%s[\"']" % n
            ),
        ]
    if mode == "c3":
        # migration entry surfaces: literal names matched in ANY reference
        # form (call, import or alias) — re-exported invocations cannot
        # hide from the gate. The sanctioned home is skipped below.
        n = re.escape(pattern)
        return [re.compile(r"(?:^|[^A-Za-z0-9_])%s\b" % n)]
    # c4: left-boundary marker match (entries are regexes, may be fragments)
    return [re.compile(r"(?:^|[^A-Za-z0-9_])%s" % pattern)]

# Pragma names mirror vocabulary entries verbatim (entry-in-allowed match),
# so the charset must cover anything a pattern file may name (dots, dashes,
# slashes) — otherwise an entry could not be declared at all.
pragma_re = re.compile(r"#\s*gate-allow:\s*([A-Za-z0-9_.*/-]+(?:\s*,\s*[A-Za-z0-9_.*/-]+)*)")
comment_only_re = re.compile(r"^\s*#")

# C3's sanctioned migrations home (guidelines/reference-architecture.md §4)
# — the ONE place app code may migrate. A path, not vocabulary data: it is
# the definition of "home", and only C3 skips it.
MIGRATIONS_HOME = "/app/local.py"

def pragma_names(line):
    m = pragma_re.search(line)
    if not m:
        return set()
    names = [x.strip() for x in m.group(1).split(",")]
    return set(names) | ({"*"} if "*" in names else set())

compiled = [(e, fs) for e in entries for fs in [forms(e)]]
findings = []

def excluded(path):
    return any(x in path for x in excludes)

for scan in scan_dirs:
    base = os.path.join(root, scan)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not excluded(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if excluded(path):
                continue
            if mode == "c3" and path.replace(os.sep, "/").endswith(MIGRATIONS_HOME):
                continue
            try:
                with open(path, "rb") as fh:
                    blob = fh.read()
                if b"\0" in blob:
                    continue  # binary, like grep -I
                text = blob.decode("utf-8", errors="replace")
            except OSError:
                continue
            prev_standalone = set()
            for lineno, line in enumerate(text.splitlines(), 1):
                # A pragma covers its own line; a pragma on a comment-only
                # line also covers the line below it (for surfaces where a
                # trailing comment is unsafe — e.g. Dockerfile ENV lines).
                allowed = pragma_names(line) | prev_standalone
                prev_pragma = pragma_names(line)
                prev_standalone = prev_pragma if comment_only_re.match(line) else set()
                for entry, fs in compiled:
                    if entry in allowed or "*" in allowed:
                        continue
                    if any(f.search(line) for f in fs):
                        findings.append(f"{path}:{lineno}:{line}")
                        break

print("\n".join(findings))
PY
}

c2_env_names() {  # dir -> findings
  local dir="$1" found
  found="$(scan_matcher "$PATTERNS_DIR/env-names.txt" c2 "$dir" backend scripts docker | head -10)"
  if [ -n "$found" ]; then
    echo "FAIL C2: unprefixed/legacy env names:"
    echo "$found" | sed 's/^/    /'
    REPO_FAIL=1
    return 1
  fi
  return 0
}

c3_migrations_home() {  # dir -> findings
  local dir="$1" found
  found="$(scan_matcher "$PATTERNS_DIR/migration-sites.txt" c3 "$dir" backend/app | head -8)"
  if [ -n "$found" ]; then
    echo "FAIL C3: migration entry points outside app/local.py:"
    echo "$found" | sed 's/^/    /'
    REPO_FAIL=1
    return 1
  fi
  return 0
}

c4_legacy_markers() {  # dir -> findings
  local dir="$1" found
  found="$(scan_matcher "$PATTERNS_DIR/legacy-markers.txt" c4 "$dir" backend/app backend/tests frontend/src scripts | head -8)"
  if [ -n "$found" ]; then
    echo "FAIL C4: legacy markers in code:"
    echo "$found" | sed 's/^/    /'
    REPO_FAIL=1
    return 1
  fi
  return 0
}

c5_packaging_shape() {  # dir -> findings
  local dir="$1" ok=0
  [ -f "$dir/pyproject.toml" ] || { echo "FAIL C5: missing root pyproject.toml"; REPO_FAIL=1; ok=1; }
  [ -f "$dir/uv.lock" ] || { echo "FAIL C5: missing uv.lock"; REPO_FAIL=1; ok=1; }
  if compgen -G "$dir/backend/requirements*.txt" > /dev/null; then
    echo "FAIL C5: requirements*.txt still present:"
    compgen -G "$dir/backend/requirements*.txt" | sed 's/^/    /'
    REPO_FAIL=1
    ok=1
  fi
  return $ok
}

c6_contract_gate() {  # dir -> findings
  local dir="$1"
  if ! grep -rqn -- "-m contract" "$dir/.github" "$dir/.gitea" 2>/dev/null; then
    echo "FAIL C6: no 'pytest -m contract' step in CI workflows"
    REPO_FAIL=1
    return 1
  fi
  return 0
}

check_repo() {  # repo_dir pyproject_rel -> sets REPO_FAIL=1 on findings
  local dir="$1" pyproject_rel="$2"
  REPO_FAIL=0

  if [ ! -f "$dir/$pyproject_rel" ]; then
    echo "FAIL C5: missing $pyproject_rel"
    REPO_FAIL=1
    return
  fi

  c1_tool_config "$dir" "$pyproject_rel"
  c2_env_names "$dir"
  c3_migrations_home "$dir"
  c4_legacy_markers "$dir"
  c5_packaging_shape "$dir"
  c6_contract_gate "$dir"
}

# ---------------------------------------------------------------------------
# dispatch (thin main — plan 23: the self-test sources this file for the
# in-process cases; running it executes main).
# ---------------------------------------------------------------------------
main() {
  local self=0 self_test=0 arg
  for arg in "$@"; do
    case "$arg" in
      --self) self=1 ;;
      --self-test) self_test=1 ;;
      *) echo "unknown arg: $arg (use --self | --self-test)" >&2; return 2 ;;
    esac
  done

  if [ "$self_test" = 1 ]; then
    exec bash "$GATE_DIR/check-family-convergence.selftest.sh"
  fi

  if [ "$self" = 1 ]; then
    # Self mode: check the repo we are standing in.
    local here rel
    here="$(basename "$(pwd)")"
    rel="backend/pyproject.toml"
    [ "$here" = "auth-kit" ] && rel="pyproject.toml"
    echo "== family convergence: $(pwd) (self)"
    check_repo "." "$rel"
    if [ "$REPO_FAIL" = 1 ]; then
      echo "RESULT: FAIL"
      return 1
    fi
    echo "RESULT: OK"
    return 0
  fi

  local fail_total=0 entry repo
  for entry in "${REPOS[@]}"; do
    repo="${entry%%:*}"
    echo "== family convergence: $repo"
    check_repo "$WORKSPACE/$repo" "${entry#*:}"
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
