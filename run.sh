#!/bin/bash
# Keeps Jet Tracker 1 running on :8135 (restarts on crash). Usage: nohup setsid ./run.sh >/dev/null 2>&1 &
cd "$(dirname "$0")"
while true; do python3 server.py "${PORT:-8135}" >> server.log 2>&1; sleep 5; done
