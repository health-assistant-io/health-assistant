#!/usr/bin/env bash

# Health Assistant — installer self-test
#
# Exercises scripts/install.sh, scripts/update-docker.sh and
# scripts/bootstrap.sh against a fake `docker` (no daemon, no images, no
# network) and a real bound socket for port conflicts. Covers the release
# image pin, the version-skew guard, the port pre-flight, the health-wait
# failure reporting and the bootstrap hand-off.
#
# Usage:
#   bash scripts/install.selftest.sh          # run every case
#   bash scripts/install.selftest.sh -v       # also print each command's output
#
# Exit: 0 all cases pass, 1 at least one failed.

set -u

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(cd "$HERE/.." && pwd)"
PASS=0
FAIL=0
FAILED_CASES=""

WORK="$(mktemp -d)"
BG_PIDS=""
cleanup() {
    local pid
    for pid in $BG_PIDS; do kill "$pid" 2>/dev/null || true; done
    rm -rf "$WORK"
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
ok()  { PASS=$((PASS + 1)); printf "${GREEN}  ok${NC}   %s\n" "$1"; }
bad() { FAIL=$((FAIL + 1)); FAILED_CASES="${FAILED_CASES}${1}"$'\n'; printf "${RED}  FAIL${NC} %s\n" "$1"; }

# assert_contains LABEL HAYSTACK NEEDLE
assert_contains() {
    case "$2" in
        *"$3"*) ok "$1" ;;
        *) bad "$1 (missing: $3)"; show_output ;;
    esac
}
assert_not_contains() {
    case "$2" in
        *"$3"*) bad "$1 (unexpected: $3)"; show_output ;;
        *) ok "$1" ;;
    esac
}
assert_eq() {
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi
}
show_output() {
    printf '%s\n' "$CASE_OUT" | sed 's/^/       | /' | tail -n 25
}

# ── fake docker ─────────────────────────────────────────────────────────────
# Answers exactly what the scripts ask, from fixtures in $DOCKER_FIXTURES, and
# records every call (used to assert "nothing was pulled or started").
mkdir -p "$WORK/bin"
cat > "$WORK/bin/docker" <<'SHIM'
#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
fixtures = os.environ.get("DOCKER_FIXTURES", ".")

if os.environ.get("DOCKER_CALLS"):
    with open(os.environ["DOCKER_CALLS"], "a") as fh:
        fh.write(" ".join(args) + "\n")


def fx(name):
    path = os.path.join(fixtures, name)
    if os.path.exists(path):
        with open(path) as fh:
            return fh.read()
    return ""


if "config" in args and "--format" in args:
    sys.stdout.write(fx("compose.json"))
    sys.exit(0)

if args[:1] == ["info"]:
    sys.exit(0)

if "ps" in args and "--filter" in args:
    spec = args[args.index("--filter") + 1]
    if spec.startswith("publish="):
        port = spec.split("=", 1)[1]
        for line in fx("port_owners").splitlines():
            if "\t" not in line:
                continue
            owned, project = line.split("\t", 1)
            if owned == port:
                print(project)
        sys.exit(0)
    if spec.startswith("label="):
        sys.stdout.write(fx("ids"))
        sys.exit(0)

if "ps" in args:
    sys.stdout.write(fx("compose_ps"))
    sys.exit(0)

if "inspect" in args:
    fmt = args[args.index("-f") + 1] if "-f" in args else ""
    target = args[-1]
    meta = json.loads(fx("containers.json") or "{}").get(target)
    if meta is None:
        sys.exit(1)
    if "Health" in fmt:
        print(meta.get("health", "none"))
    elif "ExitCode" in fmt:
        print(meta.get("exit", 0))
    elif "State.Status" in fmt:
        print(meta.get("status", "running"))
    elif "Name" in fmt:
        print("/" + meta.get("name", target))
    else:
        print("")
    sys.exit(0)

if args[:1] == ["logs"]:
    print(fx("logs_" + args[-1].replace("/", "_")) or "fixture: service log line")
    sys.exit(0)

if args[:1] == ["volume"]:
    sys.exit(1)

sys.exit(0)
SHIM
chmod +x "$WORK/bin/docker"
export PATH="$WORK/bin:$PATH"

# ── sandbox ─────────────────────────────────────────────────────────────────
# A minimal tree with the real compose port expressions (so port_env_var can
# map a port back to its .env knob), a fake .env, and stubs where install.sh
# only looks for presence.
BUSY_PORT=47801
LITERAL_PORT=47802

fixtures_for() { # dir busy redis-port literal-port
    local DIR="$1" REDIS="$2" LITERAL="$3"
    mkdir -p "$DIR/fixtures"
    cat > "$DIR/fixtures/compose.json" <<JSON
{"name": "fixture", "services": {
  "redis": {"ports": [{"published": "$REDIS", "target": 6379}]},
  "nginx": {"ports": [{"published": "$LITERAL", "target": 80}]},
  "postgres": {"ports": [{"published": "47900", "target": 5432}]}
}}
JSON
    : > "$DIR/fixtures/compose_ps"
    : > "$DIR/fixtures/ids"
    : > "$DIR/fixtures/port_owners"
    printf '{}' > "$DIR/fixtures/containers.json"
}

new_sandbox() { # name version
    local DIR="$WORK/$1"
    mkdir -p "$DIR/backend/app/core" "$DIR/frontend" "$DIR/docker"
    printf 'class Settings:\n    VERSION: str = "%s"\n' "$2" > "$DIR/backend/app/core/config.py"
    cat > "$DIR/docker/docker-compose.standalone.yml" <<YAML
services:
  redis:
    image: redis
    ports:
      - "127.0.0.1:\${REDIS_PORT:-$BUSY_PORT}:6379"
  nginx:
    image: nginx
    ports:
      - "$LITERAL_PORT:80"
  postgres:
    image: postgres
    ports:
      - "127.0.0.1:\${POSTGRES_PORT:-47900}:5432"
YAML
    cat > "$DIR/.env" <<ENV
APP_URL=http://localhost
IMAGE_TAG=latest
REDIS_PORT=$BUSY_PORT
POSTGRES_PORT=47900
ENV
    fixtures_for "$DIR" "$BUSY_PORT" "$LITERAL_PORT"
    printf '%s' "$DIR"
}

run_docker_script() { # sandbox script args...
    local DIR="$1" SCRIPT="$2"; shift 2
    CASE_OUT="$(cd "$DIR" && env DOCKER_FIXTURES="$DIR/fixtures" \
        DOCKER_CALLS="$DIR/calls" HEALTH_WAIT_INTERVAL=1 \
        bash "$CORE/scripts/$SCRIPT" "$@" 2>&1)"
    RUN_STATUS=$?
    RUN_CALLS="$(cat "$DIR/calls" 2>/dev/null || true)"
    : > "$DIR/calls"
    [ "$VERBOSE" -eq 1 ] && printf '%s\n' "$CASE_OUT"
    return 0
}

# ── W1: bootstrap maps a release ref to its image tag ───────────────────────
bootstrap_fixture() { # dir ref-ish
    local ROOT="$WORK/tarball-$2"
    mkdir -p "$ROOT/health-assistant-release/scripts" "$ROOT/health-assistant-release/backend"
    cat > "$ROOT/health-assistant-release/scripts/install.sh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$STUB_ARGS"
printf '%s\n' "${IMAGE_TAG:-}" > "$STUB_ENV_TAG"
exit 0
STUB
    chmod +x "$ROOT/health-assistant-release/scripts/install.sh"
    tar -czf "$ROOT.tgz" -C "$ROOT" health-assistant-release
    printf '%s' "$ROOT.tgz"
}

mkdir -p "$WORK/bin-curl"
cat > "$WORK/bin-curl/curl" <<'SHIM'
#!/usr/bin/env bash
out=""
prev=""
for arg in "$@"; do
    [ "$prev" = "-o" ] && out="$arg"
    prev="$arg"
done
if [ -n "$out" ]; then cp "$CURL_FIXTURE" "$out"; fi
exit 0
SHIM
chmod +x "$WORK/bin-curl/curl"

run_bootstrap() { # args...
    CASE_OUT="$(PATH="$WORK/bin-curl:$PATH" STUB_ARGS="$WORK/stub-args" \
        STUB_ENV_TAG="$WORK/stub-tag" CURL_FIXTURE="${CURL_FIXTURE:-}" \
        bash "$CORE/scripts/bootstrap.sh" "$@" 2>&1)"
    RUN_STATUS=$?
    [ "$VERBOSE" -eq 1 ] && printf '%s\n' "$CASE_OUT"
    return 0
}

say "${YELLOW}bootstrap: image tag derived from the ref${NC}"
CURL_FIXTURE="$(bootstrap_fixture x v0.8.0)"
rm -f "$WORK/stub-args" "$WORK/stub-tag"
run_bootstrap --ref v0.8.0 --dir "$WORK/fresh-release"
assert_eq "release ref exits 0" "$RUN_STATUS" "0"
assert_contains "release ref forwards --image-tag 0.8.0" "$(cat "$WORK/stub-args" 2>/dev/null)" "--image-tag 0.8.0"

rm -f "$WORK/stub-args"
run_bootstrap --ref main --dir "$WORK/fresh-main"
assert_contains "main ref pins main" "$(cat "$WORK/stub-args" 2>/dev/null)" "--image-tag main"

rm -f "$WORK/stub-args"
run_bootstrap --ref deadbeef --dir "$WORK/fresh-sha"
assert_not_contains "unpublished ref never invents a tag" "$(cat "$WORK/stub-args" 2>/dev/null)" "--image-tag"
assert_contains "unpublished ref warns" "$CASE_OUT" "no published image tag"

say "${YELLOW}bootstrap: re-running against an existing install${NC}"
LEGACY="$WORK/legacy"
mkdir -p "$LEGACY/scripts" "$LEGACY/backend/app/core"
cp "$WORK/tarball-v0.8.0/health-assistant-release/scripts/install.sh" "$LEGACY/scripts/install.sh"
printf 'class Settings:\n    VERSION: str = "0.8.0"\n' > "$LEGACY/backend/app/core/config.py"
printf 'APP_URL=http://localhost\nIMAGE_TAG=latest\n' > "$LEGACY/.env"
rm -f "$WORK/stub-args" "$WORK/stub-tag"
CURL_FIXTURE="$(bootstrap_fixture y v0.8.0)"
run_bootstrap --ref v0.8.0 --dir "$LEGACY"
assert_eq "existing install re-runs its own installer" "$RUN_STATUS" "0"
assert_contains "existing install is announced" "$CASE_OUT" "Existing install found"
assert_contains "existing install is not re-downloaded" "$CASE_OUT" "nothing downloaded"
assert_contains "legacy .env gets the pin" "$(grep '^IMAGE_TAG=' "$LEGACY/.env" 2>/dev/null)" "IMAGE_TAG=0.8.0"
assert_eq "pin is exported to the legacy installer" "$(cat "$WORK/stub-tag" 2>/dev/null)" "0.8.0"

# A newer release on the site must not pin its tag onto an older installed
# tree: that would reintroduce the same skew in the other direction.
LEGACY_OLD="$WORK/legacy-older"
mkdir -p "$LEGACY_OLD/scripts" "$LEGACY_OLD/backend/app/core"
cp "$WORK/tarball-v0.8.0/health-assistant-release/scripts/install.sh" "$LEGACY_OLD/scripts/install.sh"
printf 'class Settings:\n    VERSION: str = "0.7.0"\n' > "$LEGACY_OLD/backend/app/core/config.py"
printf 'APP_URL=http://localhost\n' > "$LEGACY_OLD/.env"
rm -f "$WORK/stub-tag"
CURL_FIXTURE="$(bootstrap_fixture z v0.9.0)"
run_bootstrap --ref v0.9.0 --dir "$LEGACY_OLD"
assert_eq "existing tree keeps its OWN version" "$(grep '^IMAGE_TAG=' "$LEGACY_OLD/.env")" "IMAGE_TAG=0.7.0"
assert_eq "older tree exports its own tag" "$(cat "$WORK/stub-tag" 2>/dev/null)" "0.7.0"
assert_contains "newer release is reported, not forced" "$CASE_OUT" "latest release is v0.9.0"
assert_contains "update path is named" "$CASE_OUT" "update-docker.sh"

say "${YELLOW}bootstrap: no installer in a non-empty dir${NC}"
ALIEN="$WORK/alien"
mkdir -p "$ALIEN"
printf 'user data\n' > "$ALIEN/notes.txt"
run_bootstrap --ref v0.8.0 --dir "$ALIEN"
assert_eq "non-empty dir without installer exits 1" "$RUN_STATUS" "1"
assert_contains "message keeps the data safe" "$CASE_OUT" "holds no installer to re-run"
assert_eq "nothing was written" "$(ls -A "$ALIEN")" "notes.txt"

# ── W1: install.sh pins, and refuses a skewed pin ───────────────────────────
say "${YELLOW}install.sh: image pin${NC}"
SB="$(new_sandbox pin 0.9.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait --image-tag 0.9.0
assert_eq "pinned install exits 0" "$RUN_STATUS" "0"
assert_contains "pin is persisted in .env" "$(grep '^IMAGE_TAG=' "$SB/.env")" "IMAGE_TAG=0.9.0"
assert_contains "stack is started" "$RUN_CALLS" "up -d"
assert_contains "summary shows the pinned images" "$CASE_OUT" "Images:  0.9.0"

say "${YELLOW}install.sh: version-skew guard${NC}"
SB="$(new_sandbox skew 0.8.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait --image-tag 0.9.0
assert_eq "skewed pin exits 1" "$RUN_STATUS" "1"
assert_contains "skew names the tag" "$CASE_OUT" "IMAGE_TAG=0.9.0 does not match"
assert_contains "skew names the tree version" "$CASE_OUT" "VERSION=0.8.0"
assert_not_contains "skew aborts before pulling" "$RUN_CALLS" " pull"
assert_not_contains "skew aborts before starting" "$RUN_CALLS" " up -d"

SB="$(new_sandbox skew-main 0.8.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait --image-tag main
assert_eq "main images are allowed on a release tree" "$RUN_STATUS" "0"

SB="$(new_sandbox unpinned 0.8.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait
assert_eq "an unpinned release tree still starts" "$RUN_STATUS" "0"
assert_contains "an unpinned release tree is warned" "$CASE_OUT" "IMAGE_TAG is latest"
assert_contains "the warning names the fix" "$CASE_OUT" "--image-tag 0.8.0"

SB="$(new_sandbox unpinned-dev 0.8.0)"
mkdir -p "$SB/.git"
run_docker_script "$SB" install.sh --no-pull --no-wait
assert_not_contains "a dev checkout is not warned" "$CASE_OUT" "IMAGE_TAG is latest"

say "${YELLOW}update-docker.sh: moves a semver pin with the tree${NC}"
SB="$(new_sandbox update 0.9.0)"
sed -i 's/^IMAGE_TAG=latest$/IMAGE_TAG=0.8.0/' "$SB/.env"
run_docker_script "$SB" update-docker.sh --no-pull --no-wait
assert_eq "update exits 0" "$RUN_STATUS" "0"
assert_contains "pin moves to the tree version" "$(grep '^IMAGE_TAG=' "$SB/.env")" "IMAGE_TAG=0.9.0"
assert_contains "pin move is reported" "$CASE_OUT" "Image pin moved 0.8.0"

SB="$(new_sandbox update-main 0.9.0)"
sed -i 's/^IMAGE_TAG=latest$/IMAGE_TAG=main/' "$SB/.env"
run_docker_script "$SB" update-docker.sh --no-pull --no-wait
assert_contains "a branch pin is left alone" "$(grep '^IMAGE_TAG=' "$SB/.env")" "IMAGE_TAG=main"

# ── W2: port pre-flight ─────────────────────────────────────────────────────
start_listener() { # port
    python3 - "$1" <<'PY' &
import socket, sys, time
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", int(sys.argv[1])))
srv.listen(4)
time.sleep(60)
PY
    BG_PIDS="$BG_PIDS $!"
    local i=0
    while [ "$i" -lt 40 ]; do
        if python3 -c "import socket,sys
s=socket.socket(); s.settimeout(0.2)
sys.exit(0 if s.connect_ex(('127.0.0.1', int('$1'))) == 0 else 1)"; then
            return 0
        fi
        sleep 0.1
        i=$((i + 1))
    done
    return 1
}

stop_listeners() {
    local pid
    for pid in $BG_PIDS; do kill "$pid" 2>/dev/null || true; done
    BG_PIDS=""
    sleep 0.2
}

say "${YELLOW}install.sh: port pre-flight${NC}"
SB="$(new_sandbox ports-free 0.9.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait
assert_eq "free ports pass" "$RUN_STATUS" "0"
assert_contains "pre-flight reports success" "$CASE_OUT" "Port pre-flight: no conflicts"

start_listener "$BUSY_PORT" || bad "could not bind the busy test port"
SB="$(new_sandbox ports-busy 0.9.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait
assert_eq "busy port exits 1" "$RUN_STATUS" "1"
assert_contains "busy port names the .env knob" "$CASE_OUT" "REDIS_PORT"
assert_contains "busy port names the port" "$CASE_OUT" "$BUSY_PORT"
assert_not_contains "busy port aborts before pulling" "$RUN_CALLS" " pull"
assert_not_contains "busy port aborts before starting" "$RUN_CALLS" " up -d"

SB="$(new_sandbox ports-ours 0.9.0)"
printf '%s\tfixture\n' "$BUSY_PORT" > "$SB/fixtures/port_owners"
run_docker_script "$SB" install.sh --no-pull --no-wait
assert_eq "our own project's port is not a conflict" "$RUN_STATUS" "0"
stop_listeners

SB="$(new_sandbox ports-literal 0.9.0)"
python3 - "$SB/fixtures/compose.json" "$LITERAL_PORT" <<'PY'
import json, sys
path, port = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data["services"]["nginx"]["ports"][0]["published"] = port
json.dump(data, open(path, "w"))
PY
start_listener "$LITERAL_PORT" || bad "could not bind the literal test port"
run_docker_script "$SB" install.sh --no-pull --no-wait
assert_eq "literal busy port exits 1" "$RUN_STATUS" "1"
assert_contains "literal port points at the compose file" "$CASE_OUT" "literal in docker/docker-compose.standalone.yml"
stop_listeners

start_listener "$BUSY_PORT" || bad "could not bind the busy test port"
SB="$(new_sandbox ports-auto 0.9.0)"
run_docker_script "$SB" install.sh --no-pull --no-wait --auto-ports
assert_eq "--auto-ports exits 0" "$RUN_STATUS" "0"
assert_contains "--auto-ports reports the move" "$CASE_OUT" "written to .env"
AUTO_PORT="$(grep '^REDIS_PORT=' "$SB/.env" | cut -d= -f2)"
if [ -n "$AUTO_PORT" ] && [ "$AUTO_PORT" != "$BUSY_PORT" ]; then
    ok "--auto-ports picked a different port ($AUTO_PORT)"
else
    bad "--auto-ports kept the busy port"
fi
assert_contains "--auto-ports still starts the stack" "$RUN_CALLS" "up -d"
stop_listeners

# ── W3: health-wait reporting ───────────────────────────────────────────────

say "${YELLOW}wait_for_backend_healthy: failure reporting${NC}"
SB="$(new_sandbox wait-healthy 0.9.0)"
CASE_OUT="$(CORE="$CORE" DIR="$SB" bash -c '
    . "$CORE/scripts/lib-docker.sh"
    DOCKER_COMPOSE_CMD=docker
    cd "$DIR"
    export DOCKER_FIXTURES="$DIR/fixtures"
    printf "%s" "{\"health-assistant-backend\": {\"health\": \"healthy\", \"status\": \"running\", \"name\": \"health-assistant-backend\"}}" > "$DIR/fixtures/containers.json"
    wait_for_backend_healthy 5
' 2>&1)"
assert_eq "healthy backend returns 0" "$?" "0"

SB="$(new_sandbox wait-failed 0.9.0)"
BASH_WAIT="$(cat <<'EOS'
    . "$CORE/scripts/lib-docker.sh"
    DOCKER_COMPOSE_CMD=docker
    cd "$DIR"
    export DOCKER_FIXTURES="$DIR/fixtures"
    printf '%s' '{"health-assistant-redis": {"status": "exited", "exit": 1, "name": "health-assistant-redis"}}' > "$DIR/fixtures/containers.json"
    printf '%s\n' 'health-assistant-redis' > "$DIR/fixtures/ids"
    printf 'redis|exited|Exited (1)\n' > "$DIR/fixtures/compose_ps"
    wait_for_backend_healthy 10
EOS
)"
CASE_OUT="$(CORE="$CORE" DIR="$SB" bash -c "$BASH_WAIT" 2>&1)"
assert_eq "dead service fails fast" "$?" "1"
assert_contains "dead service is named" "$CASE_OUT" "Failed containers"
assert_contains "dead service logs are shown" "$CASE_OUT" "redis|exited"
assert_contains "next step is a re-run" "$CASE_OUT" "re-run this script"
assert_not_contains "no 180s wait hint on fail-fast" "$CASE_OUT" "Timed out"

SB="$(new_sandbox wait-migrate 0.9.0)"
BASH_WAIT="$(cat <<'EOS'
    . "$CORE/scripts/lib-docker.sh"
    DOCKER_COMPOSE_CMD=docker
    cd "$DIR"
    export DOCKER_FIXTURES="$DIR/fixtures"
    printf '%s' '{"health-assistant-migrate": {"status": "exited", "exit": 0, "name": "health-assistant-migrate"}, "health-assistant-backend": {"health": "starting", "status": "running", "name": "health-assistant-backend"}}' > "$DIR/fixtures/containers.json"
    printf '%s\n' 'health-assistant-migrate' > "$DIR/fixtures/ids"
    wait_for_backend_healthy 1
EOS
)"
CASE_OUT="$(CORE="$CORE" DIR="$SB" bash -c "$BASH_WAIT" 2>&1)"
assert_eq "slow backend times out" "$?" "1"
assert_contains "timeout shows the service table" "$CASE_OUT" "Compose services"
assert_contains "timeout offers next steps" "$CASE_OUT" "Next steps"
assert_not_contains "a successful one-shot is not a failure" "$CASE_OUT" "Failed containers"

# ── summary ─────────────────────────────────────────────────────────────────
say ""
if [ "$FAIL" -eq 0 ]; then
    printf "${GREEN}install self-test: %s passed${NC}\n" "$PASS"
    exit 0
fi
printf "${RED}install self-test: %s passed, %s FAILED${NC}\n" "$PASS" "$FAIL"
printf '%s' "$FAILED_CASES" | sed 's/^/  - /'
exit 1
