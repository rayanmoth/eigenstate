#!/usr/bin/env bash
# Start the quantum brain, once. Idempotent and honest about it.
#
# Two callers, two behaviours:
#
#   ./start_server.sh            you, in a terminal. Waits for the port to
#                                answer and prints /health, so you know.
#   ./start_server.sh --nowait   the GAME, via execute_shell. Launches and
#                                returns immediately. The game does its own
#                                polling (the retry loop in Step), so a wait
#                                here would be at best redundant and at worst
#                                a 90-second freeze if execute_shell ever
#                                turns out to block on some platform.
#
# EVERYTHING THIS SCRIPT SAYS GOES INTO launch.log AS WELL AS STDOUT. When the
# game starts it, stdout goes nowhere -- so "check the log" has to actually be
# true, including for the failures that happen before the server exists.
set -u
cd "$(dirname "$0")" || exit 1

PORT=5055
SERVER=eigenstate_server.py
PIDFILE=.server.pid
LOG=server.log
LAUNCHLOG=launch.log

NOWAIT=0
[ "${1:-}" = "--nowait" ] && NOWAIT=1

say() { echo "$*"; echo "$(date '+%H:%M:%S') $*" >> "$LAUNCHLOG"; }

say "--- start_server.sh (nowait=$NOWAIT) ---"

if command -v lsof >/dev/null 2>&1; then
    EXISTING=$(lsof -ti "tcp:$PORT" 2>/dev/null | head -1)
    if [ -n "${EXISTING:-}" ]; then
        say "port $PORT is already held by pid $EXISTING"
        if [ "$NOWAIT" = "0" ]; then
            say "  it answers: $(curl -s "localhost:$PORT/health" || echo 'nothing')"
            say "  run ./stop_server.sh first if you want to replace it"
        fi
        exit 0
    fi
fi

# Pick an interpreter that actually has flask and qiskit, in preference order.
PY=""
for cand in "$HOME/eigenstate312/bin/python" \
            "$HOME/quantumfaction-env/bin/python" \
            "./venv/bin/python" \
            "$(command -v python3)"; do
    if [ -x "$cand" ] && "$cand" -c "import flask, qiskit" >/dev/null 2>&1; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    say "FAILED: no python with flask+qiskit found."
    say "  tried ~/eigenstate312, ~/quantumfaction-env, ./venv, python3 on PATH"
    say "  run ./setup.sh to build ./venv"
    exit 1
fi

# Credentials and mode, if present. Never printed.
[ -f moth.env ] && . ./moth.env

say "starting $SERVER with $PY"
nohup "$PY" "$SERVER" > "$LOG" 2>&1 &
echo $! > "$PIDFILE"
say "launched as pid $(cat "$PIDFILE"); output in $LOG"

if [ "$NOWAIT" = "1" ]; then
    exit 0
fi

# First boot imports qiskit and runs a tomography pass, so be patient.
for i in $(seq 1 90); do
    if curl -s "localhost:$PORT/health" >/dev/null 2>&1; then
        say "ready after ${i}s"
        curl -s "localhost:$PORT/health"; echo
        exit 0
    fi
    sleep 1
done
say "did not come up in 90s. Last lines of $LOG:"
tail -20 "$LOG"
exit 1