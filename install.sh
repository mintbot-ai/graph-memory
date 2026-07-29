#!/usr/bin/env bash
# AXP standalone installer: works without an AXP-aware host.
set -euo pipefail

log() { printf '[graph-memory] %s\n' "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

command -v hermes >/dev/null 2>&1 || die "Hermes is not installed or not on PATH"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="${HERMES_HOME}/plugins/graph-memory"
LIFECYCLE_DIR="${AXP_INSTALL_DIR:-/opt/graph-memory}"

if [ "$(id -u)" -ne 0 ] && [[ "${LIFECYCLE_DIR}" == /opt/* ]]; then
  LIFECYCLE_DIR="${HERMES_HOME}/extensions/graph-memory"
  log "not running as root; lifecycle scripts will live at ${LIFECYCLE_DIR}"
fi

log "installing Hermes provider at ${PLUGIN_DIR}"
install -d -m 0755 \
  "${PLUGIN_DIR}" \
  "${PLUGIN_DIR}/schemas" \
  "${PLUGIN_DIR}/scripts" \
  "${LIFECYCLE_DIR}"
install -m 0644 \
  "${SOURCE_DIR}/__init__.py" \
  "${SOURCE_DIR}/hermes_llm.py" \
  "${SOURCE_DIR}/ladybug_driver.py" \
  "${SOURCE_DIR}/local_embeddings.py" \
  "${SOURCE_DIR}/plugin.yaml" \
  "${PLUGIN_DIR}/"
install -m 0644 "${SOURCE_DIR}"/schemas/* "${PLUGIN_DIR}/schemas/"
install -m 0755 \
  "${SOURCE_DIR}/scripts/migrate_kuzu_to_ladybug.py" \
  "${PLUGIN_DIR}/scripts/"
for file in README.md LICENSE agent-extension.json after-install.md; do
  [ -f "${SOURCE_DIR}/${file}" ] && install -m 0644 "${SOURCE_DIR}/${file}" "${PLUGIN_DIR}/${file}"
done
for script in install.sh upgrade.sh uninstall.sh healthcheck.sh; do
  install -m 0755 "${SOURCE_DIR}/${script}" "${LIFECYCLE_DIR}/${script}"
done

log "installing pinned Python dependencies and activating the provider"
hermes memory setup graph-memory

log "running health check"
HERMES_HOME="${HERMES_HOME}" "${LIFECYCLE_DIR}/healthcheck.sh"
log "installation complete; start a new Hermes session to activate graph memory"
