#!/usr/bin/env bash

# Health Assistant — installer self-test (the family installer contract wall)
#
# Exercises the bundle installer (scripts/install.sh), the updater
# (scripts/update.sh + the update-docker.sh alias), the bundle builder
# (scripts/build-deploy-bundle.sh) and the generated bootstrap
# (scripts/bootstrap.sh — the bundle flow, fail-closed) against a fake
# `docker`/`curl` (no daemon, no images, no network) plus a real bound socket
# for the port-conflict cases. Covers the contract flags, the per-slot image
# pins (manifest / --image KEY=REF / --image-tag), the :latest refusal, the
# image assert, the port pre-flight (exit 4), --auto-ports, --reset, the
# /health + /ready waits, the setup-token rules (printed once, --no-token,
# .env 0600) and the source-free manifest v2 bundle shape.
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

command -v python3 >/dev/null 2>&1 || { echo "python3 is required for the selftest" >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "openssl is required for the selftest (VAPID key generation)" >&2; exit 1; }

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

assert_contains() { # LABEL HAYSTACK NEEDLE
    case "$2" in
        *"$3"*) ok "$1" ;;
        *) bad "$1 (missing: $3)"; printf '%s\n' "$2" | sed 's/^/       | /' | tail -n 20 ;;
    esac
}
assert_not_contains() { # LABEL HAYSTACK NEEDLE
    case "$2" in
        *"$3"*) bad "$1 (unexpected: $3)"; printf '%s\n' "$2" | sed 's/^/       | /' | tail -n 20 ;;
        *) ok "$1" ;;
    esac
}
assert_eq() {
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi
}

# ── fake docker ─────────────────────────────────────────────────────────────
# Answers exactly what the scripts ask, from fixtures in $DOCKER_FIXTURES,
# and records every call (used to assert "nothing was pulled or started").
mkdir -p "$WORK/bin"
cat > "$WORK/bin/docker" <<'SHIM'
#!/usr/bin/env python3
import os, sys

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

if args[:1] == ["info"]:
    sys.exit(0)

if "version" in args and "compose" in args and "config" not in args:
    sys.stdout.write("Docker Compose version v2.31.0\n")
    sys.exit(0)

if "config" in args and "--format" in args:
    sys.stdout.write(fx("compose.json"))
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

if "exec" in args and "psql" in args:
    sql = args[-1]
    if sql.strip() == "select 1":
        print("1")
        sys.exit(0)
    # pg_database / pg_roles lookups (and any DDL): no legacy names exist
    # on the fixture host, so every lookup answers "not found".
    sys.exit(0)

if "pull" in args and os.environ.get("PULL_MODE") == "fail":
    sys.stderr.write("fixture: pull failed\n")
    sys.exit(1)

if "inspect" in args and "-f" in args:
    target = args[-1]
    import json
    try:
        meta = json.loads(fx("containers.json") or "{}").get(target, {})
    except Exception:
        meta = {}
    fmt = args[args.index("-f") + 1]
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

if args[:1] == ["volume"]:
    if "rm" in args:
        sys.exit(0)
    if os.environ.get("DOCKER_VOLUME_EXISTS") == "1":
        sys.exit(0)
    sys.exit(1)

if args[:1] == ["image"]:
    sys.exit(0)

if args[:1] == ["logs"]:
    print(fx("logs_" + args[-1].replace("/", "_")) or "fixture: service log line")
    sys.exit(0)

sys.exit(0)
SHIM
chmod +x "$WORK/bin/docker"

# ── fake curl (ready wait + the bootstrap's fetches) ────────────────────────
# Serves $CURL_FIXTURES by URL basename, honors -o and prints the URL back
# (as -w '%{url_effective}'). /health and /ready probes answer inline (the
# app endpoints); every other URL needs a fixture file. CURL_MODE=fail →
# every request fails.
mkdir -p "$WORK/bin-curl"
cat > "$WORK/bin-curl/curl" <<'SHIM'
#!/usr/bin/env bash
if [ "${CURL_MODE:-ok}" = "fail" ]; then
    exit 1
fi
out=""; url=""; prev=""
for arg in "$@"; do
    [ "$prev" = "-o" ] && out="$arg"
    case "$arg" in
        http*) url="$arg" ;;
    esac
    prev="$arg"
done
base="${url##*/}"
case "$url" in
    *api.github.com*) base="api.json" ;;
    */health|*/ready)
        printf '{"status": "ready"}\n'
        exit 0
        ;;
esac
src="${CURL_FIXTURES:-.}/$base"
[ -f "$src" ] || exit 1
[ -n "$out" ] && cp "$src" "$out"
printf '%s' "$url"
exit 0
SHIM
chmod +x "$WORK/bin-curl/curl"

# ── fake id (root refusal) ──────────────────────────────────────────────────
mkdir -p "$WORK/bin-root"
printf '#!/usr/bin/env bash\necho 0\n' > "$WORK/bin-root/id"
chmod +x "$WORK/bin-root/id"

export PATH="$WORK/bin-curl:$WORK/bin:$PATH"

# ── pins and ports ───────────────────────────────────────────────────────────
A64="$(printf 'a%.0s' $(seq 1 64))"
B64="$(printf 'b%.0s' $(seq 1 64))"
C64="$(printf 'c%.0s' $(seq 1 64))"
D64="$(printf 'd%.0s' $(seq 1 64))"
BACKEND_REPO="ghcr.io/health-assistant-io/health-assistant/health-assistant-backend"
FRONTEND_REPO="ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend"
BACKEND_PIN="${BACKEND_REPO}:0.10.3@sha256:${A64}"
FRONTEND_PIN="${FRONTEND_REPO}:0.10.3@sha256:${B64}"
OLD_BACKEND_PIN="${BACKEND_REPO}:0.10.2@sha256:${C64}"
OLD_FRONTEND_PIN="${FRONTEND_REPO}:0.10.2@sha256:${D64}"

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }
FREE_PORT="$(free_port)"
BUSY_PORT="$(free_port)"
PG_PORT="$(free_port)"

# ── sandbox: a minimal extracted-bundle tree ────────────────────────────────
# The REAL scripts run against it (cwd = the sandbox): installer.json and
# docker/.env.example are the repo's real files, manifest.json the release's.
new_sandbox() { # name published-port [backend-pin frontend-pin]
    local DIR="$WORK/$1" PORT="$2" BIMG="${3:-$BACKEND_PIN}" FIMG="${4:-$FRONTEND_PIN}"
    mkdir -p "$DIR/docker" "$DIR/scripts" "$DIR/fixtures"
    cp "$CORE/scripts/install.sh" "$CORE/scripts/update.sh" \
       "$CORE/scripts/update-docker.sh" "$CORE/scripts/lib-docker.sh" \
       "$CORE/scripts/upgrade-check.sh" "$DIR/scripts/"
    cp "$CORE/installer.json" "$DIR/installer.json"
    cp "$CORE/.env.example" "$DIR/docker/.env.example"
    cat > "$DIR/docker/docker-compose.standalone.yml" <<YAML
services:
  nginx:
    image: nginx:1.30.6-alpine
    ports:
      - "\${HTTP_PORT:-$PORT}:80"
  postgres:
    image: timescale/timescaledb:2.32.0-pg16
    ports:
      - "127.0.0.1:\${POSTGRES_PORT:-$PG_PORT}:5432"
  migrate: {image: "$BIMG"}
  backend: {image: "$BIMG"}
  worker: {image: "$BIMG"}
  beat: {image: "$BIMG"}
  flower: {image: "$BIMG"}
  frontend: {image: "$FIMG"}
YAML
    cat > "$DIR/manifest.json" <<JSON
{
  "schemaVersion": 2,
  "product": "health",
  "version": "0.10.3",
  "tag": "v0.10.3",
  "contractVersion": "1",
  "images": {
    "backend": "$BIMG",
    "frontend": "$FIMG"
  }
}
JSON
    cat > "$DIR/fixtures/compose.json" <<JSON
{"name": "health-assistant", "services": {
  "nginx": {"image": "nginx:1.30.6-alpine", "ports": [{"published": "$PORT", "target": 80}]},
  "postgres": {"image": "timescale/timescaledb:2.32.0-pg16", "ports": [{"published": "$PG_PORT", "target": 5432}]},
  "migrate": {"image": "$BIMG"},
  "backend": {"image": "$BIMG"},
  "worker": {"image": "$BIMG"},
  "beat": {"image": "$BIMG"},
  "flower": {"image": "$BIMG"},
  "frontend": {"image": "$FIMG"}
}}
JSON
    : > "$DIR/fixtures/port_owners"
    : > "$DIR/fixtures/compose_ps"
    : > "$DIR/fixtures/ids"
    printf '{"health-assistant-backend": {"health": "healthy", "status": "running", "name": "health-assistant-backend"}}' \
        > "$DIR/fixtures/containers.json"
    printf '%s' "$DIR"
}

# seed_env SANDBOX — an existing operator .env (old pins + marker secrets).
seed_env() {
    cat > "$1/.env" <<ENV
HA_SESSION_KEY=marker-session-secret-keep-me
HA_REFRESH_KEY=marker-refresh-secret-keep-me
HA_DATA_KEY=marker-data-secret-keep-me
POSTGRES_PASSWORD=marker-owner-pw
HA_DB_PASSWORD=marker-app-pw
REDIS_PASSWORD=marker-redis-pw
FLOWER_PASSWORD=marker-flower-pw
HA_BACKEND_IMAGE=$OLD_BACKEND_PIN
HA_FRONTEND_IMAGE=$OLD_FRONTEND_PIN
IMAGE_TAG=0.10.2
HTTP_PORT=$FREE_PORT
ENV
}

run_docker_script() { # sandbox script args...
    local DIR="$1" SCRIPT="$2"; shift 2
    CASE_OUT="$(cd "$DIR" && env \
        PATH="${TEST_PATH:+$TEST_PATH:}$PATH" \
        DOCKER_FIXTURES="$DIR/fixtures" DOCKER_CALLS="$DIR/calls" \
        DOCKER_VOLUME_EXISTS="${DOCKER_VOLUME_EXISTS:-}" \
        HEALTH_WAIT_INTERVAL=1 HEALTH_WAIT_TIMEOUT=6 \
        READY_WAIT_INTERVAL=1 READY_WAIT_ATTEMPTS=2 \
        PULL_MODE="${PULL_MODE:-ok}" \
        CURL_MODE="${CURL_MODE:-ok}" \
        bash "$CORE/scripts/$SCRIPT" "$@" </dev/null 2>&1)"
    RUN_STATUS=$?
    RUN_CALLS="$(cat "$DIR/calls" 2>/dev/null || true)"
    : > "$DIR/calls" 2>/dev/null || true
    [ "$VERBOSE" -eq 1 ] && printf '%s\n' "$CASE_OUT"
    return 0
}

start_listener() { # port
    python3 - "$1" <<'PY' &
import socket, sys, time
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", int(sys.argv[1])))
srv.listen(4)
time.sleep(120)
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

# ── help / unknown flags / root refusal ───────────────────────────────────────
say "${YELLOW}install.sh: --help, unknown flags, root refusal${NC}"
SB="$(new_sandbox help "$FREE_PORT")"
run_docker_script "$SB" install.sh --help
assert_eq "--help exits 0" "$RUN_STATUS" "0"
assert_contains "help names --image-tag" "$CASE_OUT" "--image-tag"
assert_contains "help names --http-port" "$CASE_OUT" "--http-port"
assert_contains "help names --no-token" "$CASE_OUT" "--no-token"
assert_contains "help names --reset --yes" "$CASE_OUT" "--reset --yes"
assert_contains "help names the exit codes" "$CASE_OUT" "4 port conflict"
assert_not_contains "--help performs no install" "$RUN_CALLS" "up -d"

run_docker_script "$SB" install.sh --bogus-flag
assert_eq "unknown flag exits 1" "$RUN_STATUS" "1"
assert_contains "unknown flag points at --help" "$CASE_OUT" "try --help"

say "${YELLOW}install.sh: refuses root${NC}"
TEST_PATH="$WORK/bin-root"
run_docker_script "$SB" install.sh --env-only
TEST_PATH=""
assert_eq "root exits 1" "$RUN_STATUS" "1"
assert_contains "root refusal names the fix" "$CASE_OUT" "Refusing to run as root"
assert_not_contains "root refusal starts nothing" "$RUN_CALLS" "up -d"

# ── installer.json is the canonical config ────────────────────────────────────
say "${YELLOW}installer.json: canonical contract config${NC}"
check_installer_shape() { # file
    python3 - "$1" <<'PY'
import json, sys
required = ["product", "title", "repo", "repoDir", "targetPath", "phase",
            "bundlePrefix", "bootstrapAsset", "envPrefix", "imageEnv",
            "imageKeys", "defaultDirName", "defaultPort", "healthPath",
            "readyPath", "setupMode", "minRamGb", "docsUrl", "installUrl",
            "contractVersion", "installFlags"]
cfg = json.load(open(sys.argv[1]))
missing = [k for k in required if k not in cfg]
extra = [k for k in cfg if k not in required]
assert not missing, "missing required keys: %s" % missing
assert not extra, "extra keys rejected by additionalProperties:false: %s" % extra
assert cfg["product"] == "health", cfg["product"]
assert cfg["repo"] == "health-assistant-io/health-assistant"
assert cfg["targetPath"] == "scripts/bootstrap.sh"
assert cfg["imageEnv"] == "IMAGE_TAG"
assert cfg["imageKeys"] == ["backend", "frontend"], cfg["imageKeys"]
assert cfg["readyPath"] == "/ready", cfg["readyPath"]
assert cfg["setupMode"] == "setup token", cfg["setupMode"]
assert cfg["defaultPort"] == 80, cfg["defaultPort"]
assert cfg["healthPath"] == "/health"
assert cfg["installUrl"] == "https://neuronection.com/install/health.sh", cfg["installUrl"]
assert cfg["contractVersion"] == "1"
flags = set(cfg["installFlags"])
need = {"no-pull", "no-wait", "auto-ports", "no-ports", "http-port",
        "env-only", "reset", "yes", "image-tag", "no-token"}
assert need <= flags, "installFlags missing: %s" % (need - flags)
print("installer.json schema shape OK")
PY
}
if check_installer_shape "$CORE/installer.json" >/dev/null 2>&1; then
    ok "repo installer.json matches the schema shape"
else
    bad "repo installer.json matches the schema shape"
    check_installer_shape "$CORE/installer.json" 2>&1 | sed 's/^/       | /' | head -5
fi

# ── image pin sources (per-slot) ─────────────────────────────────────────────
say "${YELLOW}install.sh: image pin sources${NC}"
SB="$(new_sandbox pin-manifest "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only
assert_eq "--env-only with manifest exits 0" "$RUN_STATUS" "0"
assert_contains "backend digest pinned" "$(grep '^HA_BACKEND_IMAGE=' "$SB/.env")" "HA_BACKEND_IMAGE=$BACKEND_PIN"
assert_contains "frontend digest pinned" "$(grep '^HA_FRONTEND_IMAGE=' "$SB/.env")" "HA_FRONTEND_IMAGE=$FRONTEND_PIN"
assert_contains "release tag recorded in IMAGE_TAG" "$(grep '^IMAGE_TAG=' "$SB/.env")" "IMAGE_TAG=0.10.3"
assert_not_contains "--env-only pulls nothing" "$RUN_CALLS" " pull"
assert_not_contains "--env-only starts nothing" "$RUN_CALLS" "up -d"

say "${YELLOW}install.sh: fresh .env is 0600 and fully generated${NC}"
assert_eq ".env is 0600" "$(stat -c %a "$SB/.env")" "600"
if grep -qE '^(POSTGRES_PASSWORD=secure_password_here|HA_DB_PASSWORD=secure_app_password_here|REDIS_PASSWORD=change_me_redis_password|FLOWER_PASSWORD=change_this_to_a_secure_password|HA_SESSION_KEY=$|HA_REFRESH_KEY=$|HA_DATA_KEY=$)' "$SB/.env"; then
    bad "placeholders were replaced by generated secrets"
else
    ok "placeholders were replaced by generated secrets"
fi
SK1="$(grep '^HA_SESSION_KEY=' "$SB/.env" | cut -d= -f2-)"
SK2="$(grep '^HA_REFRESH_KEY=' "$SB/.env" | cut -d= -f2-)"
SK3="$(grep '^HA_DATA_KEY=' "$SB/.env" | cut -d= -f2-)"
if [ -n "$SK1" ] && [ "$SK1" != "$SK2" ] && [ -n "$SK3" ] && [ "$SK3" != "$SK1" ]; then
    ok "the key-family secrets are independent"
else
    bad "the key-family secrets are independent"
fi
OWNER_PW="$(grep '^POSTGRES_PASSWORD=' "$SB/.env" | cut -d= -f2-)"
APP_PW="$(grep '^HA_DB_PASSWORD=' "$SB/.env" | cut -d= -f2-)"
if [ -n "$OWNER_PW" ] && [ "$OWNER_PW" != "$APP_PW" ]; then
    ok "ADR-0064 owner/app DB secrets are distinct"
else
    bad "ADR-0064 owner/app DB secrets are distinct"
fi
assert_contains "setup-token mode is env" "$(grep '^SETUP_TOKEN_MODE=' "$SB/.env")" "SETUP_TOKEN_MODE=env"
TOKEN="$(grep '^SETUP_BOOTSTRAP_TOKEN=' "$SB/.env" | cut -d= -f2-)"
[ -n "$TOKEN" ] && ok "a bootstrap token was generated (stored in .env)" || bad "a bootstrap token was generated"
VAPID_PUB="$(grep '^VAPID_PUBLIC_KEY=' "$SB/.env" | cut -d= -f2-)"
VAPID_PRIV="$(grep '^VAPID_PRIVATE_KEY=' "$SB/.env" | cut -d= -f2-)"
if [ -n "$VAPID_PUB" ] && [ -n "$VAPID_PRIV" ] && [ "$VAPID_PUB" != "$VAPID_PRIV" ]; then
    ok "a VAPID Web Push pair was generated"
else
    bad "a VAPID Web Push pair was generated"
fi
if grep -q '^# HA_DEMO_EMAIL=' "$SB/.env"; then
    ok "demo credentials stay commented out"
else
    bad "demo credentials stay commented out"
fi
assert_not_contains "secrets are not printed (session key)" "$CASE_OUT" "$SK1"
assert_not_contains "secrets are not printed (db owner)" "$CASE_OUT" "$OWNER_PW"
assert_not_contains "the setup token is not printed by --env-only" "$CASE_OUT" "$TOKEN"

SB="$(new_sandbox pin-keyref "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only --image "backend=$BACKEND_PIN" --image "frontend=$FRONTEND_PIN"
assert_eq "--image KEY=REF (the bootstrap hand-off form) exits 0" "$RUN_STATUS" "0"
assert_contains "backend KEY=REF pin lands in .env" "$(grep '^HA_BACKEND_IMAGE=' "$SB/.env")" "HA_BACKEND_IMAGE=$BACKEND_PIN"
assert_contains "frontend KEY=REF pin lands in .env" "$(grep '^HA_FRONTEND_IMAGE=' "$SB/.env")" "HA_FRONTEND_IMAGE=$FRONTEND_PIN"

SB="$(new_sandbox pin-rogue "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only --image "rogue=$BACKEND_PIN"
assert_eq "a foreign image key exits 1" "$RUN_STATUS" "1"
assert_contains "foreign key names imageKeys" "$CASE_OUT" "unknown image key 'rogue'"

SB="$(new_sandbox pin-bare "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only --image "$BACKEND_PIN"
assert_eq "a bare ref (two image slots) exits 1" "$RUN_STATUS" "1"
assert_contains "bare ref asks for the slot" "$CASE_OUT" "refusing the bare image ref"
if [ -f "$SB/.env" ]; then
    bad "refusal happens before .env is written"
else
    ok "refusal happens before .env is written"
fi

SB="$(new_sandbox pin-tag "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only --image-tag v0.10.3
assert_eq "--image-tag exits 0" "$RUN_STATUS" "0"
assert_contains "tag resolves via the manifest repo (backend)" "$(grep '^HA_BACKEND_IMAGE=' "$SB/.env")" "HA_BACKEND_IMAGE=${BACKEND_REPO}:0.10.3"
assert_contains "tag resolves via the manifest repo (frontend)" "$(grep '^HA_FRONTEND_IMAGE=' "$SB/.env")" "HA_FRONTEND_IMAGE=${FRONTEND_REPO}:0.10.3"
assert_contains "tag recorded in IMAGE_TAG" "$(grep '^IMAGE_TAG=' "$SB/.env")" "IMAGE_TAG=0.10.3"

SB="$(new_sandbox pin-skew "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only --image-tag 0.11.0
assert_eq "tag skewing from the manifest exits 1" "$RUN_STATUS" "1"
assert_contains "skew names the manifest version" "$CASE_OUT" "version 0.10.3"

SB="$(new_sandbox pin-latest "$FREE_PORT")"
run_docker_script "$SB" install.sh --env-only --image "backend=${BACKEND_REPO}:latest"
assert_eq "operator-typed :latest is refused" "$RUN_STATUS" "1"
assert_contains ":latest refusal cites the rule" "$CASE_OUT" "Refusing the mutable pin"
if [ -f "$SB/.env" ]; then
    bad "mutable refusal happens before .env is written"
else
    ok "mutable refusal happens before .env is written"
fi

SB="$(new_sandbox pin-none "$FREE_PORT")"
rm "$SB/manifest.json"
run_docker_script "$SB" install.sh
assert_eq "no pin exits 1" "$RUN_STATUS" "1"
assert_contains "no pin explains the bundle has no source" "$CASE_OUT" "No image pin"
assert_contains "no pin names the manifest fix" "$CASE_OUT" "manifest.json"
if [ -f "$SB/.env" ]; then
    bad "no-pin failure writes no .env"
else
    ok "no-pin failure writes no .env"
fi

run_docker_script "$SB" install.sh --env-only
assert_eq "--env-only without a pin still writes .env" "$RUN_STATUS" "0"
assert_contains "--env-only warns about the missing pin" "$CASE_OUT" "no complete image pin"

# ── existing .env and the re-run contract ────────────────────────────────────
say "${YELLOW}install.sh: existing .env and re-run${NC}"
SB="$(new_sandbox exists "$FREE_PORT")"
rm "$SB/manifest.json"
seed_env "$SB"
BEFORE_SHA="$(sha256sum "$SB/.env" | awk '{print $1}')"
run_docker_script "$SB" install.sh --env-only
assert_eq "existing .env run exits 0" "$RUN_STATUS" "0"
assert_contains "existing .env is announced" "$CASE_OUT" "leaving it untouched"
assert_eq "existing .env is byte-identical" "$(sha256sum "$SB/.env" | awk '{print $1}')" "$BEFORE_SHA"
assert_not_contains "existing .env run starts nothing" "$RUN_CALLS" "up -d"

SB="$(new_sandbox rerun "$FREE_PORT")"
run_docker_script "$SB" install.sh --http-port "$FREE_PORT"
assert_eq "first full install exits 0" "$RUN_STATUS" "0"
assert_contains "summary names the app" "$CASE_OUT" "Health Assistant is running!"
assert_contains "health wait passes" "$CASE_OUT" "Backend is healthy (/health)"
assert_contains "ready wait passes" "$CASE_OUT" "App is ready"
assert_contains "summary names the health URL" "$CASE_OUT" "http://127.0.0.1:${FREE_PORT}/health"
assert_contains "summary names the ready URL" "$CASE_OUT" "http://127.0.0.1:${FREE_PORT}/ready"
assert_contains "summary names the backend pin" "$CASE_OUT" "$BACKEND_PIN"
assert_contains "setup URL is printed once" "$(grep -o 'setup?token=' <<<"$CASE_OUT" | wc -l)" "1"
RUN_TOKEN="$(grep '^SETUP_BOOTSTRAP_TOKEN=' "$SB/.env" | cut -d= -f2-)"
assert_contains "the printed setup URL carries the .env token" "$CASE_OUT" "setup?token=${RUN_TOKEN}"
assert_not_contains "no log-grep hint (D12)" "$CASE_OUT" "logs backend"
assert_contains "next steps name the updater" "$CASE_OUT" "scripts/update.sh"
ENV_SHA="$(sha256sum "$SB/.env" | awk '{print $1}')"
run_docker_script "$SB" install.sh
assert_eq "re-run exits 0" "$RUN_STATUS" "0"
assert_contains "re-run keeps .env" "$CASE_OUT" "leaving it untouched"
assert_eq "re-run does not rewrite .env" "$(sha256sum "$SB/.env" | awk '{print $1}')" "$ENV_SHA"
assert_contains "re-run still refreshes the stack" "$RUN_CALLS" "up -d"
assert_contains "re-run still pulls" "$RUN_CALLS" " pull"

SB="$(new_sandbox no-token "$FREE_PORT")"
run_docker_script "$SB" install.sh --http-port "$FREE_PORT" --no-token
assert_eq "--no-token full install exits 0" "$RUN_STATUS" "0"
assert_not_contains "the token is never printed with --no-token" "$CASE_OUT" "token="
assert_contains "--no-token still points at the wizard" "$CASE_OUT" "setup wizard"

# ── port pre-flight ───────────────────────────────────────────────────────────
say "${YELLOW}install.sh: port pre-flight${NC}"
start_listener "$BUSY_PORT" || bad "could not bind the busy test port"
SB="$(new_sandbox ports-busy "$BUSY_PORT")"
run_docker_script "$SB" install.sh --http-port "$BUSY_PORT"
assert_eq "busy port exits 4 (the contract's code)" "$RUN_STATUS" "4"
assert_contains "busy port names the .env knob" "$CASE_OUT" "HTTP_PORT"
assert_contains "busy port names the port" "$CASE_OUT" "$BUSY_PORT"
assert_not_contains "busy port aborts before pulling" "$RUN_CALLS" " pull"
assert_not_contains "busy port aborts before starting" "$RUN_CALLS" " up"

SB="$(new_sandbox ports-ours "$BUSY_PORT")"
printf '%s\thealth-assistant\n' "$BUSY_PORT" > "$SB/fixtures/port_owners"
run_docker_script "$SB" install.sh --http-port "$BUSY_PORT"
assert_eq "our own project's port is not a conflict" "$RUN_STATUS" "0"
stop_listeners

start_listener "$BUSY_PORT" || bad "could not bind the busy test port"
SB="$(new_sandbox ports-auto "$BUSY_PORT")"
run_docker_script "$SB" install.sh --http-port "$BUSY_PORT" --auto-ports
assert_eq "--auto-ports exits 0" "$RUN_STATUS" "0"
assert_contains "--auto-ports reports the move" "$CASE_OUT" "written to .env"
AUTO_PORT="$(grep '^HTTP_PORT=' "$SB/.env" | cut -d= -f2)"
if [ -n "$AUTO_PORT" ] && [ "$AUTO_PORT" != "$BUSY_PORT" ]; then
    ok "--auto-ports picked a different port ($AUTO_PORT)"
else
    bad "--auto-ports kept the busy port"
fi
assert_contains "--auto-ports still starts the stack" "$RUN_CALLS" "up -d"

SB="$(new_sandbox ports-skip "$BUSY_PORT")"
run_docker_script "$SB" install.sh --http-port "$BUSY_PORT" --no-ports
assert_eq "--no-ports skips the conflict check" "$RUN_STATUS" "0"
assert_contains "--no-ports starts the stack" "$RUN_CALLS" "up -d"
stop_listeners

# ── image verification against the rendered compose model ────────────────────
say "${YELLOW}install.sh: image verification${NC}"
SB="$(new_sandbox assert-bad "$FREE_PORT")"
python3 - "$SB/fixtures/compose.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
bad = "ghcr.io/health-assistant-io/health-assistant/health-assistant-backend@sha256:" + "b" * 64
for name in ("migrate", "backend", "worker", "beat", "flower"):
    data["services"][name]["image"] = bad
json.dump(data, open(path, "w"))
PY
run_docker_script "$SB" install.sh
assert_eq "rendered-image mismatch exits 3 (fixtures: manifest-image-compose-assert)" "$RUN_STATUS" "3"
assert_contains "mismatch names both refs" "$CASE_OUT" "Image verification failed"
assert_not_contains "mismatch aborts before pulling" "$RUN_CALLS" " pull"
assert_not_contains "mismatch aborts before starting" "$RUN_CALLS" " up"

SB="$(new_sandbox mutable-manifest "$FREE_PORT" "${BACKEND_REPO}:latest" "${FRONTEND_REPO}:latest")"
run_docker_script "$SB" install.sh
assert_eq "manifest :latest pins exit 3 at the render wall" "$RUN_STATUS" "3"
assert_contains "the mutable ref is named" "$CASE_OUT" "latest"
assert_not_contains "mutable manifest aborts before pulling" "$RUN_CALLS" " pull"
assert_not_contains "mutable manifest aborts before starting" "$RUN_CALLS" " up"

# ── reset ────────────────────────────────────────────────────────────────────
say "${YELLOW}install.sh: --reset${NC}"
SB="$(new_sandbox reset "$FREE_PORT")"
run_docker_script "$SB" install.sh --reset
assert_eq "--reset without --yes refuses non-interactively" "$RUN_STATUS" "1"
assert_contains "--reset without --yes explains the flag" "$CASE_OUT" "--reset needs --yes"

DOCKER_VOLUME_EXISTS=1
export DOCKER_VOLUME_EXISTS
SB="$(new_sandbox reset-yes "$FREE_PORT")"
# An existing .env (kept across the reset — the exact stale-volume scenario
# --reset exists for); the leftover guard therefore stays out of the way.
seed_env "$SB"
run_docker_script "$SB" install.sh --reset --yes
unset DOCKER_VOLUME_EXISTS
assert_eq "--reset --yes exits 0" "$RUN_STATUS" "0"
assert_contains "reset lists the postgres volume" "$CASE_OUT" "health-assistant_postgres_data"
assert_contains "reset lists the redis volume" "$CASE_OUT" "health-assistant_redis_data"
assert_contains "reset removes the volumes" "$RUN_CALLS" "volume rm"

# ── health / readiness wait failures ────────────────────────────────────────
say "${YELLOW}install.sh: health-wait failure reporting${NC}"
SB="$(new_sandbox wait-dead "$FREE_PORT")"
printf '%s' '{"health-assistant-redis": {"status": "exited", "exit": 1, "name": "health-assistant-redis"}, "health-assistant-backend": {"health": "starting", "status": "running", "name": "health-assistant-backend"}}' \
    > "$SB/fixtures/containers.json"
printf 'health-assistant-redis\n' > "$SB/fixtures/ids"
printf 'redis|exited|Exited (1)\n' > "$SB/fixtures/compose_ps"
run_docker_script "$SB" install.sh
assert_eq "a dead service fails the install (exit 3)" "$RUN_STATUS" "3"
assert_contains "the failure names the culprit" "$CASE_OUT" "Failed containers"

CURL_MODE=fail
SB="$(new_sandbox wait-notready "$FREE_PORT")"
run_docker_script "$SB" install.sh
assert_eq "a /ready that never turns green exits 3" "$RUN_STATUS" "3"
assert_contains "the readiness timeout is named" "$CASE_OUT" "did not report ready"
CURL_MODE=ok

# Exit 2: an image pull that cannot complete (offline host, registry auth).
PULL_MODE=fail
SB="$(new_sandbox pull-fail "$FREE_PORT")"
run_docker_script "$SB" install.sh
assert_eq "a failed pull exits 2 (the contract's download code)" "$RUN_STATUS" "2"
assert_contains "the pull failure names the escape hatch" "$CASE_OUT" "--no-pull"
assert_not_contains "a failed pull never starts the stack" "$RUN_CALLS" "up -d"
PULL_MODE=ok

# ── update.sh: pin bump, rollback pin, no destructive ops ────────────────────
say "${YELLOW}update.sh: pin bump, rollback pin, no destructive ops${NC}"
SB="$(new_sandbox update-bump "$FREE_PORT")"
seed_env "$SB"
run_docker_script "$SB" update.sh
assert_eq "update with the new manifest exits 0" "$RUN_STATUS" "0"
assert_contains "backend pin moved to the manifest digest" "$(grep '^HA_BACKEND_IMAGE=' "$SB/.env")" "HA_BACKEND_IMAGE=$BACKEND_PIN"
assert_contains "frontend pin moved to the manifest digest" "$(grep '^HA_FRONTEND_IMAGE=' "$SB/.env")" "HA_FRONTEND_IMAGE=$FRONTEND_PIN"
assert_contains "previous backend pin kept as a comment" "$(grep '# previous HA_BACKEND_IMAGE' "$SB/.env")" "$OLD_BACKEND_PIN"
assert_contains "previous frontend pin kept as a comment" "$(grep '# previous HA_FRONTEND_IMAGE' "$SB/.env")" "$OLD_FRONTEND_PIN"
assert_contains "pin move is reported" "$CASE_OUT" "backend pin moved"
assert_contains "rollback command printed" "$CASE_OUT" "--image backend=$OLD_BACKEND_PIN"
assert_contains "update pulls" "$RUN_CALLS" " pull"
assert_contains "update recreates the stack" "$RUN_CALLS" "up -d"
assert_not_contains "update removes no volumes" "$RUN_CALLS" "volume rm"

SB="$(new_sandbox update-keep "$FREE_PORT" "$OLD_BACKEND_PIN" "$OLD_FRONTEND_PIN")"
rm "$SB/manifest.json"
seed_env "$SB"
run_docker_script "$SB" update.sh
assert_eq "update without a new pin exits 0" "$RUN_STATUS" "0"
assert_contains "current pin kept" "$CASE_OUT" "No new pin found"
assert_contains "backend pin unchanged in .env" "$(grep '^HA_BACKEND_IMAGE=' "$SB/.env")" "HA_BACKEND_IMAGE=$OLD_BACKEND_PIN"

SB="$(new_sandbox update-mutable "$FREE_PORT")"
seed_env "$SB"
run_docker_script "$SB" update.sh --image "backend=${BACKEND_REPO}:latest"
assert_eq "update refuses an operator-typed :latest" "$RUN_STATUS" "1"
assert_contains "the refusal cites the contract" "$CASE_OUT" "Refusing the mutable pin"

SB="$(new_sandbox update-noenv "$FREE_PORT")"
rm "$SB/manifest.json"
run_docker_script "$SB" update.sh
assert_eq "update without .env exits 1" "$RUN_STATUS" "1"
assert_contains "points at install.sh" "$CASE_OUT" "Run scripts/install.sh first"

SB="$(new_sandbox update-alias "$FREE_PORT")"
seed_env "$SB"
run_docker_script "$SB" update-docker.sh
assert_eq "update-docker.sh alias forwards to update.sh" "$RUN_STATUS" "0"
assert_contains "alias moves the pin too" "$(grep '^HA_BACKEND_IMAGE=' "$SB/.env")" "HA_BACKEND_IMAGE=$BACKEND_PIN"

# ── bundle builder (source-free, manifest v2) ───────────────────────────────
say "${YELLOW}build-deploy-bundle.sh: source-free bundle, manifest v2${NC}"
BB="$WORK/bundle-repo"
mkdir -p "$BB/docker" "$BB/scripts"
for f in docker-compose.prod.yml docker-compose.standalone.yml nginx.conf nginx-TLS.conf \
         init-db.sql init-roles.sh README.md; do
    cp "$CORE/docker/$f" "$BB/docker/$f"
done
cp "$CORE/.env.example" "$BB/.env.example"
for f in install.sh update.sh upgrade-check.sh backup.sh restore.sh lib-docker.sh build-deploy-bundle.sh; do
    cp "$CORE/scripts/$f" "$BB/scripts/$f"
done
cp "$CORE/installer.json" "$BB/installer.json"
DUMMY_SHA="3b02ea310f9b3574e9b91f4f65c72f49b4fe73c1"
BB_BACKEND="ghcr.io/health-assistant-io/health-assistant/health-assistant-backend@sha256:${A64}"
BB_FRONTEND="ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend@sha256:${B64}"
if CASE_OUT="$(cd "$BB" && bash scripts/build-deploy-bundle.sh \
        --version 9.9.9 --commit "$DUMMY_SHA" \
        --backend-image "$BB_BACKEND" --frontend-image "$BB_FRONTEND" \
        --output-dir dist 2>&1)"; then
    ok "bundle build exits 0"
else
    bad "bundle build exits 0"
    printf '%s\n' "$CASE_OUT" | sed 's/^/       | /' | tail -n 25
fi
MANIFEST="$BB/dist/manifest.json"
assert_contains "manifest is schemaVersion 2" "$(cat "$MANIFEST")" '"schemaVersion": 2'
assert_contains "manifest product is the contract slug" "$(cat "$MANIFEST")" '"product": "health"'
assert_contains "manifest carries contractVersion" "$(cat "$MANIFEST")" '"contractVersion": "1"'
assert_contains "manifest carries the backend digest ref" "$(cat "$MANIFEST")" "\"backend\": \"${BB_BACKEND%@*}:9.9.9@sha256:${A64}\""
assert_contains "manifest carries the frontend digest ref" "$(cat "$MANIFEST")" "\"frontend\": \"${BB_FRONTEND%@*}:9.9.9@sha256:${B64}\""
assert_contains "manifest carries the version pair" "$(cat "$MANIFEST")" '"version": "9.9.9",'
TARLIST="$(tar -tzf "$BB/dist/health-compose-v9.9.9.tar.gz")"
assert_contains "bundle ships scripts/install.sh" "$TARLIST" "scripts/install.sh"
assert_contains "bundle ships scripts/update.sh" "$TARLIST" "scripts/update.sh"
assert_contains "bundle ships scripts/upgrade-check.sh" "$TARLIST" "scripts/upgrade-check.sh"
assert_contains "bundle ships installer.json" "$TARLIST" "installer.json"
assert_contains "bundle ships the env example under docker/" "$TARLIST" "docker/.env.example"
assert_contains "bundle ships backup helpers" "$TARLIST" "scripts/backup.sh"
if printf '%s\n' "$TARLIST" | grep -qE '(^|/)\.env$'; then
    bad "bundle ships no generated .env"
else
    ok "bundle ships no generated .env"
fi
assert_not_contains "bundle ships no source tree (.py)" "$TARLIST" ".py"
MBYTES="$(python3 -c "import json; print(json.load(open('$MANIFEST'))['bundle']['bytes'])")"
ACTUAL_BYTES="$(wc -c < "$BB/dist/health-compose-v9.9.9.tar.gz" | tr -d ' ')"
assert_eq "manifest bundle.bytes matches" "$MBYTES" "$ACTUAL_BYTES"
MSHA="$(python3 -c "import json; print(json.load(open('$MANIFEST'))['bundle']['sha256'])")"
assert_eq "manifest bundle.sha256 matches" "$MSHA" "$(sha256sum "$BB/dist/health-compose-v9.9.9.tar.gz" | awk '{print $1}')"
INNER_SHA="$(python3 -c "import json; print(json.load(open('$MANIFEST'))['sha256sums']['scripts/install.sh'])")"
EXTRACT="$WORK/bundle-extract" && rm -rf "$EXTRACT" && mkdir -p "$EXTRACT"
tar -xzf "$BB/dist/health-compose-v9.9.9.tar.gz" -C "$EXTRACT"
assert_eq "per-file sha256sums are real" "$INNER_SHA" "$(sha256sum "$EXTRACT/scripts/install.sh" | awk '{print $1}')"
VERIFIED="$(cd "$BB/dist" && sha256sum -c SHA256SUMS 2>&1)"
assert_contains "release SHA256SUMS verifies" "$VERIFIED" "OK"
FIRST_SHA="$MSHA"
rm -rf "$BB/dist"
(cd "$BB" && bash scripts/build-deploy-bundle.sh --version 9.9.9 --commit "$DUMMY_SHA" \
    --backend-image "$BB_BACKEND" --frontend-image "$BB_FRONTEND" --output-dir dist >/dev/null 2>&1)
SECOND_SHA="$(python3 -c "import json; print(json.load(open('$MANIFEST'))['bundle']['sha256'])")"
assert_eq "the build is deterministic (same content, same bundle sha)" "$FIRST_SHA" "$SECOND_SHA"

# ── bootstrap: the bundle flow, fail-closed ──────────────────────────────────
say "${YELLOW}bootstrap.sh: bundle flow, fail-closed${NC}"
# A fixture release: the exact assets a contract release publishes.
REL="$WORK/release-fixtures"
make_release_fixtures() { # dir-with-a-copy-of-the-sums
    local R="$1"
    mkdir -p "$R/inner/scripts" "$R/inner/docker"
    printf '#!/usr/bin/env bash\necho "stub installer ran: $*"\n' > "$R/inner/scripts/install.sh"
    chmod +x "$R/inner/scripts/install.sh"
    printf '{"product": "health"}\n' > "$R/inner/installer.json"
    printf 'services: {}\n' > "$R/inner/docker/docker-compose.standalone.yml"
    tar -czf "$R/health-compose-v0.10.3.tar.gz" -C "$R/inner" .
    cat > "$R/manifest.json" <<JSON
{
  "schemaVersion": 2,
  "product": "health",
  "version": "0.10.3",
  "images": {
    "backend": "$BACKEND_PIN",
    "frontend": "$FRONTEND_PIN"
  }
}
JSON
    ( cd "$R" && sha256sum health-compose-v0.10.3.tar.gz manifest.json > SHA256SUMS )
    printf '{"tag_name": "v0.10.3"}\n' > "$R/api.json"
    rm -rf "$R/inner"
}
make_release_fixtures "$REL"

run_bootstrap() { # args... — the exact-tag asset name is part of the case
    local SCRIPT="${BOOT_SCRIPT:-$CORE/scripts/bootstrap.sh}"
    CASE_OUT="$(PATH="$WORK/bin-curl:$PATH" CURL_FIXTURES="${CURL_FIXTURES:-$REL}" \
        CURL_MODE="${CURL_MODE:-ok}" STUB_HOME="$WORK" \
        bash "$SCRIPT" "$@" </dev/null 2>&1)"
    RUN_STATUS=$?
    [ "$VERBOSE" -eq 1 ] && printf '%s\n' "$CASE_OUT"
    return 0
}

# The exact-tag asset knows its release from its own filename — no API call.
cp "$CORE/scripts/bootstrap.sh" "$WORK/health-bootstrap-v0.10.3.sh"
BOOT_SCRIPT="$WORK/health-bootstrap-v0.10.3.sh"
TARGET="$WORK/bootstrap-target"
rm -rf "$TARGET"
run_bootstrap --dry-run --dir "$TARGET"
assert_eq "exact-tag dry run exits 0" "$RUN_STATUS" "0"
assert_contains "the tag resolves from the filename" "$CASE_OUT" "resolving the tag v0.10.3 from this file's name"
assert_contains "the release assets are fetched" "$CASE_OUT" "Fetching the release assets of v0.10.3"
assert_contains "the bundle sha is printed" "$CASE_OUT" "Bundle SHA-256:"
assert_contains "the hand-off forwards the backend pin" "$CASE_OUT" "--image backend=$BACKEND_PIN"
assert_contains "the hand-off forwards the frontend pin" "$CASE_OUT" "--image frontend=$FRONTEND_PIN"
if [ -e "$TARGET" ]; then
    bad "dry run wrote nothing to the target dir"
else
    ok "dry run wrote nothing to the target dir"
fi

# Mutable refs are refused, never silently installed.
run_bootstrap --ref main --dry-run --dir "$TARGET"
assert_eq "--ref main exits 1 (mutable refused)" "$RUN_STATUS" "1"
assert_contains "the refusal is explicit" "$CASE_OUT" "refusing mutable ref 'main'"

run_bootstrap --edge --dry-run --dir "$TARGET"
assert_eq "--edge is refused loudly (exit 2)" "$RUN_STATUS" "2"
assert_contains "--edge names the rule" "$CASE_OUT" "--edge selects a branch"

# The GitHub API being unreachable fails closed — never a branch fallback.
BOOT_SCRIPT="$CORE/scripts/bootstrap.sh"
CURL_MODE=fail
run_bootstrap --dir "$TARGET"
CURL_MODE=ok
assert_eq "api unreachable exits 2" "$RUN_STATUS" "2"
assert_contains "fail-closed names the rule" "$CASE_OUT" "never falls back to a branch"
assert_not_contains "no silent main appears" "$CASE_OUT" "falling back to 'main'"

# A tampered bundle aborts before extraction.
TAMPER="$WORK/release-tampered"
cp -r "$REL" "$TAMPER"
printf '\n# tampered\n' >> "$TAMPER/health-compose-v0.10.3.tar.gz"
BOOT_SCRIPT="$WORK/health-bootstrap-v0.10.3.sh"
CURL_FIXTURES="$TAMPER" run_bootstrap --dir "$TARGET"
assert_eq "checksum mismatch exits 2" "$RUN_STATUS" "2"
assert_contains "the mismatch aborts" "$CASE_OUT" "SHA-256 mismatch"
assert_contains "nothing was extracted" "$CASE_OUT" "nothing was extracted"
if [ -e "$TARGET" ]; then
    bad "a tampered bundle extracted nothing"
else
    ok "a tampered bundle extracted nothing"
fi

# Offline without SHA256SUMS refuses (fail closed).
OFFLINE_DIR="$WORK/offline"
mkdir -p "$OFFLINE_DIR"
cp "$REL/health-compose-v0.10.3.tar.gz" "$OFFLINE_DIR/"
run_bootstrap --offline "$OFFLINE_DIR/health-compose-v0.10.3.tar.gz" --dir "$TARGET"
assert_eq "offline without sums exits 2" "$RUN_STATUS" "2"
assert_contains "unverified offline bundles are refused" "$CASE_OUT" "refusing to install an unverified bundle"

# Offline WITH sums verifies and hands off to the bundle's own installer.
cp "$REL/SHA256SUMS" "$REL/manifest.json" "$OFFLINE_DIR/"
run_bootstrap --offline "$OFFLINE_DIR/health-compose-v0.10.3.tar.gz" --dir "$TARGET"
assert_eq "offline with sums exits 0 (stub installer ran)" "$RUN_STATUS" "0"
assert_contains "offline mode is announced" "$CASE_OUT" "Offline mode"
assert_contains "the hand-off reached the bundle installer" "$CASE_OUT" "stub installer ran"

# ── summary ──────────────────────────────────────────────────────────────────
say ""
if [ "$FAIL" -eq 0 ]; then
    printf "${GREEN}install self-test: %s passed${NC}\n" "$PASS"
    exit 0
fi
printf "${RED}install self-test: %s passed, %s FAILED${NC}\n" "$PASS" "$FAIL"
printf '%s' "$FAILED_CASES" | sed 's/^/  - /'
exit 1
