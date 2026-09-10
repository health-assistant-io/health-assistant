#!/usr/bin/env bash
# AI-architecture alignment gate (ADR-0008 / guidelines/ai-features.md).
#
# Enforces, per product repo (python and js/electron kinds):
#   R1  no provider-SDK imports (openai / anthropic / google-genai) outside
#       the AI layer (python: backend/app/ai/chat_models.py; js: src/main/ai/)
#       and tests
#   R2  LangChain chat classes, the agent/graph stack (langchain,
#       @langchain/langgraph*), and MCP libraries (fastmcp / mcp /
#       @langchain/mcp-adapters) imported only inside the AI layer
#       (python: backend/app/ai/; js: src/main/ai/) (+ tests)
#   R3  LangGraph graphs (StateGraph; js: also createAgent) live under the
#       ai/graphs/ dir (python: backend/app/ai/graphs; js: src/main/ai/graphs)
#   R4  langgraph (+ langgraph-checkpoint-* when persistent checkpointing
#       is used) exists as a dependency when graphs exist
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

# repo (relative to workspace) : mode : tests dir (repo-relative) : kind
REPOS=(
  "study-assistant:strict:backend/tests:python"
  "career-assistant:transition:backend/tests:python"
  "health-assistant/core:transition:backend/tests:python"
  "desktop-assistant:strict:tests:js"
)

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
      echo "SKIP (no src/main — not an electron product)"
      return
    fi
    SCAN_ARGS=(--include='*.ts' --include='*.js' --include='*.mts' -rIlnE
      --exclude-dir='node_modules' --exclude-dir='dist'
      --exclude-dir='build' --exclude-dir='release' --exclude-dir='generated' --exclude-dir='.git')
    SDK_RE="(from ['\"]openai['\"]|require\(['\"]openai['\"]\)|from ['\"]anthropic['\"]|from ['\"]@anthropic-ai/sdk['\"]|from ['\"]@google/genai['\"])"
    CLASS_RE="(from ['\"]@langchain/(openai|anthropic|google-genai)['\"]|require\(['\"]@langchain/(openai|anthropic|google-genai)['\"]\)|from ['\"]@langchain/langgraph|from ['\"]@langchain/mcp-adapters['\"]|from ['\"]langchain(/agents)?['\"])";
    DEPS_FILES=("$dir/package.json")
  else
    backend="$dir/backend"
    scan_root="$backend"
    FACTORY="backend/app/ai/chat_models.py"
    GRAPHS_DIR="backend/app/ai/graphs"
    if [ ! -d "$backend/app" ]; then
      echo "SKIP (no backend/app — not a product backend)"
      return
    fi
    SCAN_ARGS=(--include='*.py' -rIlnE
      --exclude-dir='.venv' --exclude-dir='venv' --exclude-dir='dist'
      --exclude-dir='build' --exclude-dir='__pycache__' --exclude-dir='.git')
    SDK_RE='(from openai[. ]|import openai$|from anthropic[. ]|import anthropic$|from google[.]genai|from google import genai|from google[.]generativeai)'
    CLASS_RE='(from langchain_(openai|anthropic|google_genai)[. ]import|init_chat_model[[:space:]]*\(|from fastmcp[. ]import|from mcp[.](server|client|shared)[. ]import)'
    GRAPH_RE='StateGraph'
    DEPS_FILES=("$backend/pyproject.toml" "$backend/requirements.txt" "$backend/requirements"*.txt)
  fi

  is_allowed() {  # path repo-relative -> ai layer, tests, or graphs dir
    case "$1" in
      "$FACTORY"|"$FACTORY"/*|"$tests_dir"/*|"$GRAPHS_DIR"/*) return 0 ;;
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

fail=0
if [ "$SELF" -eq 1 ]; then
  mode="${MODE_OVERRIDE:-strict}"
  if [ -d "$(pwd)/src/main" ]; then
    kind=js; tests_dir=tests
  else
    kind=python; tests_dir=backend/tests
  fi
  printf 'self (%s, %s)  ' "$mode" "$kind"
  check_repo "$(pwd)" "$mode" "$tests_dir" "$kind"
  [ "$REPO_FAIL" -eq 1 ] && fail=1
else
  for entry in "${REPOS[@]}"; do
    repo="${entry%%:*}"; rest="${entry#*:}"
    mode="${rest%%:*}"; tests_dir_kind="${rest#*:}"
    tests_dir="${tests_dir_kind%%:*}"; kind="${tests_dir_kind##*:}"
    [ -n "$MODE_OVERRIDE" ] && mode="$MODE_OVERRIDE"
    printf '%-28s ' "$repo"
    check_repo "$WORKSPACE/$repo" "$mode" "$tests_dir" "$kind"
    [ "$REPO_FAIL" -eq 1 ] && fail=1
  done
fi

if [ "$fail" -eq 0 ]; then
  echo "ai alignment: no strict violations"
else
  echo "ai alignment: FAILURES above (see guidelines/ai-features.md, ADR-0008)" >&2
  exit 1
fi
