#!/bin/bash
# Double-click this file on a Mac to open the Deal Tracker.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python isn't installed. Get it from https://www.python.org/downloads/ then double-click this again."
  read -p "Press Enter to close."; exit 1
fi
if [ ! -f config.json ]; then python3 dealfinder.py setup; fi
python3 dealfinder.py dashboard
