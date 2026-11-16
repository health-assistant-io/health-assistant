#!/usr/bin/env bash
# check-seed-pack — the bundled seed-pack drift gate (ADR 0056 Phase 6).
#
# The bundled seeds (backend/data/seeds) are the shipped canonical content:
# the boot apply digests them on every deploy, and the export/round-trip
# surface (SeedExportService → canonical pack zip → unpack) pins the same
# files. This gate proves the checked-in tree still satisfies the pack
# contract and that the canonical pipeline is deterministic:
#
#   P1 the bundled pack builds and validates against the pack contract
#      (contracts/seed-pack.schema.json via app.core.seed_pack) — an
#      unregistered JSON file, a missing item file or any contract error
#      fails here;
#   P2 two builds of the same tree are byte-identical (manifest + items),
#      and `seed_pack.py pack --check` against the first build is green —
#      the digest is stable, no timestamps anywhere;
#   P3 the canonical pack zip unpacks back into the exact same item bytes
#      (export → unpack round-trip, sha256-verified against the manifest);
#   P4 with SEED_PACK_CHECK_AGAINST set (or passed as arg 1): the given
#      pack directory (a checked-in or downloaded pack) is current —
#      `pack --check` exits non-zero on drift.
#
# Self-test: none (the pytest suite covers the CLI + service surfaces;
# this script composes them against the real tree).
#
# Usage: check-seed-pack.sh [PACK_DIR]   (run from the repo root; a
#        PACK_DIR argument is equivalent to SEED_PACK_CHECK_AGAINST)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
PYTHON="${PYTHON:-python3}"
# app.core.seed_pack is stdlib-only; PYTHONPATH covers the app package.
export PYTHONPATH="$BACKEND${PYTHONPATH:+:$PYTHONPATH}"

AGAINST="${1:-${SEED_PACK_CHECK_AGAINST:-}}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$BACKEND" || exit 2

echo "== seed-pack gate: building the bundled pack (P1)"
if ! $PYTHON scripts/seed_pack.py pack --out "$TMP/build-a" > "$TMP/digest-a.txt" 2> "$TMP/pack-a.err"; then
  echo "FAIL P1: the bundled pack does not build/validate" >&2
  cat "$TMP/pack-a.err" >&2
  exit 1
fi
echo "P1 OK — digest $(cat "$TMP/digest-a.txt")"

echo "== seed-pack gate: determinism across builds + pack --check (P2)"
if ! $PYTHON scripts/seed_pack.py pack --out "$TMP/build-b" > "$TMP/digest-b.txt" 2>/dev/null; then
  echo "FAIL P2: second build failed" >&2
  exit 1
fi
if ! diff -r "$TMP/build-a" "$TMP/build-b" > /dev/null 2>&1; then
  echo "FAIL P2: two builds of the same seeds tree differ (nondeterministic build)" >&2
  diff -rq "$TMP/build-a" "$TMP/build-b" | head -10 >&2
  exit 1
fi
if ! $PYTHON scripts/seed_pack.py pack --out "$TMP/build-a" --check > /dev/null 2>&1; then
  echo "FAIL P2: pack --check flagged drift on a fresh build" >&2
  exit 1
fi
echo "P2 OK — builds byte-identical, --check green"

echo "== seed-pack gate: canonical zip round-trip (P3)"
PY_ROUNDTRIP=$(
  $PYTHON - "$TMP/build-a" "$TMP/unpacked" <<'PYEOF'
import io, sys, zipfile
from pathlib import Path

pack = Path(sys.argv[1])
out = Path(sys.argv[2])
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
    zf.writestr(info, (pack / "manifest.json").read_text(encoding="utf-8"))
    for f in sorted((pack / "items").glob("*.json")):
        info = zipfile.ZipInfo(f"items/{f.name}", date_time=(1980, 1, 1, 0, 0, 0))
        zf.writestr(info, f.read_bytes())
zip_path = out.parent / "roundtrip.zip"
zip_path.write_bytes(buf.getvalue())

import subprocess
rc = subprocess.run(
    [sys.executable, "scripts/unpack_seeds_zip.py", str(zip_path), "--out", str(out), "--no-backup"],
    capture_output=True, text=True,
)
if rc.returncode != 0:
    print(rc.stderr, end=""); sys.exit(1)

bad = [
    f.name
    for f in sorted((pack / "items").glob("*.json"))
    if f.read_bytes() != (out / f.name).read_bytes()
]
if bad:
    print("round-trip drifted: " + ", ".join(bad), end=""); sys.exit(1)
print("ok")
PYEOF
)
if [[ "$PY_ROUNDTRIP" != "ok" ]]; then
  echo "FAIL P3: canonical zip round-trip ($PY_ROUNDTRIP)" >&2
  exit 1
fi
echo "P3 OK — pack zip unpacks to the same item bytes"

if [[ -n "$AGAINST" ]]; then
  echo "== seed-pack gate: checked-in pack currency (P4: $AGAINST)"
  if ! $PYTHON scripts/seed_pack.py pack --check --against "$AGAINST" > /dev/null 2> "$TMP/p4.err"; then
    echo "FAIL P4: checked-in pack has drifted from data/seeds" >&2
    cat "$TMP/p4.err" >&2
    exit 1
  fi
  echo "P4 OK — $AGAINST is current"
fi

echo "seed-pack gate: OK"
