#!/usr/bin/env bash
# AXP health hook: exit 0 = healthy, non-zero = unhealthy.
set -euo pipefail

log() { printf '[graph-memory] %s\n' "$*"; }
die() { log "UNHEALTHY: $*" >&2; exit 1; }

command -v hermes >/dev/null 2>&1 || die "Hermes is not on PATH"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="${HERMES_HOME}/plugins/graph-memory"
[ -f "${PLUGIN_DIR}/__init__.py" ] || die "provider is not installed at ${PLUGIN_DIR}"
[ -f "${PLUGIN_DIR}/plugin.yaml" ] || die "plugin.yaml is missing"

status="$(HERMES_HOME="${HERMES_HOME}" hermes memory status 2>&1)" || die "hermes memory status failed"
printf '%s\n' "${status}" | grep -q 'Provider:  graph-memory' \
  || die "graph-memory is installed but not the active provider"
printf '%s\n' "${status}" | grep -q 'Status:    available' \
  || die "Python dependencies are unavailable"

log "healthy: provider installed, active, and dependencies import successfully"
