#!/usr/bin/env bash
# Convenience wrapper: scrape (if needed) then serve.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f data/league.sqlite3 ]; then
  echo "No database yet — scraping..."
  python3 -m fantasy.cli scrape
fi
exec python3 -m fantasy.cli serve "$@"
