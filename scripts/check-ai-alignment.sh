#!/usr/bin/env bash
# AI-architecture alignment gate (ADR-0008 / guidelines/ai-features.md).
#
# Enforces, per product repo:
#   R1  no provider-SDK imports (openai / anthropic / google-genai) outside
#       the model factory (backend/app/ai/chat_models.py) and tests
#   R2  LangChain chat classes imported only inside the factory (+ tests)
#   R3  LangGraph graphs (StateGraph) live under backend/app/ai/graphs/
#   R4  langgraph + a langgraph-checkpoint-* dep exist when graphs exist
#
# Modes: strict (findings fail) or transition (findings reported, exit 0).
#
# Usage:
#   ./check-ai-alignment.sh                 # family scan from the dev repo
#   ./check-ai-alignment.sh --strict        # fail on every repo (post-migration)
#   ./check-ai-alignment.sh --self [--mode transition|strict]
#                                            # self-check one repo from its
#                                            # root (vendored copy in product
#                                            # CI — ADR-0003/0004 compliant)
#
# Vendored copies in product repos must stay byte-identical to this file
# (sha-checked by scripts/verify-wiring.sh). The per-repo mode table below
# only applies to the family scan; --self defaults to strict.
set -uo pipefail

DEV_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="$(dirname "$DEV_ROOT")"

# repo (relative to workspace) : mode : tests dir (repo-relative)
REPOS=(
  "study-assistant:strict:backend/tests"
  "career-assistant:transition:backend/tests"
  "health-assistant/core:transition:backend/tests"
)

FACTORY="backend/app/ai/chat_models.py"   # canonical factory path (allowlist target)
GRAPHS_DIR="backend/app/ai/graphs"

STRICT_ALL=0; SELF=0; MODE_OVERRIDE=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT_ALL=1 ;;
    --self) SELF=1 ;;
    --mode) : ;;  # value consumed below
    transition|strict) [ "$MODE_OVERRIDE" = "" ] || MODE_OVERRIDE="$arg"; MODE_OVERRIDE="$arg" ;;
    *) echo "unknown arg: $arg (use --strict, --self, --mode transition|strict)" >&2; exit 2 ;;
  esac
done

check_repo() {  # dir mode tests_dir -> sets REPO_FAIL=1 on strict findings
  local dir="$1" mode="$2" tests_dir="$3"
  local backend="$dir/backend"
  REPO_FAIL=0

  if [ ! -d "$backend/app" ]; then
    echo "SKIP (no backend/app — not a product backend)"
    return
  fi

  local SCAN_ARGS=(--include='*.py' -rIlnE
    --exclude-dir='.venv' --exclude-dir='venv' --exclude-dir='dist'
    --exclude-dir='build' --exclude-dir='__pycache__' --exclude-dir='.git')
  local SDK_RE='(from openai[. ]|import openai$|from anthropic[. ]|import anthropic$|from google[.]genai|from google import genai|from google[.]generativeai)'
  local CLASS_RE='(from langchain_(openai|anthropic|google_genai)[. ]import|init_chat_model[[:space:]]*\()'

  is_allowed() {  # path repo-relative -> factory, tests, or graphs dir
    case "$1" in
      "$FACTORY"|"$tests_dir"/*|"$GRAPHS_DIR"/*) return 0 ;;
      *) return 1 ;;
    esac
  }

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
    is_allowed "$rel" || report R2 "$rel" 'LangChain chat class import outside factory'
  done < <(grep "${SCAN_ARGS[@]}" "$CLASS_RE" "$backend" 2>/dev/null | sort -u)

  local graphs=0
  while IFS= read -r f; do
    graphs=1
    rel="${f#"$dir"/}"
    case "$rel" in "$GRAPHS_DIR"/*|"$tests_dir"/*) ;; *) report R3 "$rel" 'StateGraph outside app/ai/graphs/' ;; esac
  done < <(grep "${SCAN_ARGS[@]}" 'StateGraph' "$backend/app" 2>/dev/null | sort -u)

  if [ "$graphs" -eq 1 ]; then
    local deps="$backend/pyproject.toml $backend/requirements.txt $backend/requirements*.txt"
    grep -qh 'langgraph' $deps 2>/dev/null || report R4 'deps' 'graphs exist but no langgraph dependency'
    grep -qhE 'langgraph-checkpoint-(postgres|sqlite)' $deps 2>/dev/null || \
      report R4 'deps' 'graphs exist but no langgraph-checkpoint-* dependency'
  fi

  if [ "$findings" -eq 0 ]; then
    echo "OK"
  elif [ "$mode" = "strict" ] || [ "$STRICT_ALL" -eq 1 ]; then
    echo "FAIL ($mode, $findings finding(s))"; REPO_FAIL=1
  else
    echo "TRANSITION ($findings finding(s) — exempt until migration lands)"
  fi
}

fail=0
if [ "$SELF" -eq 1 ]; then
  mode="${MODE_OVERRIDE:-strict}"
  printf 'self (%s)  ' "$mode"
  check_repo "$(pwd)" "$mode" "backend/tests"
  [ "$REPO_FAIL" -eq 1 ] && fail=1
else
  for entry in "${REPOS[@]}"; do
    repo="${entry%%:*}"; rest="${entry#*:}"
    mode="${rest%%:*}"; tests_dir="${rest##*:}"
    [ -n "$MODE_OVERRIDE" ] && mode="$MODE_OVERRIDE"
    printf '%-28s ' "$repo"
    check_repo "$WORKSPACE/$repo" "$mode" "$tests_dir"
    [ "$REPO_FAIL" -eq 1 ] && fail=1
  done
fi

if [ "$fail" -eq 0 ]; then
  echo "ai alignment: no strict violations"
else
  echo "ai alignment: FAILURES above (see guidelines/ai-features.md, ADR-0008)" >&2
  exit 1
fi
