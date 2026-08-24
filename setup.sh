#!/usr/bin/env bash
# One-time setup. Builds ./venv with everything the server needs, so
# start_server.sh finds an interpreter without depending on whatever is
# lying around in your home directory.
#
# Run once:   ./setup.sh
# Then:       ./start_server.sh
set -eu
cd "$(dirname "$0")" || exit 1

echo "--- Eigenstate server setup ---"

PY3="$(command -v python3 || true)"
if [ -z "$PY3" ]; then
    echo "FAILED: no python3 on PATH. Install Python 3.11 or 3.12 first."
    exit 1
fi
echo "using $PY3 ($("$PY3" --version 2>&1))"

if [ ! -d venv ]; then
    echo "creating ./venv"
    "$PY3" -m venv venv
else
    echo "./venv already exists, reusing it"
fi

VPY=./venv/bin/python
"$VPY" -m pip install --upgrade pip >/dev/null

echo "installing flask and qiskit (this is the slow part, a few minutes)"
"$VPY" -m pip install flask qiskit qiskit-aer requests

# The three vendored libraries. MothQuantumGraph MUST come first on the
# path -- it shadows names in QuantumGraph, and installing them the other
# way round gives you a server that imports fine and then measures the
# wrong thing, which is far worse than an ImportError.
for lib in MothQuantumGraph pairwise-tomography QuantumGraph; do
    if [ -d "$lib" ]; then
        echo "installing vendored $lib"
        "$VPY" -m pip install -e "$lib"
    else
        echo "WARNING: $lib is not in this folder. The server will not start."
        echo "         Copy it in and re-run ./setup.sh."
    fi
done

echo
echo "checking the imports the server actually needs"
if "$VPY" -c "import flask, qiskit, quantumgraph, pairwise_tomography" 2>/dev/null; then
    echo "all four import cleanly."
    echo
    echo "Next:  ./start_server.sh"
else
    echo "something did not import. In order, check:"
    "$VPY" -c "import flask"                 2>&1 | tail -1
    "$VPY" -c "import qiskit"                2>&1 | tail -1
    "$VPY" -c "import quantumgraph"          2>&1 | tail -1
    "$VPY" -c "import pairwise_tomography"   2>&1 | tail -1
    exit 1
fi