#!/usr/bin/env bash
# AXP standalone uninstaller. State is preserved unless AXP_PURGE=1.
set -euo pipefail

log() { printf '[graph-memory] %s\n' "$*"; }
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="${HERMES_HOME}/plugins/graph-memory"
LIFECYCLE_DIR="${AXP_INSTALL_DIR:-/opt/graph-memory}"
[ -d "${LIFECYCLE_DIR}" ] || LIFECYCLE_DIR="${HERMES_HOME}/extensions/graph-memory"

if command -v hermes >/dev/null 2>&1; then
  config_path="$(hermes config path 2>/dev/null || true)"
  if [ -n "${config_path}" ] && python3 - "${config_path}" <<'PY'
import sys
try:
    import yaml
    config = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
    raise SystemExit(0 if (config.get("memory") or {}).get("provider") == "graph-memory" else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    hermes config set memory.provider ""
  fi
fi

rm -rf "${PLUGIN_DIR}"
if [ "${AXP_PURGE:-0}" = "1" ]; then
  rm -rf "${HERMES_HOME}/graph-memory"
  log "removed plugin and graph database"
else
  log "removed plugin; preserved graph database at ${HERMES_HOME}/graph-memory"
  log "set AXP_PURGE=1 when uninstalling to delete stored memory too"
fi

# Remove lifecycle files last; tolerate a non-root installation path.
rm -rf "${LIFECYCLE_DIR}"
