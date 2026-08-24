#!/usr/bin/env python3
"""
moth_engines.py  --  what can Moth's API actually DO?

Written against the real OpenAPI spec at https://api.mothquantum.com/docs.
That API is not a circuit runner: it executes named ENGINES, each with its own
params schema and input-file slots. So before writing any integration, the
question is which engines exist and whether any of them takes a quantum
circuit and gives back measurement results.

This asks. It only sends GETs, so it costs nothing and runs no jobs.

    export MOTH_API_TOKEN=your_key
    python3 moth_engines.py

    python3 moth_engines.py --engine <engine_id>    # full detail on one
"""

import os
import sys
import json
import urllib.request
import urllib.error

BASE  = os.environ.get("MOTH_API_URL", "https://api.mothquantum.com").rstrip("/")
TOKEN = os.environ.get("MOTH_API_TOKEN", "")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# words that would suggest an engine can run a circuit for us
CIRCUIT_WORDS = ("circuit", "qasm", "qiskit", "shots", "counts", "measure",
                 "qubit", "gate", "statevector", "backend", "sampler",
                 "expectation", "tomograph", "graph")


def get(path):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Accept": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:400]}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def looks_like_circuit(blob):
    low = json.dumps(blob).lower()
    return [w for w in CIRCUIT_WORDS if w in low]


def main():
    if not TOKEN:
        print("Set MOTH_API_TOKEN first.")
        return
    print("base:", BASE)
    print("token:", TOKEN[:4] + "..." + TOKEN[-4:])
    print()

    # ---- does the key authenticate at all? ----
    # The spec calls the bearer a "Supabase-issued access token", so it is
    # genuinely unclear whether an API key works directly here or has to be
    # exchanged for a JWT. This is the test.
    print("=" * 64)
    print("AUTH  GET /api/v1/me")
    print("=" * 64)
    code, body = get("/api/v1/me")
    print("  status:", code)
    print("  " + json.dumps(body, indent=2)[:500].replace("\n", "\n  "))
    if code == 401:
        print()
        print("  >>> 401. The API key is probably NOT usable as a bearer token")
        print("      directly -- the spec describes the bearer as a Supabase")
        print("      access token. Ask whether API keys go in a different")
        print("      header, or must be exchanged for a JWT first.")
    print()

    # ---- what engines exist? this is the whole question ----
    print("=" * 64)
    print("ENGINES  GET /api/v1/engines")
    print("=" * 64)
    code, body = get("/api/v1/engines")
    print("  status:", code)
    if code != 200:
        print("  " + json.dumps(body, indent=2)[:400].replace("\n", "\n  "))
        return

    engines = body.get("engines") or []
    print(f"  {body.get('count', len(engines))} engines\n")

    interesting = []
    for e in engines:
        eid = e.get("engine_id", "?")
        name = e.get("name", "")
        credits = e.get("credits_per_run", "?")
        free = " FREE" if credits in (0, "0") else f" {credits} credits"
        print(f"  {eid:28s} {str(name)[:26]:28s}{free}")
        desc = str(e.get("description", ""))[:100]
        if desc:
            print(f"      {desc}")
        hits = looks_like_circuit(e)
        if hits:
            interesting.append((eid, hits))
            print(f"      >>> mentions: {', '.join(hits)}")

    print()
    print("=" * 64)
    if interesting:
        print("CANDIDATES for running circuits:")
        for eid, hits in interesting:
            print(f"  {eid}  ({', '.join(hits)})")
        print()
        print("Get the full params schema for one with:")
        print(f"  python3 moth_engines.py --engine {interesting[0][0]}")
    else:
        print("NO engine mentions circuits, qubits, shots or counts.")
        print()
        print("That means this API cannot take a measurement for the game, and")
        print("we should stop trying to make it. The honest options are:")
        print("  1. use an engine that does something the game actually needs")
        print("     (asset generation, for instance) and keep measurements local")
        print("  2. ask whether a circuit-runner engine exists but is private")
        print("  3. reach the quantum hardware by a different route entirely")
    print("=" * 64)


def detail(engine_id):
    if not TOKEN:
        print("Set MOTH_API_TOKEN first.")
        return
    code, body = get("/api/v1/engines/" + engine_id)
    print("status:", code)
    print(json.dumps(body, indent=2)[:4000])


if __name__ == "__main__":
    if "--engine" in sys.argv:
        i = sys.argv.index("--engine")
        if i + 1 < len(sys.argv):
            detail(sys.argv[i + 1])
        else:
            print("give an engine id after --engine")
    else:
        main()
