#!/usr/bin/env bash
# check-extensions-structure — the D24 structure probe (ADR-0040 / plan 32
# Phase 6; BLOCKING since the Phase 9 closeout). Findings print as WARN;
# pass --strict to fail on any finding — the family-convergence workflow
# runs this probe with --strict, so a finding fails CI. Ported from
# career's reference probe (plan 32 Phase 1) with health-scoped rules.
#
# Probes:
#  1 scoped engine code only in the subsystem home — the pre-D24 framework
#    paths are gone (backend/app/core/integration_registry.py and the
#    integrations/ framework tree) while orchestration keeps its home
#    (services/integration_sync_service.py), MCP client construction stays
#    in ai/ (mcp_client_factory.py, ADR-0012 alignment gate R2), and the
#    bridge SDK satellites stay consumer-only (integrations/health-assistant-bridge)
#  2 no dynamic loading of NON-PACKAGE code under the subsystem home and no
#    sys.path mutation (D2's gate point: imported artifacts are data). The
#    registry's importlib.import_module of in-package
#    app.extensions.builtin.* T1 providers is the sanctioned lazy-load —
#    bundled in-repo code, never data-dir content; anything loading from a
#    Path / outside the package is a finding.
#  3 fastmcp/mcp imports only under backend/app/ai/ (same CLASS_RE as the
#    vendored gate, so probe and gate never disagree)
#  4 vendored family contract modules exist at their ledgered paths —
#    drift vs the dev canonicals is the dev repo's sync --check gate, not
#    this probe
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRICT=0
[[ "${1:-}" == "--strict" ]] && STRICT=1

findings=0
note() { echo "WARN: $1"; findings=$((findings + 1)); }
ok() { echo "ok:   $1"; }

# 1 — old paths gone, scoped homes in place
for gone in \
  "backend/app/core/integration_registry.py" \
  "integrations/base.py" \
  "integrations/sdk" \
  "integrations/dev_dummy" \
  "integrations/fhir_server" \
  "integrations/webhook" \
  "integrations/mcp_client"; do
  if [ -e "$ROOT/$gone" ]; then
    note "pre-D24 path still present: $gone (engine code lives in backend/app/extensions/)"
  else
    ok "pre-D24 path absent: $gone"
  fi
done
if [ -f "$ROOT/backend/app/services/integration_sync_service.py" ]; then
  ok "integration sync orchestration stays in services/ (integration_sync_service.py)"
else
  note "services/integration_sync_service.py missing — sync orchestration belongs in services/ (D24 rule 4)"
fi
if [ -f "$ROOT/backend/app/ai/mcp_client_factory.py" ]; then
  ok "AI-layer MCP client factory stays in ai/ (mcp_client_factory.py, ADR-0012)"
else
  note "backend/app/ai/mcp_client_factory.py missing — MCP client construction stays in ai/"
fi
if [ -d "$ROOT/integrations/health-assistant-bridge" ]; then
  ok "bridge SDK satellites stay consumer-only (integrations/health-assistant-bridge)"
else
  note "integrations/health-assistant-bridge missing — the tracked SDK satellites live there"
fi

# 2 — no dynamic loading of non-package code under the subsystem home
# (D2's gate point). Health's registry lazy-loads T1 bundled providers via
# importlib.import_module("app.extensions.builtin.<domain>.…") — in-package
# reviewed code, the sanctioned pattern; any other dynamic load (machinery/
# util/SourceFileLoader/exec_module, or import_module of anything outside
# app.extensions) is a finding, and sys.path mutation is banned outright.
dyn_hits="$(grep -rn -E "importlib\.(machinery|util|reload)|SourceFileLoader|exec_module" "$ROOT/backend/app/extensions" 2>/dev/null | grep -v __pycache__ || true)"
if [ -n "$dyn_hits" ]; then
  note "dynamic file loading under app/extensions (imported code is data, never loaded — D2):"
  echo "$dyn_hits" | sed 's/^/      /'
else
  ok "no dynamic file loading under app/extensions (D2 gate point)"
fi
import_module_hits="$(
  grep -rn -A1 -E "importlib\.import_module" "$ROOT/backend/app/extensions" 2>/dev/null | grep -v __pycache__ \
  | awk '/:[0-9]+:[-:]?/ && !/-[0-9]+-/ { if (buf != "" && buf !~ /app\.extensions/) print buf; buf=$0; next }
         { buf = buf " " $0 }
         END { if (buf != "" && buf !~ /app\.extensions/) print buf }' || true)"
if [ -n "$import_module_hits" ]; then
  note "import_module of non-package modules under app/extensions (only app.extensions.builtin.* lazy loads are sanctioned):"
  echo "$import_module_hits" | sed 's/^/      /'
else
  ok "import_module under app/extensions targets in-package builtin modules only"
fi
syspath_hits="$(grep -rn -E "sys\.path\.(append|insert)|PYTHONPATH" "$ROOT/backend/app/extensions" 2>/dev/null | grep -v __pycache__ || true)"
if [ -n "$syspath_hits" ]; then
  note "sys.path/PYTHONPATH mutation under app/extensions (data-dir content is spawned, never importable — D2):"
  echo "$syspath_hits" | sed 's/^/      /'
else
  ok "no sys.path mutation under app/extensions (imported content is data, never a module path)"
fi

# 3 — fastmcp/mcp imports only under backend/app/ai/ (same CLASS_RE as the
# vendored check-ai-alignment gate R2, so the two checks agree by construction;
# tightened 2026-10-17 to catch nested module paths + plain imports — the
# first-level match was the plan-32 Phase 9 blind spot)
mcp_hits="$(
  grep -rln -E 'from fastmcp([. ]|$)|from mcp[.](server|client|shared)([.]| import)|import mcp[.](server|client|shared)' "$ROOT/backend/app" 2>/dev/null \
  | grep -v __pycache__ | grep -v "^$ROOT/backend/app/ai/" || true)"
if [ -n "$mcp_hits" ]; then
  note "fastmcp/mcp imports outside app/ai/ (ADR-0012 alignment gate R2):"
  echo "$mcp_hits" | sed 's/^/      /'
else
  ok "fastmcp/mcp imports confined to app/ai/ (ADR-0012)"
fi

# 4 — vendored family contract set at ledgered paths
for vendored in \
  "backend/app/extensions/polite_http.py" \
  "backend/app/extensions/contract.py" \
  "backend/app/extensions/conformance.py" \
  "contracts/extension-charter.json" \
  "contracts/extension-manifest.schema.json" \
  "contracts/extension-sources.schema.json" \
  "contracts/capability-rpc.schema.json" \
  "contracts/capability-rpc.md" \
  "contracts/extension-contract.fixtures.json" \
  "contracts/polite_http.fixtures.json"; do
  if [ -f "$ROOT/$vendored" ]; then
    ok "vendored: $vendored"
  else
    note "vendored contract module missing: $vendored (run ../../dev/scripts/sync-extension-contract.sh --project health-assistant/core)"
  fi
done

echo
if [ "$findings" -gt 0 ]; then
  echo "extensions structure: $findings finding(s)"
  if [ "$STRICT" -eq 1 ]; then
    exit 1
  fi
  echo "warning-level run (plan 32 Phase 9: CI invokes this probe --strict)"
  exit 0
fi
echo "extensions structure: OK"
