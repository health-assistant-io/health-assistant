#!/usr/bin/env bash

# Health Assistant — upgrade pre-flight self-test
#
# Exercises scripts/upgrade-check.sh's .env classification without Docker:
# the dangerous retired names block, harmless ones only warn, and a correct
# .env passes. Complements install.selftest.sh (which covers the install and
# update scripts themselves).
#
# Usage: bash scripts/upgrade-check.selftest.sh [-v]
# Exit:  0 all cases pass, 1 at least one failed.

set -u

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(cd "$HERE/.." && pwd)"
PASS=0
FAIL=0
FAILED_CASES=""

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$WORK/backend/alembic/versions" "$WORK/frontend"

ok() { PASS=$((PASS + 1)); printf "${GREEN}  ok${NC}   %s\n" "$1"; }
bad() { FAIL=$((FAIL + 1)); FAILED_CASES="${FAILED_CASES}${1}"$'\n'; printf "${RED}  FAIL${NC} %s\n" "$1"; }

write_env() { printf '%s\n' "$@" > "$WORK/.env"; }

run_check() {
    CASE_OUT="$(cd "$WORK" && bash "$CORE/scripts/upgrade-check.sh" --env-only 2>&1)"
    CASE_RC=$?
    if [ "$VERBOSE" = "1" ]; then
        printf '%s\n' "$CASE_OUT" | sed 's/^/       | /'
    fi
}

assert_rc() {
    if [ "$CASE_RC" = "$2" ]; then ok "$1"; else bad "$1 (want exit $2, got $CASE_RC)"; fi
}
assert_contains() {
    case "$CASE_OUT" in
        *"$2"*) ok "$1" ;;
        *) bad "$1 (missing: $2)" ;;
    esac
}
assert_not_contains() {
    case "$CASE_OUT" in
        *"$2"*) bad "$1 (unexpected: $2)" ;;
        *) ok "$1" ;;
    esac
}

echo "Upgrade pre-flight self-test"

# 1. A production APP_ENV with no HA_APP_ENV is the silent-downgrade class.
write_env 'APP_ENV=production'
run_check
assert_rc "APP_ENV=production with no HA_APP_ENV blocks" 1
assert_contains "APP_ENV refusal names HA_APP_ENV" "HA_APP_ENV"

# 2. The same value under the new name is fine (warning only).
write_env 'APP_ENV=production' 'HA_APP_ENV=production' \
    'HA_SESSION_KEY=session-key' 'HA_REFRESH_KEY=refresh-key' 'HA_DATA_KEY=data-key'
run_check
assert_rc "renamed APP_ENV with HA_APP_ENV set passes" 0
assert_contains "the stale line is still reported as a warning" "[warn]"

# 3. A dev APP_ENV is harmless.
write_env 'APP_ENV=development'
run_check
assert_rc "APP_ENV=development does not block" 0

# 4. The sealing key without its replacement is a blocker.
write_env 'INTEGRATION_SECRET_KEY=abc123'
run_check
assert_rc "INTEGRATION_SECRET_KEY without HA_DATA_KEY blocks" 1
assert_contains "the sealing-key refusal names HA_DATA_KEY" "HA_DATA_KEY"

# 5. With HA_DATA_KEY present the old name is only a warning.
write_env 'INTEGRATION_SECRET_KEY=abc123' 'HA_DATA_KEY=data-key'
run_check
assert_rc "INTEGRATION_SECRET_KEY with HA_DATA_KEY passes" 0
assert_contains "the old sealing key is reported as a warning" "[warn]"

# 6. A retired SECRET_KEY with no key pair is a blocker.
write_env 'SECRET_KEY=old-value'
run_check
assert_rc "SECRET_KEY without HA_SESSION_KEY/HA_REFRESH_KEY blocks" 1
assert_contains "the SECRET_KEY refusal names the new pair" "HA_SESSION_KEY"

# 7. Harmless infrastructure renames only warn.
write_env 'DATABASE_URL=postgresql://x' 'UPLOAD_DIR=/tmp/uploads'
run_check
assert_rc "harmless retired names do not block" 0
assert_contains "DATABASE_URL warns" "HA_DATABASE_URL"
assert_contains "UPLOAD_DIR warns" "HA_UPLOAD_DIR"

# 8. A clean environment passes with no findings.
write_env 'HA_APP_ENV=production' 'HA_SESSION_KEY=session-key' \
    'HA_REFRESH_KEY=refresh-key' 'HA_DATA_KEY=data-key'
run_check
assert_rc "a correct .env passes" 0
assert_contains "the clean case says so" "No upgrade blockers"
assert_not_contains "the clean case has no warnings" "[warn]"

echo
if [ "$FAIL" -gt 0 ]; then
    printf "${RED}upgrade-check self-test: %d failed${NC}\n" "$FAIL"
    printf '%s' "$FAILED_CASES"
    exit 1
fi
printf "${GREEN}upgrade-check self-test: %d passed${NC}\n" "$PASS"
exit 0
