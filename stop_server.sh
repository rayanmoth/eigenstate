#!/usr/bin/env bash
# Stop the server. Kills what we started AND anything else on the port, because
# a stale process holding 5055 is the single most confusing failure mode here.
set -u
cd "$(dirname "$0")" || exit 1
PORT=5055
PIDFILE=.server.pid

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill "$PID" 2>/dev/null; then echo "stopped $PID (ours)"; fi
    rm -f "$PIDFILE"
fi

if command -v lsof >/dev/null 2>&1; then
    for pid in $(lsof -ti "tcp:$PORT" 2>/dev/null); do
        kill "$pid" 2>/dev/null && echo "stopped $pid (was on port $PORT)"
    done
    sleep 1
    LEFT=$(lsof -ti "tcp:$PORT" 2>/dev/null | head -1)
    if [ -n "${LEFT:-}" ]; then
        kill -9 "$LEFT" 2>/dev/null && echo "force-stopped $LEFT"
    fi
fi
echo "port $PORT is clear"
