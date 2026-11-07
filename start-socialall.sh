#!/bin/sh
# Start SocialAll as an app on Linux: ./start-socialall.sh
# The first run sets things up, which takes about a minute. Extra arguments go to `python -m socialall.desktop`,
# e.g. ./start-socialall.sh stop
cd "$(dirname "$0")" || exit 1

for py in python3.13 python3.12 python3.14 python3.11 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$py" scripts/bootstrap.py "$@"
  fi
done

echo "SocialAll needs Python 3.11 or newer. Install it with your package manager, then run this again." >&2
exit 1
