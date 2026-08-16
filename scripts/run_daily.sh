#!/bin/bash
# The daily run. Invoked by launchd at 6:00 PM, or by hand to test.
#
#   bash scripts/run_daily.sh              # fetch, select, send
#   bash scripts/run_daily.sh --dry-run    # everything except the send

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$HOME/.config/daily-deals/env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Run: bash $REPO/scripts/setup_local.sh" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

cd "$REPO"

echo
echo "================================================================"
echo "Run started $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================================"

echo
echo "--- 1/3  fetching candidates ---"
if ! python3 scripts/fetch_candidates.py; then
  echo "FAILED at fetch step." >&2
  exit 1
fi

echo
echo "--- 2/3  selecting the deal ---"
if ! python3 scripts/select_deal.py; then
  echo "FAILED at selection step." >&2
  exit 1
fi

echo
echo "--- 3/3  sending ---"
if ! python3 scripts/send_notification.py "$@"; then
  echo "FAILED at send step." >&2
  exit 1
fi

echo
echo "Run finished $(date '+%H:%M:%S')."
