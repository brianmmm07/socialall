#!/bin/bash
# Double-click to start SocialAll on a Mac. The first run sets things up, which takes about a minute.
# After that it opens in a second or two. Closing this window doesn't stop SocialAll.
# Extra arguments go to `python -m socialall.desktop`, e.g. ./Start\ SocialAll.command autostart on
cd "$(dirname "$0")" || exit 1

for py in python3.13 python3.12 python3.14 python3.11 python3 \
          /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    "$py" scripts/bootstrap.py "$@" && exit 0
    echo
    read -r -p "SocialAll didn't start (see above). Press Return to close this window." _
    exit 1
  fi
done

echo "SocialAll needs Python 3.11 or newer. Get it from https://www.python.org/downloads/ and double-click this again."
read -r -p "Press Return to close this window." _
exit 1
