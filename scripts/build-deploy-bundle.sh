#!/usr/bin/env bash
# Health Assistant — source-free deployment bundle builder.
#
# Builds `health-compose-v<version>.tar.gz`: everything an operator needs to
# stand the stack up from the published ghcr images, with NO source tree and
# NO secrets:
#
#   health-compose-v<version>/
#     docker/    both compose flavors (standalone + prod), the nginx configs
#                (plain + TLS variant), the postgres init scripts and the
#                operator runbook (docker/README.md)
#     scripts/   host backup/restore helpers (backup.sh, restore.sh and the
#                lib-docker.sh they source)
#     .env.example
#
# Next to the tarball the script writes the release metadata:
#
#   manifest.json — product/version/commit, BOTH image digests
#                   (images.backend / images.frontend, the exact artifacts
#                   the publish pipeline pushed), the bundle SHA256 and the
#                   per-file SHA256SUMS of the bundle members
#   SHA256SUMS    — release-level checksums (tarball + manifest.json)
#
# The tarball is built deterministically (sorted names, fixed mtimes, root
# owner, gzip -n) so the same content always yields the same bundle SHA.
#
# CI: `.github/workflows/release.yml` calls this on tag builds after the
# `Docker Build and Publish` pipeline (whose jobs expose the two digests as
# the `image-digests` artifact) has finished. Locally it runs from any
# checkout of this repo.
#
# Usage:
#   scripts/build-deploy-bundle.sh \
#     --version 0.10.0 \
#     --commit 3b02ea310f9b3574e9b91f4f65c72f49b4fe73c1 \
#     --backend-image  ghcr.io/health-assistant-io/health-assistant/health-assistant-backend@sha256:… \
#     --frontend-image ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend@sha256:… \
#     --output-dir dist
#     [--image-tag 0.10.0]   # defaults to the version without the leading v

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
die()   { echo -e "${RED}error:${NC} $*" >&2; exit 1; }
info()  { echo -e "${GREEN}==>${NC} $*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PRODUCT="health-assistant"

# The complete, allowlisted bundle surface. Everything an operator (or the
# release workflow) can extract is on this list — the source-free guard
# below verifies the staged tree against it, so a new docker/ file can only
# ship in the bundle by being added here deliberately.
readonly BUNDLE_MEMBERS=(
  docker/docker-compose.standalone.yml
  docker/docker-compose.prod.yml
  docker/nginx.conf
  docker/nginx-TLS.conf
  docker/init-db.sql
  docker/init-roles.sh
  docker/README.md
  scripts/backup.sh
  scripts/restore.sh
  scripts/lib-docker.sh
  .env.example
)

VERSION=""
COMMIT=""
BACKEND_IMAGE=""
FRONTEND_IMAGE=""
IMAGE_TAG=""
OUTPUT_DIR="$REPO_ROOT/dist"

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)        VERSION="${2:?--version needs a value}"; shift 2 ;;
    --commit)         COMMIT="${2:?--commit needs a value}"; shift 2 ;;
    --backend-image)  BACKEND_IMAGE="${2:?--backend-image needs a value}"; shift 2 ;;
    --frontend-image) FRONTEND_IMAGE="${2:?--frontend-image needs a value}"; shift 2 ;;
    --image-tag)      IMAGE_TAG="${2:?--image-tag needs a value}"; shift 2 ;;
    --output-dir)     OUTPUT_DIR="${2:?--output-dir needs a value}"; shift 2 ;;
    -h|--help)        usage 0 ;;
    *)                die "unknown argument: $1 (see --help)" ;;
  esac
done

# ─── Validate inputs ─────────────────────────────────────────────────────────
# Accept `v0.10.0` or `0.10.0` (+ pre-release suffixes); the manifest and the
# image tag always carry the version WITHOUT the leading v — the same shape
# bootstrap.sh/install.sh pin and the publish pipeline tags.
VERSION="${VERSION#v}"
[ -n "$VERSION" ] || die "--version is required"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+._0-9A-Za-z]*)?$ ]] \
  || die "--version '$VERSION' is not a semver(ish) version"
[ -n "$COMMIT" ] || die "--commit is required"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "--commit must be a full 40-hex git SHA (got '$COMMIT')"
IMAGE_TAG="${IMAGE_TAG:-$VERSION}"
[ -n "$BACKEND_IMAGE" ]  || die "--backend-image is required"
[ -n "$FRONTEND_IMAGE" ] || die "--frontend-image is required"

for ref in "$BACKEND_IMAGE" "$FRONTEND_IMAGE"; do
  # <name>[:<tag>]@sha256:<64 hex> — the release workflow passes the fully
  # tagged ref (name:version@digest); locally a bare name@digest also works.
  [[ "$ref" =~ ^[a-z0-9._/-]+(:[a-z0-9._-]+)?@sha256:[0-9a-f]{64}$ ]] \
    || die "image ref must be <name>[:<tag>]@sha256:<64 hex> (got '$ref')"
done

# ─── Stage the allowlisted bundle surface ────────────────────────────────────
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
readonly BUNDLE_DIRNAME="health-compose-v${VERSION}"
BUNDLE_ROOT="$STAGE/$BUNDLE_DIRNAME"
mkdir -p "$BUNDLE_ROOT"

for member in "${BUNDLE_MEMBERS[@]}"; do
  src="$REPO_ROOT/$member"
  [ -f "$src" ] || die "allowlisted bundle member missing from the repo: $member"
  mkdir -p "$BUNDLE_ROOT/$(dirname "$member")"
  cp -p "$src" "$BUNDLE_ROOT/$member"
done
chmod 0755 "$BUNDLE_ROOT/scripts/"*.sh

# ─── Source-free + no-secrets guard ──────────────────────────────────────────
# (cd into the staged tree so paths are bundle-relative in every message)
assert_source_free() (
  cd "$BUNDLE_ROOT" || exit 1

  # 1. The file list is EXACTLY the allowlist — nothing else may leak in.
  while IFS= read -r f; do
    rel="${f#./}"
    found=0
    for member in "${BUNDLE_MEMBERS[@]}"; do
      [ "$rel" = "$member" ] && { found=1; break; }
    done
    [ "$found" = 1 ] || die "non-allowlisted file staged in the bundle: $rel"
  done < <(find . -type f | sort)

  # 2. No source tree, no build state, no secrets — belt over suspenders.
  while IFS= read -r f; do
    rel="${f#./}"
    case "$rel" in
      *.py|*.pyc|*.ts|*.tsx|*.js|*.mjs|*.cjs|*.go|*.rs|*.java|*.kt|*.sqlite|*.sqlite3|*.db)
        die "source/state file staged in the bundle: $rel" ;;
      backend/*|frontend/*|integrations/*|contracts/*|node_modules/*|.git/*|.venv/*)
        die "forbidden path staged in the bundle: $rel" ;;
      .env)
        die "refusing to ship a real .env — the bundle carries .env.example only" ;;
    esac
  done < <(find . -type f)

  # 3. The env template stays a template: no credential-shaped values.
  if grep -nE '(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|-----BEGIN)' .env.example; then
    die ".env.example looks like it carries real credential material"
  fi
)
assert_source_free
info "staged ${#BUNDLE_MEMBERS[@]} files (source-free check passed)"

# ─── Deterministic tarball ───────────────────────────────────────────────────
mkdir -p "$OUTPUT_DIR"
TARBALL="$OUTPUT_DIR/$BUNDLE_DIRNAME.tar.gz"
# GNU tar: sorted names, epoch mtimes, root ownership; gzip -n drops the
# filename/timestamp so identical content always hashes identically.
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime='1970-01-01 00:00:00Z' --format=gnu \
    -cf - -C "$STAGE" "$BUNDLE_DIRNAME" \
  | gzip -n -9 > "$TARBALL"

BUNDLE_SHA="$(sha256sum "$TARBALL" | awk '{print $1}')"
BUNDLE_BYTES="$(stat -c %s "$TARBALL")"
info "bundle: $TARBALL ($BUNDLE_BYTES bytes, sha256 ${BUNDLE_SHA:0:12}…)"

# ─── manifest.json ───────────────────────────────────────────────────────────
# JSON is written by python3 (never string-interpolated) so the values above
# cannot break the structure.
MANIFEST="$OUTPUT_DIR/manifest.json"
# Pullable ref, pinned by digest. The name part may already carry the version
# tag (the release workflow passes name:version@digest) — keep it; otherwise
# append IMAGE_TAG.
build_ref() {
  local base="${1%@*}" digest="${1#*@}"
  case "$base" in
    *:*) printf '%s@%s' "$base" "$digest" ;;
    *)   printf '%s:%s@%s' "$base" "$IMAGE_TAG" "$digest" ;;
  esac
}
BACKEND_REF="$(build_ref "$BACKEND_IMAGE")"
FRONTEND_REF="$(build_ref "$FRONTEND_IMAGE")"

export BUNDLE_ROOT VERSION COMMIT PRODUCT IMAGE_TAG \
  BACKEND_IMAGE FRONTEND_IMAGE BACKEND_REF FRONTEND_REF \
  BUNDLE_FILE="${BUNDLE_DIRNAME}.tar.gz" \
  BUNDLE_SHA BUNDLE_BYTES MANIFEST
python3 - <<'PY'
import datetime
import hashlib
import json
import os
import sys

root = os.environ["BUNDLE_ROOT"]

# Per-file SHA256SUMS over the staged bundle members (bundle-relative paths).
members = {}
for dirpath, _dirnames, filenames in os.walk(root):
    for name in sorted(filenames):
        path = os.path.join(dirpath, name)
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        members[rel] = digest.hexdigest()

manifest = {
    "product": os.environ["PRODUCT"],
    "version": os.environ["VERSION"],
    "tag": f"v{os.environ['VERSION']}",
    "commit": os.environ["COMMIT"],
    "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    "images": {
        "tag": os.environ["IMAGE_TAG"],
        # The two digests the publish pipeline actually pushed — the release
        # manifest is the contract between the bundle and the registry.
        "backend": os.environ["BACKEND_IMAGE"].split("@", 1)[1],
        "frontend": os.environ["FRONTEND_IMAGE"].split("@", 1)[1],
        # Pullable refs, pinned by digest (tag@digest).
        "backend_ref": os.environ["BACKEND_REF"],
        "frontend_ref": os.environ["FRONTEND_REF"],
    },
    "bundle": {
        "file": os.environ["BUNDLE_FILE"],
        "sha256": os.environ["BUNDLE_SHA"],
        "bytes": int(os.environ["BUNDLE_BYTES"]),
    },
    "sha256sums": dict(sorted(members.items())),
}

with open(os.environ["MANIFEST"], "w") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
PY

# The manifest must not lie: the embedded digest fields match what was passed.
while IFS='=' read -r key want; do
  got="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['images'][sys.argv[2]])" "$MANIFEST" "$key")"
  [ "$got" = "$want" ] || die "manifest self-check failed: images.$key=$got != $want"
done <<EOF
backend=${BACKEND_IMAGE#*@}
frontend=${FRONTEND_IMAGE#*@}
EOF

# ─── SHA256SUMS (release level: tarball + manifest) ──────────────────────────
SUMS="$OUTPUT_DIR/SHA256SUMS"
(cd "$OUTPUT_DIR" && sha256sum "${BUNDLE_DIRNAME}.tar.gz" manifest.json > SHA256SUMS)

# Keep SHA256SUMS out of itself (regenerate would loop) — it lists exactly
# the two release assets the workflow uploads alongside it.
[ "$(wc -l < "$SUMS")" = "2" ] || die "SHA256SUMS should list exactly two assets"

info "manifest: $MANIFEST"
info "sums:     $SUMS"
echo "---- release assets ----"
ls -l "$TARBALL" "$MANIFEST" "$SUMS"
echo "---- SHA256SUMS ----"
cat "$SUMS"
