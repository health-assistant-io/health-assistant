#!/usr/bin/env bash
# AI-architecture alignment gate (ADR-0008 / guidelines/ai-features.md).
#
# Enforces, per product repo (python and js/electron kinds):
#   R1  no provider-SDK imports (openai / anthropic / google-genai; decision
#       transports: @typesafe-ai/sdk js, typesafe_sdk python — plan 26 /
#       ADR-0032) outside the AI layer (python: backend/app/ai/
#       chat_models.py; js: src/main/ai/) and tests. The python
#       typesafe_sdk pattern activates when the plan 26 Phase 3/5 ports
#       adopt the SDK; it is in the sanctioned surface from the start so
#       no port ever ships with the SDK outside the AI layer.
#   R2  LangChain chat classes, the agent/graph stack (langchain,
#       @langchain/langgraph*), and MCP libraries (fastmcp / mcp /
#       @langchain/mcp-adapters) imported only inside the AI layer
#       (python: backend/app/ai/; js: src/main/ai/) (+ tests).
#       The mcp/fastmcp match covers nested module paths and plain
#       imports (from mcp.server.mcpserver import …, import mcp.client…)
#       — first-level-module matching alone let study ship an MCP server
#       import outside ai/ while the gate stayed green (plan 32 Phase 9
#       observation; tightened 2026-10-17)
#   R3  LangGraph graphs (StateGraph; js: also createAgent) live under the
#       ai/graphs/ dir (python: backend/app/ai/graphs; js: src/main/ai/graphs)
#   R4  langgraph (+ langgraph-checkpoint-* when persistent checkpointing
#       is used) exists as a dependency when graphs exist
#   R5  audio & model-catalog endpoint calls (audio/transcriptions,
#       audio/speech, /v1/models — ADR-0018) stay inside the AI layer
#       (python: backend/app/ai/; js: src/main/ai/) — these are raw
#       httpx/fetch surfaces invisible to R1/R2's import checks
#   R6  wire-parameter construction stays in the AI layer: the modern
#       token-cap parameter name (max_completion_tokens) appears only in
#       backend/app/ai/ (python) / src/main/ai/ (js) + tests (+ ops
#       scripts/ for the REST pings) — config storage spells max_tokens,
#       and a stray occurrence means a hand-rolled payload or rename
#       (ADR-0038)
#   R7  sampling temperature is omit-unless-configured: no numeric
#       temperature default may be manufactured in app code (the old
#       0.7/0.0 defaults turned every model row into a reasoning-model
#       breaker — ADR-0038 temperature addendum); unset = send nothing
#
# Modes: strict (findings fail) or transition (findings reported, exit 0).
# Repos with pre-existing R1–R5 surfaces run in transition until their
# migration lands (ai-features §10).
#
# Usage:
#   ./check-ai-alignment.sh                 # family scan (any cwd, any copy)
#   ./check-ai-alignment.sh --strict        # fail on every repo (post-migration)
#   ./check-ai-alignment.sh --self [--mode transition|strict]
#                                            # self-check one repo from its
#                                            # root (vendored copy in product
#                                            # CI — ADR-0003/0004 compliant)
#   ./check-ai-alignment.sh --self-test     # path-resolution regression suite
#
# Vendored copies in product repos must stay byte-identical to this file
# (sha-checked by scripts/verify-wiring.sh). The per-repo mode table below
# only applies to the family scan; --self defaults to strict.
set -uo pipefail

SELF_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
DEV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The family root (the directory holding the product repos) is NOT always
# one level above this copy: the dev canonical lives at <family>/dev, while
# the vendored copy in health-assistant/core lives at
# <family>/health-assistant/core. Guessing with dirname() put the scan at
# <family>/health-assistant/health-assistant, where every table entry missed
# and each repo printed "SKIP (no backend/app)" — a vacuous green from the
# very repo the gate was running in. Probe both depths and take the one that
# actually carries the repos (the --self-test covers both layouts).
family_root() {
  local cand n best="" best_n=0 entry
  for cand in "$(dirname "$DEV_ROOT")" "$(dirname "$(dirname "$DEV_ROOT")")"; do
    [ "$cand" = "$best" ] && continue
    n=0
    for entry in "${REPOS[@]}"; do
      [ -d "$cand/${entry%%:*}" ] && n=$((n + 1))
    done
    if [ "$n" -gt "$best_n" ]; then best="$cand"; best_n="$n"; fi
  done
  [ -n "$best" ] && { echo "$best"; return 0; }
  return 1
}

# repo (relative to workspace) : mode : tests dir (repo-relative) : kind
# All four repos run strict since plan 26 Phase 6 (career's grand port and
# health's decision port landed; both had been transition from the plan 10
# era — their own CIs already ran --self --mode strict, so the family scan
# table now matches).
REPOS=(
  "study-assistant:strict:backend/tests:python"
  "career-assistant:strict:backend/tests:python"
  "health-assistant/core:strict:backend/tests:python"
  "desktop-assistant:strict:tests:js"
)

WORKSPACE="$(family_root)" || WORKSPACE=""

STRICT_ALL=0; SELF=0; SELF_TEST=0; MODE_OVERRIDE=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT_ALL=1 ;;
    --self) SELF=1 ;;
    --self-test) SELF_TEST=1 ;;
    --mode) : ;;  # value consumed below
    transition|strict) [ "$MODE_OVERRIDE" = "" ] || MODE_OVERRIDE="$arg"; MODE_OVERRIDE="$arg" ;;
    *) echo "unknown arg: $arg (use --strict, --self, --self-test, --mode transition|strict)" >&2; exit 2 ;;
  esac
done

check_repo() {  # dir mode tests_dir kind -> sets REPO_FAIL=1 on strict findings
  local dir="$1" mode="$2" tests_dir="$3" kind="${4:-python}"
  REPO_FAIL=0

  local backend scan_root FACTORY GRAPHS_DIR GRAPH_RE
  local -a SCAN_ARGS SDK_RE CLASS_RE DEPS_FILES

  if [ "$kind" = "js" ]; then
    backend="$dir/src/main"
    scan_root="$backend"
    FACTORY="src/main/ai"
    GRAPHS_DIR="src/main/ai/graphs"
    GRAPH_RE='StateGraph|createAgent\b'
    if [ ! -d "$backend" ]; then
      echo "FAIL (no src/main — not an electron product)"; REPO_FAIL=1
      return
    fi
    SCAN_ARGS=(--include='*.ts' --include='*.js' --include='*.mts' -rIlnE
      --exclude-dir='node_modules' --exclude-dir='dist'
      --exclude-dir='build' --exclude-dir='release' --exclude-dir='generated' --exclude-dir='.git')
    SDK_RE="(from ['\"]openai['\"]|require\(['\"]openai['\"]\)|from ['\"]anthropic['\"]|from ['\"]@anthropic-ai/sdk['\"]|from ['\"]@typesafe-ai/sdk['\"]|from ['\"]@google/genai['\"])"
    CLASS_RE="(from ['\"]@langchain/(openai|anthropic|google-genai|google)['\"]|require\(['\"]@langchain/(openai|anthropic|google-genai|google)['\"]\)|from ['\"]@langchain/langgraph|from ['\"]@langchain/mcp-adapters['\"]|from ['\"]langchain(/agents)?['\"])";
    DEPS_FILES=("$dir/package.json")
  else
    backend="$dir/backend"
    scan_root="$backend"
    FACTORY="backend/app/ai/chat_models.py"
    GRAPHS_DIR="backend/app/ai/graphs"
    if [ ! -d "$backend/app" ]; then
      echo "FAIL (no backend/app — not a product backend)"; REPO_FAIL=1
      return
    fi
    SCAN_ARGS=(--include='*.py' -rIlnE
      --exclude-dir='.venv' --exclude-dir='venv' --exclude-dir='dist'
      --exclude-dir='build' --exclude-dir='__pycache__' --exclude-dir='.git')
    SDK_RE='(from openai[. ]|import openai$|from anthropic[. ]|import anthropic$|from google[.]genai|from google import genai|from google[.]generativeai|from typesafe_sdk[. ]|import typesafe_sdk$)'
    CLASS_RE='(from langchain_(openai|anthropic|google_genai)[. ]import|init_chat_model[[:space:]]*\(|from fastmcp([. ]|$)|from mcp[.](server|client|shared)([.]| import)|import mcp[.](server|client|shared))'
    GRAPH_RE='StateGraph'
    DEPS_FILES=("$backend/pyproject.toml")
  fi

  is_allowed() {  # path repo-relative -> ai layer, tests, or graphs dir
    case "$1" in
      "$FACTORY"|"$FACTORY"/*|"$tests_dir"/*|"$GRAPHS_DIR"/*) return 0 ;;
      # Python repos: the AI layer is the whole app/ai dir (the decision
      # funnel lives in app/ai/decide/ — plan 26 §3.7 sanctions the decision
      # SDK inside the AI layer, not the chat factory alone).
      "$AI_LAYER"|"$AI_LAYER"/*) return 0 ;;
      *) return 1 ;;
    esac
  }

  is_allowed_r2() {  # R2's AI layer is the whole app/ai dir (python), not just the factory
    case "$1" in
      "$AI_LAYER"|"$AI_LAYER"/*|"$tests_dir"/*|"$GRAPHS_DIR"/*) return 0 ;;
      *) return 1 ;;
    esac
  }
  if [ "$kind" = "python" ]; then
    AI_LAYER="backend/app/ai"
  else
    AI_LAYER="$FACTORY"
  fi

  local findings=0 first=1
  report() {  # rule, file, detail
    [ "$first" -eq 1 ] && { echo "findings:"; first=0; }
    findings=$((findings + 1))
    printf '    %s  %s: %s\n' "$1" "${2#"$dir"/}" "$3"
  }

  local f rel
  while IFS= read -r f; do
    rel="${f#"$dir"/}"
    is_allowed "$rel" || report R1 "$rel" 'provider SDK import outside factory'
  done < <(grep "${SCAN_ARGS[@]}" "$SDK_RE" "$backend" 2>/dev/null | sort -u)

  while IFS= read -r f; do
    rel="${f#"$dir"/}"
    is_allowed_r2 "$rel" || report R2 "$rel" 'LangChain chat class / MCP import outside the AI layer'
  done < <(grep "${SCAN_ARGS[@]}" "$CLASS_RE" "$backend" 2>/dev/null | sort -u)

  local graphs=0
  while IFS= read -r f; do
    graphs=1
    rel="${f#"$dir"/}"
    case "$rel" in "$GRAPHS_DIR"/*|"$tests_dir"/*) ;; *) report R3 "$rel" 'graph (StateGraph/createAgent) outside the ai/graphs/ dir' ;; esac
  done < <(grep "${SCAN_ARGS[@]}" "$GRAPH_RE" "$scan_root" 2>/dev/null | sort -u)

  # R5 (ADR-0018): audio/catalog REST surfaces are raw httpx/fetch, invisible
  # to the import checks — pin them to the AI layer (url fragments composed
  # with f-strings/templates, so no quote anchoring). Connection tests /
  # provider services belong in gateway-owned modules (ai-features §1/§14).
  while IFS= read -r f; do
    rel="${f#"$dir"/}"
    is_allowed_r2 "$rel" || report R5 "$rel" 'audio/catalog endpoint call outside the AI layer (ADR-0018)'
  done < <(grep "${SCAN_ARGS[@]}" 'audio/transcriptions|audio/speech|/v1/models|generativelanguage[.]googleapis[.]com' "$backend" 2>/dev/null | sort -u)

  # R6 (ADR-0038): max_completion_tokens is payload construction — it lives
  # in the AI layer (the vendored wire module / factory) and tests, plus
  # ops scripts/ for the REST pings. Config storage and API schemas spell
  # max_tokens; a stray occurrence is a hand-rolled payload or rename.
  if [ "$kind" = "python" ]; then
    r6_scan=("$backend/app"); r6_args=(--include='*.py' -rIlnE
      --exclude-dir='__pycache__' --exclude-dir='.git')
    r6_extra='scripts/*'
  else
    r6_scan=("$dir/src"); r6_args=(--include='*.ts' --include='*.js' --include='*.mts' -rIlnE
      --exclude-dir='node_modules' --exclude-dir='dist' --exclude-dir='build'
      --exclude-dir='release' --exclude-dir='generated' --exclude-dir='.git')
    r6_extra='src/shared/*'
  fi
  while IFS= read -r f; do
    rel="${f#"$dir"/}"
    case "$rel" in
      "$AI_LAYER"|"$AI_LAYER"/*|"$tests_dir"/*|$r6_extra) ;;
      *) report R6 "$rel" 'max_completion_tokens outside the AI layer (ADR-0038)' ;;
    esac
  done < <(grep "${r6_args[@]}" 'max_completion_tokens' "${r6_scan[@]}" 2>/dev/null | sort -u)

  # R7 (ADR-0038 temperature addendum): no manufactured temperature defaults
  # in app code — unset rides the provider default (the only value reasoning
  # families accept); a user-set value must be the only way a number rides
  # the wire. Tests are exempt (they construct fixtures explicitly).
  if [ "$kind" = "python" ]; then
    r7_scan=("$backend/app"); r7_args=(--include='*.py' -rIlnE
      --exclude-dir='__pycache__' --exclude-dir='.git')
  else
    r7_scan=("$dir/src"); r7_args=(--include='*.ts' --include='*.tsx' --include='*.js' -rIlnE
      --exclude-dir='node_modules' --exclude-dir='dist' --exclude-dir='build'
      --exclude-dir='release' --exclude-dir='generated' --exclude-dir='.git')
  fi
  while IFS= read -r f; do
    rel="${f#"$dir"/}"
    case "$rel" in "$tests_dir"/*) ;; *) report R7 "$rel" 'manufactured temperature default — omit unless configured (ADR-0038)' ;; esac
  done < <(grep "${r7_args[@]}" 'temperature[^A-Za-z0-9_]*[:=][[:space:]]*0\.' "${r7_scan[@]}" 2>/dev/null | sort -u)

  if [ "$graphs" -eq 1 ]; then
    grep -qh 'langgraph' "${DEPS_FILES[@]}" 2>/dev/null || report R4 'deps' 'graphs exist but no langgraph dependency'
    if [ "$kind" = "python" ] || grep -qhE 'langgraph-checkpoint|SqliteSaver|PostgresSaver' "$backend" 2>/dev/null; then
      grep -qhE 'langgraph-checkpoint' "${DEPS_FILES[@]}" 2>/dev/null || \
        report R4 'deps' 'graphs exist but no langgraph-checkpoint-* dependency'
    fi
  fi

  if [ "$findings" -eq 0 ]; then
    echo "OK"
  elif [ "$mode" = "strict" ] || [ "$STRICT_ALL" -eq 1 ]; then
    echo "FAIL ($mode, $findings finding(s))"; REPO_FAIL=1
  else
    echo "TRANSITION ($findings finding(s) — exempt until migration lands)"
  fi
}

# --self-test: path-resolution regression suite (no sibling repos needed).
# Covers the misresolution class twice over — the dev-canonical layout
# (<family>/dev) and the vendored layout (<family>/health-assistant/core) —
# and asserts the loud-failure paths that replaced the vacuous SKIP.
selftest_fixture_repo() {  # dir kind
  local d="$1" kind="${2:-python}"
  if [ "$kind" = "js" ]; then
    mkdir -p "$d/src/main/ai"
    printf 'export const MODEL = "mock";\n' > "$d/src/main/ai/factory.ts"
    printf 'export const BOOT = 1;\n' > "$d/src/main/index.ts"
  else
    mkdir -p "$d/backend/app/ai" "$d/backend/tests"
    printf 'VALUE = 1\n' > "$d/backend/app/main.py"
    printf 'def build_chat_model():\n    return None\n' > "$d/backend/app/ai/chat_models.py"
    printf 'def test_ok():\n    assert True\n' > "$d/backend/tests/test_ok.py"
  fi
}

selftest_family() {  # dir -> four product repos (the REPOS table's shapes)
  local fam="$1"
  selftest_fixture_repo "$fam/study-assistant"
  selftest_fixture_repo "$fam/career-assistant"
  selftest_fixture_repo "$fam/health-assistant/core"
  selftest_fixture_repo "$fam/desktop-assistant" js
  mkdir -p "$fam/dev/scripts" "$fam/health-assistant/core/scripts"
  cp "$SELF_PATH" "$fam/dev/scripts/check-ai-alignment.sh"
  cp "$SELF_PATH" "$fam/health-assistant/core/scripts/check-ai-alignment.sh"
}

run_selftest() {
  local work fam lone out rc pass=0 failed=0
  work="$(mktemp -d)"
  fam="$work/family"
  selftest_family "$fam"

  echo "== ai-alignment gate self-test =="

  case_ok() { pass=$((pass + 1)); echo "ok   $1"; }
  case_fail() { failed=$((failed + 1)); echo "FAIL $1"; echo "$2" | sed 's/^/     /'; }

  # 1. vendored copy (two levels below the family root): no-arg scans the
  #    repo it lives in instead of printing SKIP + a vacuous green.
  out="$(cd "$work" && bash "$fam/health-assistant/core/scripts/check-ai-alignment.sh" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ] && grep -qE '^health-assistant/core +OK$' <<<"$out" \
    && ! grep -q 'SKIP' <<<"$out" && [ "$(grep -cE 'OK$' <<<"$out")" -ge 4 ]; then
    case_ok "vendored copy no-arg resolves the family root and scans its own repo"
  else
    case_fail "vendored copy no-arg resolves the family root and scans its own repo" "rc=$rc
$out"
  fi

  # 2. dev-canonical copy (one level below the family root): same scan.
  out="$(cd "$work" && bash "$fam/dev/scripts/check-ai-alignment.sh" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ] && grep -qE '^health-assistant/core +OK$' <<<"$out" \
    && ! grep -q 'SKIP' <<<"$out" && ! grep -q 'health-assistant/health-assistant' <<<"$out"; then
    case_ok "dev-canonical copy no-arg resolves the family root"
  else
    case_fail "dev-canonical copy no-arg resolves the family root" "rc=$rc
$out"
  fi

  # 3. --self from a repo root still checks that repo (CI path).
  out="$(cd "$fam/health-assistant/core" && bash scripts/check-ai-alignment.sh --self 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ] && grep -q 'self (strict, python)  OK' <<<"$out"; then
    case_ok "--self from the repo root checks that repo"
  else
    case_fail "--self from the repo root checks that repo" "rc=$rc
$out"
  fi

  # 4. --self outside a repo fails loud (was: vacuous "no strict violations").
  out="$(cd "$work" && bash "$fam/dev/scripts/check-ai-alignment.sh" --self 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && grep -q 'not a repo root' <<<"$out" && ! grep -q 'no strict violations' <<<"$out"; then
    case_ok "--self outside a repo fails loud"
  else
    case_fail "--self outside a repo fails loud" "rc=$rc
$out"
  fi

  # 5. a repo named in the table but not checked out is a loud failure, not
  #    a skipped entry.
  rm -rf "$fam/desktop-assistant"
  out="$(cd "$work" && bash "$fam/dev/scripts/check-ai-alignment.sh" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && grep -q 'repo not checked out' <<<"$out"; then
    case_ok "missing family repo is a loud failure"
  else
    case_fail "missing family repo is a loud failure" "rc=$rc
$out"
  fi

  # 6. a lone copy with no siblings cannot run a family scan: loud failure.
  lone="$work/lone/core"
  selftest_fixture_repo "$lone"
  mkdir -p "$lone/scripts"
  cp "$SELF_PATH" "$lone/scripts/check-ai-alignment.sh"
  out="$(cd "$work" && bash "$lone/scripts/check-ai-alignment.sh" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && grep -q 'cannot locate the family root' <<<"$out" \
    && ! grep -q 'no strict violations' <<<"$out"; then
    case_ok "lone copy no-arg fails loud instead of skipping every repo"
  else
    case_fail "lone copy no-arg fails loud instead of skipping every repo" "rc=$rc
$out"
  fi

  rm -rf "$work"
  echo "--"
  echo "self-test: $pass passed, $failed failed"
  [ "$failed" -eq 0 ]
}

fail=0
if [ "$SELF_TEST" -eq 1 ]; then
  run_selftest || exit 1
  exit 0
elif [ "$SELF" -eq 1 ]; then
  mode="${MODE_OVERRIDE:-strict}"
  if [ -d "$(pwd)/src/main" ]; then
    kind=js; tests_dir=tests
  elif [ -d "$(pwd)/backend/app" ]; then
    kind=python; tests_dir=backend/tests
  else
    echo "FAIL (no backend/app or src/main under $(pwd) — not a repo root)" >&2
    echo "ai alignment: FAILURES above (see guidelines/ai-features.md, ADR-0008/0018)" >&2
    exit 1
  fi
  printf 'self (%s, %s)  ' "$mode" "$kind"
  check_repo "$(pwd)" "$mode" "$tests_dir" "$kind"
  [ "$REPO_FAIL" -eq 1 ] && fail=1
else
  if [ -z "$WORKSPACE" ]; then
    echo "ai alignment: cannot locate the family root above $DEV_ROOT" >&2
    echo "  (no sibling product repos found; run --self for a single repo)" >&2
    exit 2
  fi
  echo "family root: $WORKSPACE"
  for entry in "${REPOS[@]}"; do
    repo="${entry%%:*}"; rest="${entry#*:}"
    mode="${rest%%:*}"; tests_dir_kind="${rest#*:}"
    tests_dir="${tests_dir_kind%%:*}"; kind="${tests_dir_kind##*:}"
    [ -n "$MODE_OVERRIDE" ] && mode="$MODE_OVERRIDE"
    printf '%-28s ' "$repo"
    if [ ! -d "$WORKSPACE/$repo" ]; then
      echo "FAIL (repo not checked out at $WORKSPACE/$repo)"; fail=1
      continue
    fi
    check_repo "$WORKSPACE/$repo" "$mode" "$tests_dir" "$kind"
    [ "$REPO_FAIL" -eq 1 ] && fail=1
  done
fi

if [ "$fail" -eq 0 ]; then
  echo "ai alignment: no strict violations"
else
  echo "ai alignment: FAILURES above (see guidelines/ai-features.md, ADR-0008/0018)" >&2
  exit 1
fi
