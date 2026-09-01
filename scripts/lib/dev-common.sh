#!/usr/bin/env bash
# dev-common.sh — shared helpers for the Neuronection assistant family dev scripts.
#
# Canonical copy lives in career-assistant/scripts/lib/ and is distributed to
# the sibling repos via career-assistant/scripts/sync-dev-lib.sh — edit the
# canonical copy only, never a synced copy.
#
# This file is SOURCED, never executed. All public helpers are prefixed `dc_`
# so they cannot collide with project-local names. It is written to work under
# `set -euo pipefail` and to respect NO_COLOR / non-TTY output.

# ---------------------------------------------------------------------------
# Guard: refuse to execute, allow double-source without side effects.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "dev-common.sh is a library; source it, do not execute it." >&2
  exit 1
fi
DC_SOURCED="${DC_SOURCED:-}"
if [[ -n "$DC_SOURCED" ]]; then
  return 0
fi
DC_SOURCED=1

# ---------------------------------------------------------------------------
# Output helpers (colors disabled on non-TTY and when NO_COLOR is set).
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  DC_GREEN='\033[0;32m'; DC_YELLOW='\033[1;33m'; DC_RED='\033[0;31m'; DC_BLUE='\033[0;34m'; DC_NC='\033[0m'
else
  DC_GREEN=''; DC_YELLOW=''; DC_RED=''; DC_BLUE=''; DC_NC=''
fi

dc_die() { printf "${DC_RED}error:${DC_NC} %s\n" "$*" >&2; exit 1; }
dc_error() { printf "${DC_RED}%s${DC_NC}\n" "$*" >&2; }
dc_warn() { printf "${DC_YELLOW}%s${DC_NC}\n" "$*"; }
dc_ok() { printf "${DC_GREEN}%s${DC_NC}\n" "$*"; }
dc_info() { printf "${DC_BLUE}%s${DC_NC}\n" "$*"; }
dc_step() { printf "${DC_GREEN}==>${DC_NC} %s\n" "$*"; }

# ---------------------------------------------------------------------------
# dc_help [script] — print the leading '#' comment block of a script as help
# text (strip one leading '# '). Lets each entrypoint keep its docs in its own
# header instead of duplicating them in a case branch.
# ---------------------------------------------------------------------------
dc_help() {
  local script="${1:-$0}"
  awk 'FNR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } NF { exit }' "$script"
  exit 0
}

# ---------------------------------------------------------------------------
# dc_require_cmd NAME [HINT] — fail with a clear message if a command is missing.
# ---------------------------------------------------------------------------
dc_require_cmd() {
  local name="$1" hint="${2:-}"
  if ! command -v "$name" >/dev/null 2>&1; then
    dc_error "Required command '$name' is not installed or not in PATH.${hint:+ $hint}"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# dc_load_env FILE — source a .env file with auto-export (`set -a`).
# Silently skips a missing file; fails loudly on a broken one.
# ---------------------------------------------------------------------------
dc_load_env() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  . "$file"
  set +a
}

# ---------------------------------------------------------------------------
# Ports. dc_port_in_use is meant for `if` conditions (returns status).
# ---------------------------------------------------------------------------
dc_port_in_use() {
  local port="$1"
  lsof -Pi ":$port" -sTCP:LISTEN -t >/dev/null 2>&1
}

# dc_port_holders PORT — print PIDs listening on PORT (fuser, ss fallback).
dc_port_holders() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser "${port}/tcp" 2>/dev/null || true
  else
    ss -tlnp 2>/dev/null | grep -E "[:.]${port}[[:space:]]" | grep -oP 'pid=\K[0-9]+' | sort -u || true
  fi
}

# dc_describe_pid PID — one-line human description of a process.
dc_describe_pid() {
  tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null || echo "pid $1"
}

# dc_kill_port PORT — stop whatever listens on PORT: TERM, wait up to 2s,
# then KILL. Safe to call when the port is already free.
dc_kill_port() {
  local port="$1" pids pid
  pids="$(dc_port_holders "$port")"
  [[ -z "$pids" ]] && return 0
  for pid in $pids; do
    dc_warn "stopping pid $pid on port $port: $(dc_describe_pid "$pid")"
    kill "$pid" 2>/dev/null || true
  done
  for _ in $(seq 1 20); do
    [[ -z "$(dc_port_holders "$port")" ]] && return 0
    sleep 0.1
  done
  for pid in $(dc_port_holders "$port"); do
    dc_warn "force killing pid $pid on port $port"
    kill -9 "$pid" 2>/dev/null || true
  done
}

# dc_pkill PATTERN — pkill -f that never fails the script (`|| true` built in).
dc_pkill() {
  pkill -f "$1" 2>/dev/null || true
}

# dc_pkill9 PATTERN — SIGKILL pass, for escalation after a SIGTERM pass.
dc_pkill9() {
  pkill -9 -f "$1" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Python venv bootstrap.
#   dc_venv_python VENV_DIR         — echo the venv's python path
#   dc_ensure_venv VENV_DIR REQS    — create VENV_DIR if missing/broken and
#                                     pip-install REQS when they are newer
#                                     than the stamp (venv/.dc-reqs.stamp),
#                                     so unchanged deps do not reinstall on
#                                     every start. REQS may be empty to skip
#                                     dependency installation.
# ---------------------------------------------------------------------------
dc_venv_python() {
  echo "$1/bin/python"
}

dc_ensure_venv() {
  local venv_dir="$1" reqs="${2:-}" venv_py stamp
  venv_py="$(dc_venv_python "$venv_dir")"
  if [[ -d "$venv_dir" ]]; then
    if ! "$venv_py" -c "import sys" >/dev/null 2>&1 || ! "$venv_dir/bin/pip" --version >/dev/null 2>&1; then
      dc_warn "virtual environment at $venv_dir appears broken; recreating it"
      rm -rf "$venv_dir"
    fi
  fi
  if [[ ! -x "$venv_py" ]]; then
    dc_require_cmd python3 "Install python3 (and python3-venv) first."
    dc_step "creating virtual environment in $venv_dir"
    python3 -m venv "$venv_dir" || dc_die "failed to create virtual environment at $venv_dir"
  fi
  [[ -z "$reqs" ]] && return 0
  stamp="$venv_dir/.dc-reqs.stamp"
  if [[ ! -f "$stamp" || "$reqs" -nt "$stamp" ]]; then
    dc_step "installing backend dependencies from $reqs"
    "$venv_py" -m pip install --disable-pip-version-check -q --upgrade pip
    "$venv_py" -m pip install --disable-pip-version-check -q -r "$reqs" \
      || dc_die "failed to install requirements from $reqs"
    touch "$stamp"
  fi
}

# ---------------------------------------------------------------------------
# Node frontend bootstrap.
#   dc_ensure_node_deps DIR PM [LOCKFILE] — install into DIR with package
#   manager PM (npm|pnpm) when node_modules is missing or the lockfile is
#   newer than the stamp (node_modules/.dc-deps.stamp). LOCKFILE defaults to
#   the PM's usual name; the install is skipped cleanly if node_modules exists
#   and no stamp trigger applies.
# ---------------------------------------------------------------------------
dc_ensure_node_deps() {
  local dir="$1" pm="$2" lock="${3:-}" lockfile stamp
  case "$pm" in
    npm) lockfile="${lock:-package-lock.json}" ;;
    pnpm) lockfile="${lock:-pnpm-lock.yaml}" ;;
    *) dc_die "dc_ensure_node_deps: unknown package manager '$pm' (use npm|pnpm)" ;;
  esac
  [[ -f "$dir/$lockfile" ]] || lockfile="$dir/package.json"
  [[ -f "$lockfile" ]] || lockfile=""
  if [[ ! -d "$dir/node_modules" ]]; then
    dc_require_cmd "$pm" "See the project README for setup instructions."
    dc_step "installing frontend dependencies in $dir ($pm)"
    if [[ "$pm" == "pnpm" ]]; then
      (cd "$dir" && pnpm install) || dc_die "pnpm install failed in $dir"
    else
      (cd "$dir" && npm install) || dc_die "npm install failed in $dir"
    fi
    [[ -n "$lockfile" ]] && touch "$dir/node_modules/.dc-deps.stamp"
    return 0
  fi
  stamp="$dir/node_modules/.dc-deps.stamp"
  if [[ -n "$lockfile" && ( ! -f "$stamp" || "$lockfile" -nt "$stamp" ) ]]; then
    dc_require_cmd "$pm" "See the project README for setup instructions."
    dc_step "frontend lockfile changed; reinstalling dependencies in $dir"
    if [[ "$pm" == "pnpm" ]]; then
      (cd "$dir" && pnpm install) || dc_die "pnpm install failed in $dir"
    else
      (cd "$dir" && npm install) || dc_die "npm install failed in $dir"
    fi
    touch "$stamp"
  fi
}

# ---------------------------------------------------------------------------
# dc_check_port_free PORT NAME — hard-fail when PORT is already listening.
# dc_check_port_warn PORT NAME — warn only (for services that can move ports).
# ---------------------------------------------------------------------------
dc_check_port_free() {
  local port="$1" name="$2"
  if dc_port_in_use "$port"; then
    dc_error "Port $port ($name) is already in use:"
    local pid
    for pid in $(dc_port_holders "$port"); do
      dc_error "  pid $pid: $(dc_describe_pid "$pid")"
    done
    dc_error "Free it (e.g. ./scripts/run-dev.sh --force-stop or --force) and retry."
    exit 1
  fi
}

dc_check_port_warn() {
  local port="$1" name="$2"
  if dc_port_in_use "$port"; then
    dc_warn "Port $port ($name) is already in use; the matching service may fail to start."
  fi
}

# ---------------------------------------------------------------------------
# dc_exec_honcho PROCFILE [extra honcho args...] — verify honcho is importable
# and replace the current process with `honcho start -f PROCFILE` so signals
# (Ctrl+C) propagate to every child. Expects to run from the Procfile's dir.
# ---------------------------------------------------------------------------
dc_exec_honcho() {
  local procfile="$1"
  shift
  if ! command -v honcho >/dev/null 2>&1; then
    dc_die "honcho is not installed. Install it (e.g. 'pip install honcho') and retry."
  fi
  exec honcho start -f "$procfile" "$@"
}
