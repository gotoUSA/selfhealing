#!/usr/bin/env bash
# Grafana Dashboard UID Sanitizer
#
# Replaces hardcoded datasource UIDs and names with template variables,
# making dashboards portable across Grafana instances.
#
# Usage: bash sanitize-dashboards.sh [DASHBOARD_DIR]
set -euo pipefail

DASHBOARD_DIR="${1:-examples/monitoring}"

for f in "$DASHBOARD_DIR"/*.json; do
  [ -f "$f" ] || continue

  # 1) UID-based references → template variables
  sed -i \
    -e 's/"uid":\s*"prometheus"/"uid": "${DS_PROMETHEUS}"/g' \
    -e 's/"uid":\s*"mimir"/"uid": "${DS_MIMIR}"/g' \
    -e 's/"uid":\s*"tempo"/"uid": "${DS_TEMPO}"/g' \
    -e 's/"uid":\s*"loki"/"uid": "${DS_LOKI}"/g' \
    "$f"

  # 2) datasourceUid (Exemplar/Cross-DS links) → template variables
  sed -i \
    -e 's/"datasourceUid":\s*"prometheus"/"datasourceUid": "${DS_PROMETHEUS}"/g' \
    -e 's/"datasourceUid":\s*"tempo"/"datasourceUid": "${DS_TEMPO}"/g' \
    -e 's/"datasourceUid":\s*"loki"/"datasourceUid": "${DS_LOKI}"/g' \
    -e 's/"datasourceUid":\s*"mimir"/"datasourceUid": "${DS_MIMIR}"/g' \
    "$f"

  # 3) Name-based references → UID-based format
  sed -i \
    -e 's/"datasource":\s*"Prometheus"/"datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}/g' \
    "$f"

  # 4) Timezone → UTC (global compatibility)
  sed -i \
    -e 's/"timezone":\s*"Asia\/Seoul"/"timezone": "utc"/g' \
    "$f"

  echo "Sanitized: $f"
done
