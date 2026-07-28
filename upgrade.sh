#!/usr/bin/env bash
# AXP upgrade hook. install.sh is idempotent and performs dependency convergence.
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '[graph-memory] upgrading from %s\n' "${AXP_FROM_VERSION:-unknown}"
exec "${SOURCE_DIR}/install.sh"
