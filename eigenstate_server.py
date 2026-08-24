"""
Eigenstate quantum brain, v8 -- commitments, interference, real staleness.

WHAT v8 ADDS OVER v7
1. COMMITMENTS. An oath applies its gates now and defers its measurement
   to a maturity month. In between it sits in the circuit where the whole
   world can rotate it. See the COMMITMENTS block below.
2. INTERFERENCE WITH AXES. Contributions to a measurement arrive as
   rotations about a chosen axis and compose in the circuit before the
   shot. Same axis adds or cancels exactly; different axes do neither,
   because they do not commute. Measured, on a fresh world, attacker
   p_win: baseline 0.51, +force 0.89, -force 0.115, +force then -force
   0.506 (exact cancellation), two half-forces 0.895 (exact addition).
3. HONEST ODDS. probability_of() reports the real probability of an
   outcome from shots on the same prepared circuit the deciding shot comes
   from, so the number shown to the player is the number the game obeys.
4. INTEL ACTUALLY GOES STALE. v7 returned intel_fresh=True
   unconditionally, so scouting bought nothing. /scout now refreshes a
   target and read_state reports known_* alongside live values.

WHY v7 WAS A REWRITE
v6 hand-rolled its own circuit bookkeeping. v7 uses QuantumGraph, the
package James Wootton wrote for the map-generation article this project
started from (github.com/qiskit-community/QuantumGraph). Three reasons
that is better, not just more authentic:

  1. FULL TWO-QUBIT TOMOGRAPHY. v6 read one number per pair (<ZZ>).
     QuantumGraph gives all nine two-qubit Paulis, which makes
     ASYMMETRIC relationships possible: <ZX> and <XZ> can differ, so one
     kingdom can have leverage over another rather than relationships
     only ever being mutual. Verified settable: ZX=+0.89 with XZ=-0.00.

  2. IT IS FASTER, done right. Naively it looks 3x slower because every
     setter re-runs tomography. With update=False on the setters and one
     update_tomography() at the end of a turn, a full turn is ~0.4s
     against v6's 0.32s -- and that is with far richer data.

  3. set_bloch / set_relationship express intent as a TARGET plus a
     fraction ("rotate this pair 55% of the way toward being aligned"),
     which is a much better fit for game actions than manually tracking
     rotation angles.

THE MECHANIC THAT FELL OUT OF THE PHYSICS
Connected correlation cannot exist between two qubits that are both
certain. Measured, with both qubits at |Z| and a ZZ target of +1:

     |Z| = 1.0  ->  connected ZZ = 0.00
     |Z| = 0.7  ->  connected ZZ = 0.09
     |Z| = 0.4  ->  connected ZZ = 0.53
     |Z| = 0.0  ->  connected ZZ = 0.95

So CONVICTION AND CONNECTION COMPETE. A kingdom absolutely sure of its
own mind has no room left to be bound to anyone; a kingdom in the balance
can be deeply entangled. Nothing in the game code enforces that. It is
what the mathematics does, and it is not something a classical system can
imitate honestly. Dispositions are therefore deliberately kept off the
poles (MOOD_CEILING) so relationships remain possible at all.

WHY THE CONNECTED CORRELATOR MATTERS (still)
get_relationship returns RAW Paulis. For two independent qubits <ZZ>
factorises into <Z><Z>, so two kingdoms that have never met report a
strong bond that is purely an artefact of their moods. Verified here:
raw ZZ = -0.59 where the product alone was -0.56, leaving a true
correlation of -0.03. Everything below reports connected values.
"""

import sys, os, math, threading, time, json, copy, traceback
from flask import Flask, request, jsonify

# QuantumGraph is not on PyPI, so it ships in a folder next to this file.
#
# Resolve that folder from the SCRIPT's location, not the working directory.
# The first version used a bare "./QuantumGraph", which only worked if you
# happened to run the server from the exact folder containing it.
_HERE = os.path.dirname(os.path.abspath(__file__))
# v9: Moth's fork first if it is present, then the qiskit-community one, then
# this directory. Moth's fork is API-identical and needs no qiskit-ibm-runtime.
# NB: build the list once and insert in order. Repeated sys.path.insert(0, ...)
# in a loop REVERSES your preference order, which silently loaded the wrong
# fork the first time I wrote this.
_SEARCH = ["MothQuantumGraph", "pairwise-tomography", "QuantumGraph"]
sys.path[:0] = [os.path.join(_HERE, c) for c in _SEARCH] + [_HERE]

try:
    from quantumgraph import QuantumGraph
    import quantumgraph as _qgmod
except ModuleNotFoundError as e:
    # NB: this catches a missing module ANYWHERE in the import chain, not
    # just quantumgraph itself. quantumgraph imports qiskit_experiments and
    # qiskit_ibm_runtime, so a missing dependency used to be reported as
    # "could not import quantumgraph", which sent you hunting for the wrong
    # problem entirely. Report the module Python actually could not find.
    missing = getattr(e, "name", "") or "quantumgraph"
    print(f"\n  Missing module: {missing}\n")

    if missing != "quantumgraph":
        print("  quantumgraph itself was found; one of its dependencies was not.")
        print("  Note qiskit-ibm-runtime is NOT listed in QuantumGraph's own")
        print("  requirements.txt even though the code imports it.\n")
        print("  Fix, with your venv active:")
        print("    pip install qiskit-experiments qiskit-ibm-runtime scipy\n")
    else:
        print("  Looked in:")
        print("    " + os.path.join(_HERE, "QuantumGraph"))
        print("    " + _HERE)
        found = os.path.isdir(os.path.join(_HERE, "QuantumGraph"))
        print(f"\n  QuantumGraph folder next to this script: "
              f"{'yes' if found else 'NO -- that is the problem'}")
        if found:
            inner = os.path.join(_HERE, "QuantumGraph", "quantumgraph")
            print(f"  inner quantumgraph package present:      "
                  f"{'yes' if os.path.isdir(inner) else 'NO -- unzipped one level too deep?'}")
        print("\n  Expected layout:")
        print("    <somewhere>/eigenstate_server.py")
        print("    <somewhere>/QuantumGraph/quantumgraph/__init__.py\n")

    # DO NOT sys.exit HERE. On a terminal that prints the diagnostic above
    # and stops, which is what you want. On a serverless host it kills the
    # worker during module import and the platform reports only
    # FUNCTION_INVOCATION_FAILED with no cause -- the diagnostic is written
    # to a stdout nobody reads. Raising puts the same text in the traceback,
    # which does reach the log.
    raise ImportError(
        f"eigenstate: could not import {missing}. "
        f"Looked for the vendored libraries in {_HERE}: "
        + ", ".join(f"{c}={'yes' if os.path.isdir(os.path.join(_HERE, c)) else 'MISSING'}"
                    for c in _SEARCH)
        + ". If they are all MISSING in a deployed build, they were not "
        "included in the bundle -- most often because each one is its own "
        "git clone and git recorded it as an empty submodule."
    )

from qiskit import transpile
from qiskit.circuit import ClassicalRegister
from qiskit_aer import AerSimulator

from eigenstate_backend import (make_backend, graph_settings,
                                 mode as backend_mode, hw_mode, hw_timeout,
                                 hardware_status, reset_hardware,
                                 set_credentials, clear_credentials,
                                 load_saved_credentials, token_present,
                                 token_masked, graph_engine, GraphEngine)

app = Flask(__name__)
lock = threading.Lock()


@app.route("/", methods=["GET"])
def _root():
    """A front door. Hitting the bare domain in a browser used to return a
    404, which looks broken to anyone who did not write this -- including
    you, in a demo, with someone watching."""
    return jsonify({
        "service": "Eigenstate quantum brain",
        "version": "clean-3",
        "stateless": True,
        "what": "The world state lives in the client. Every endpoint takes a "
                "world and returns one, so this server remembers nothing "
                "between requests and any number of players can share it.",
        "endpoints": {
            "GET  /health":  "liveness, version, backend",
            "POST /newgame": "measure a new world into being",
            "POST /turn":    "advance a month",
            "POST /resolve": "measure battles and initiative",
            "POST /scout":   "refresh intel on one court",
            "POST /observe":  "full pairwise tomography",
            "GET  /hardware": "what ran where",
        },
        "game": "GameMaker desktop client, not served from here",
    })


@app.errorhandler(404)
def _no_route(e):
    """Say WHICH path we were handed, and what we would have accepted.

    A platform rewrite that hands Flask the wrong path produces a 404 on
    every endpoint, which is indistinguishable from a code bug if all you
    get is Werkzeug's default page. This makes the difference visible in one
    request: if `path` below is not the path you asked for, it is routing,
    not code.
    """
    return jsonify({
        "error": "no such route",
        "path": request.path,
        "full_path": request.full_path,
        "script_root": request.script_root,
        "url": request.url,
        "method": request.method,
        "query": dict(request.args),
        "known_routes": sorted(
            r.rule for r in app.url_map.iter_rules()
            if not r.rule.startswith("/static")),
        "version": "clean-3",
    }), 404


@app.errorhandler(Exception)
def _blew_up(e):
    """Hand the caller the traceback instead of Werkzeug's beige 500 page.

    Chasing a 500 across a hosted server by guessing is miserable and slow:
    the client sees "Internal Server Error", the platform's log is a
    separate tab, and the loop between a hypothesis and a test is minutes
    long. This makes the failing line visible in the response the client
    already prints, which collapses that loop to one run.

    It also prints to stdout, so it lands in the platform log even when
    nobody is looking at the client.

    HTTPException subclasses are re-raised: 404 and the like have their own
    handlers and are not crashes.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e

    tb = traceback.format_exc()
    print("=== UNHANDLED ===\n" + tb, flush=True)

    if os.environ.get("EIGENSTATE_TRACEBACKS", "1") != "1":
        return jsonify({"error": "internal error"}), 500

    return jsonify({
        "error": type(e).__name__,
        "detail": str(e),
        "where": request.path,
        "traceback": tb.splitlines()[-14:],
        "version": "clean-3",
    }), 500


@app.errorhandler(ValueError)
def _bad_world(e):
    """A malformed world blob is the client's fault, not ours. 400 with the
    reason, rather than a 500 that looks like the server fell over. The
    client is authoritative over world state now, so everything arriving
    here is untrusted input and has to fail legibly."""
    return jsonify({"error": "bad request", "detail": str(e)}), 400


@app.after_request
def _cors(resp):
    """Needed the moment the game is served from a different origin than the
    server -- which is every hosted deployment, and every HTML5 export. A
    browser blocks the request outright without these, and it fails looking
    exactly like the server being down, which is an afternoon nobody needs.

    Wide open on purpose: there is nothing here to protect. No accounts, no
    stored data, and the credit guard is server-side.
    """
    resp.headers["Access-Control-Allow-Origin"]  = os.environ.get(
        "EIGENSTATE_CORS_ORIGIN", "*")
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Max-Age"]       = "86400"
    return resp


# No explicit OPTIONS route: Flask answers the preflight automatically for
# every registered rule, and the after_request hook above attaches the CORS
# headers to that answer too. A hand-written catch-all was redundant.

# THE BACKEND SEAM. Everything about which machine runs the circuits lives in
# eigenstate_backend.py, so this file never sees a credential.
BACKEND  = make_backend()
SETTINGS = graph_settings()

# DECIDING SHOTS are routed per measurement, not per session. That is the
# whole toggle: the world is always read locally and fast, and an individual
# battle or oath maturity can be sent to a real device on demand.
#
# hw_enabled / hw_scope are RUNTIME state, flipped by POST /hardware, so the
# game can offer it as a keypress rather than a restart.
# A key saved from a previous session, if the player asked us to remember it.
load_saved_credentials()

hw_enabled = (hw_mode() not in ("off", "", "none"))

def _default_scope():
    """Only one thing can go to hardware, so this is nearly a constant.

    Kept as a function because EIGENSTATE_HW_SCOPE=off is a useful thing for a
    demo to be able to set without editing code.
    """
    want = (os.environ.get("EIGENSTATE_HW_SCOPE") or "").strip()
    return want if want in ("oaths", "off", "world") else "oaths"


hw_scope   = _default_scope()

hw_log     = []             # what actually ran where, for the Observatory

# ---- THE SPEND GUARD -------------------------------------------------
# A hard ceiling on how many jobs this process will ever send to a real
# device. Not a warning, not a log line -- a refusal.
#
# The reason it exists: the router below can send a shot per battle, and a
# 24-month game with an aggressive scope setting can be dozens of jobs. One
# bad loop, one forgotten scope="all", and a credit allocation is gone. So
# the default is deliberately small, and hitting it degrades to local play
# rather than stopping the game.
#
# Emulation is free, so this only needs to bind on paid QPU -- but it counts
# every device job regardless, because "which device am I on" is exactly the
# thing you can get wrong.
HW_BUDGET   = int(os.environ.get("EIGENSTATE_HW_BUDGET", "25"))
hw_jobs_used  = 0
hw_shots_used = 0


def hw_budget_left():
    return max(0, HW_BUDGET - hw_jobs_used)


def hw_budget_report():
    return {"limit": HW_BUDGET, "used": hw_jobs_used,
            "left": hw_budget_left(), "shots": hw_shots_used}


def want_hardware(kind):
    """Should THIS measurement go to the engine?

    Three scopes now:

      off     nothing leaves the machine.
      oaths   the deferred oath measurement only. One round trip a month at
              most, and only in months where something matures.
      world   oaths, PLUS one read of the whole world per month, whose
              tomography becomes the numbers the player is shown. Every bond,
              mood and leverage figure on screen then came off the engine
              rather than off the local simulation.

    Still never: battles and initiative. A battle is composed rotations at
    specific angles where the ORDER is the mechanic, and this engine takes
    state targets. "Rotate q2 by 0.4 about Y then -0.3 about X" is not
    expressible as "set the ZZ correlation to 0.6", so sending it would be a
    lie about what was computed. Initiative is asked every single month and a
    round trip per turn is not a game.
    """
    if not hw_enabled:
        return False
    if kind == "oath":
        return hw_scope in ("oaths", "world")
    if kind == "world":
        return hw_scope == "world"
    return False


def run_counts(circ, shots, hardware=False, kind=""):
    """Execute a circuit locally and return (counts, where, fell_back, reason).

    ALWAYS LOCAL, and the signature keeps its `hardware` argument only so the
    call sites read the same. graph-v1 takes state TARGETS (set_bloch,
    set_relationship), not raw gates, and a battle IS raw gates: composed
    rotations whose ORDER is the mechanic. There is no honest way to express
    that as a target, so battles are decided here and say so.

    What does go to hardware is the oath, via world_ops() -- the full world
    state, which the engine can express exactly.
    """
    sam = BACKEND if hasattr(BACKEND, "run") else _aer()
    counts = sam.run(transpile(circ, sam), shots=shots).result().get_counts()
    return counts, "local", False, ""


_aer_cached = None


def _aer():
    """A sampler for when the graph backend is ExpectationValue, which reads
    states but cannot take a shot."""
    global _aer_cached
    if _aer_cached is None:
        _aer_cached = AerSimulator()
    return _aer_cached


N = 5                    # kingdoms; 0 is the player
SHOTS = SETTINGS["shots"] or 2000
REBUILD_PASSES = SETTINGS["rebuild_passes"]
MOOD_CEILING = 0.50      # NOT just "below 1". Measured: at |Z|=0.7 a pair
                         # can only reach connected ZZ of 0.09, so a mood
                         # ceiling of 0.75 meant "invaded" hardened a
                         # kingdom so far that it destroyed the war bond the
                         # same event was creating. 0.50 leaves room for
                         # both conviction and connection to coexist.
BOND_STEP = 0.45         # how far one act rotates a relationship
MOOD_STEP = 0.40

qg = None
current_year = 1
gate_log = []            # human-readable record of this year's quantum ops

# THE WORLD IS HELD AS INTENT AND REBUILT EACH TURN.
#
# The first version let QuantumGraph's circuit accumulate every gate ever
# applied. Over a 20-year game that took circuit depth from 12 to 167 and
# turn time from 0.83s to 2.53s, and worse, every kingdom drifted into
# being entangled with every other -- independence collapsed to 0.1 and
# all moods washed out to near zero. The world turned to mush.
#
# So the authoritative state is these three classical tables, and the
# graph is rebuilt from them at the start of every turn. Depth is then
# constant, the dynamics are controllable, and the physics is no less real
# for being re-prepared rather than grown: the circuit, the entanglement
# and the measurements are all still genuine.
mood   = [0.0] * N                      # Z we steer each kingdom toward
bond   = [[0.0] * N for _ in range(N)]  # signed alignment, -1..1
lev    = [[0.0] * N for _ in range(N)]  # asymmetric sway, lev[a][b]

BOND_DECAY = 0.90        # relationships fade unless maintained
LEV_DECAY  = 0.85
MAX_BOND_TOTAL = 1.25    # monogamy: a finite budget of correlation each


# ======================================================================
# COMMITMENTS -- the v8 addition.
#
# Every act in v7 was applied and measured inside the same turn. A
# commitment instead applies its gates NOW and defers its measurement to
# a maturity month. In between it sits in superposition: it tugs the
# world (so it is visible and it eats into the correlation budget),
# decay() erodes it, and anybody -- you or a rival -- can rotate it.
# What gets measured at maturity is the state the whole world left it in,
# not the state you swore it in.
#
# WHY AXES. Each contribution is a rotation about a chosen axis, and
# rotations about different axes DO NOT COMMUTE. That is not flavour, it
# is the rotation group, and it gives three genuinely different kinds of
# pressure:
#
#   "Y" force       Ry -- moves the odds directly.
#   "X" persuasion  Rx -- also moves the odds, but in another plane, so
#                         it does not simply add to or cancel force.
#   "Z" coercion    Rz -- changes NOTHING on its own. Rz commutes with a
#                         Z-basis measurement, so leverage by itself
#                         decides no outcome. What it does is rotate the
#                         plane, which changes what a LATER Y or X
#                         contribution is worth. Coercion is setup.
#
# That last one is the piece no classical model can honestly imitate: an
# act that provably cannot change any outcome alone, and changes every
# other act's value in combination.
# ======================================================================
COMMIT_KINDS = {
    # kind        target bond sign, base angle, default span (months)
    "pact":      {"sign": +1, "angle": 0.55, "span": 3},
    "betrothal": {"sign": +1, "angle": 0.80, "span": 6},
    "siege":     {"sign": -1, "angle": 0.70, "span": 2},
    "embargo":   {"sign": -1, "angle": 0.45, "span": 4},
}
SPAN_TUG        = 0.65   # how much of its strength an open commitment
                         # already exerts on the world at full progress
COMMIT_DECAY    = 0.94   # an unattended commitment loses conviction
PRESSURE_DECAY  = 0.88
P_SHOTS         = 512    # shots for the live "how likely is this to
                         # seal" readout the board shows
INTEL_TTL       = 5      # months a scout's report stays fresh

pending  = []            # open commitments; see commit_open()
next_uid = 1
matured_log = []         # what matured this turn, returned to the client

# last-known snapshot per kingdom, so stale intel can be reported as
# stale rather than the client being handed the truth and asked to
# pretend it does not have it
known       = [None] * N
last_seen   = [0] * N


# ----------------------------------------------------------------------
def commit_progress(c):
    """0..1 -- how far through its span a commitment is."""
    span = max(1, c["matures"] - c["sworn"])
    return max(0.0, min(1.0, (current_year - c["sworn"]) / span))


def commit_tilt(c):
    """Net signed pressure on a commitment. Positive helps it seal."""
    return sum(p["amount"] for p in c["pressure"])


def effective_bonds():
    """bond[] plus the tug of every open commitment, then re-clipped to the
    monogamy budget.

    This is the whole reason commitments feel expensive. An open pact eats
    into the same finite correlation budget a settled alliance does, so
    binding somebody hard enough genuinely leaves them no capacity to bond
    with anyone else -- including with the people who would otherwise
    gang up on you."""
    eff = [row[:] for row in bond]
    for c in pending:
        a, b = c["a"], c["b"]
        sign = COMMIT_KINDS[c["kind"]]["sign"]
        tug = sign * c["strength"] * commit_progress(c) * SPAN_TUG
        tug += 0.25 * commit_tilt(c) * sign
        eff[a][b] = max(-1.0, min(1.0, eff[a][b] + tug))
        eff[b][a] = eff[a][b]
    for i in range(N):
        tot = sum(abs(eff[i][j]) for j in range(N) if j != i)
        if tot > MAX_BOND_TOTAL:
            s = MAX_BOND_TOTAL / tot
            for j in range(N):
                if j != i:
                    eff[i][j] = eff[j][i] = eff[i][j] * s
    return eff


def enforce_monogamy():
    """A finite budget of correlation per kingdom, so nobody ends up bound to
    everybody. Without this, independence collapsed to 0.1 over 20 years and
    every kingdom lost its own character.

    THIS IS THE BEST MECHANIC IN THE GAME AND IT USED TO BE SILENT. Going over
    budget does not REJECT the new bond, it proportionally dilutes every bond
    that kingdom already had. So binding someone close genuinely takes them
    away from everyone else -- you did not attack their alliance, you crowded
    it out. Nothing told the player that was happening, so this now returns a
    report and /turn ships it.

    ALSO FIXED: the old version applied each kingdom's rescale inside the
    ascending i loop while writing bond[j][i], so kingdom 0's rescale changed
    kingdom 3's total before kingdom 3 was examined. Low indices were
    privileged and the result depended on an arbitrary ordering. Now every
    scale is computed from the SAME pre-state, and a shared pair takes the
    tighter of the two constraints -- a bond cannot exceed either party's
    budget, so min() is the principled choice, not an approximation.
    """
    scales = [1.0] * N
    totals = [0.0] * N
    for i in range(N):
        tot = sum(abs(bond[i][j]) for j in range(N) if j != i)
        totals[i] = tot
        if tot > MAX_BOND_TOTAL:
            scales[i] = MAX_BOND_TOTAL / tot

    diluted = []            # one entry per pair that actually shrank
    for i in range(N):
        for j in range(i + 1, N):
            s = min(scales[i], scales[j])
            if s >= 0.999:
                continue
            before = bond[i][j]
            if abs(before) < 0.03:
                continue
            after = before * s
            bond[i][j] = bond[j][i] = after
            # `crowded_by` is whichever party ran out of room, which is the
            # one the player wants named in the log line
            crowder = i if scales[i] <= scales[j] else j
            diluted.append({
                "a": i, "b": j,
                "before": round(before, 3),
                "after": round(after, 3),
                "scale": round(s, 3),
                "over": crowder,
                "over_total": round(totals[crowder], 3),
            })
    return diluted


def bond_commitments(i):
    """How kingdom i has spent itself, per partner, including open oaths.

    Reads effective_bonds so an unresolved commitment shows up as spent
    capacity -- which it is, in the prepared circuit -- rather than appearing
    free until it matures.
    """
    eff = effective_bonds()
    out = []
    for j in range(N):
        if j == i:
            continue
        v = eff[i][j]
        if abs(v) < 0.03:
            continue
        # "who", not "with": `with` is a reserved keyword in GML and the
        # client cannot write _cm.with at all.
        out.append({"who": j, "amount": round(abs(v), 3),
                    "sign": 1 if v > 0 else -1})
    out.sort(key=lambda d: -d["amount"])
    return out


def decay():
    for i in range(N):
        for j in range(N):
            if i == j: continue
            bond[i][j] *= BOND_DECAY
            lev[i][j]  *= LEV_DECAY
            deb[i][j]  *= DEB_DECAY
            if abs(bond[i][j]) < 0.03: bond[i][j] = 0.0
            if abs(lev[i][j])  < 0.03: lev[i][j]  = 0.0
            if abs(deb[i][j])  < 0.03: deb[i][j]  = 0.0


def rebuild():
    """Re-prepare the graph from the intent tables. Two tomography passes,
    because set_relationship computes its rotation from the CURRENT numbers
    and the second pass therefore lands targets far more accurately."""
    global qg
    qg = QuantumGraph(N, backend=BACKEND)
    for i in range(N):
        # hostility -> Bloch Z, scaled so |Z| never reaches 1 and there is
        # always room left for correlation
        z = -mood[i] * MOOD_CEILING
        x = math.sqrt(max(0.0, 1.0 - z * z))
        qg.set_bloch({"X": x, "Y": 0.0, "Z": z}, i, update=False)
    qg.update_tomography(shots=SHOTS)

    # v8: the graph is prepared from bond PLUS open commitments, so an
    # unresolved pact is already a real correlation in the circuit rather
    # than a note in a list. That is what makes it interferable.
    eff = effective_bonds()
    for _ in range(REBUILD_PASSES):
        for a in range(N):
            for b in range(a + 1, N):
                if abs(eff[a][b]) > 0.03:
                    qg.set_relationship({"ZZ": 1 if eff[a][b] > 0 else -1},
                                        a, b, fraction=min(0.95, abs(eff[a][b])),
                                        update=False)
                if abs(lev[a][b]) > 0.03:
                    p = "ZX" if lev[a][b] > 0 else "XZ"
                    qg.set_relationship({p: 1}, a, b,
                                        fraction=min(0.9, abs(lev[a][b])),
                                        update=False)
                # the second, independent channel
                if abs(deb[a][b]) > 0.03:
                    p = "XY" if deb[a][b] > 0 else "YX"
                    qg.set_relationship({p: 1}, a, b,
                                        fraction=min(0.9, abs(deb[a][b])),
                                        update=False)
        qg.update_tomography(shots=SHOTS)


def fresh_graph():
    global qg, mood, bond, lev, deb, gate_log, pending, next_uid, matured_log
    global known, last_seen
    mood = [0.0] * N
    bond = [[0.0] * N for _ in range(N)]
    lev  = [[0.0] * N for _ in range(N)]
    deb  = [[0.0] * N for _ in range(N)]
    gate_log = []
    pending = []
    next_uid = 1
    matured_log = []
    known = [None] * N
    last_seen = [0] * N
    qg = QuantumGraph(N, backend=BACKEND)
    # Every kingdom starts on the equator: undecided, and therefore
    # CAPABLE of being bound to others. Starting at a pole would make the
    # entire relationship layer inert.
    rebuild()


# =====================================================================
# THE WORLD AS DATA
#
# Why this exists: the module-level tables above are fine as scratch space
# but they must not PERSIST between requests. On a serverless host there is
# no guarantee that a player's second request reaches the same instance as
# their first, and no guarantee that two players do not share one instance.
# Either way the globals are wrong: a kingdom vanishes mid-run, or two
# people play the same game without knowing.
#
# The fix is smaller than it looks, because rebuild() already reconstructs
# the entire quantum state from these tables on every measurement. The
# QuantumGraph is a scratchpad, not a memory. So the whole world is under a
# hundred numbers, and the server can be a pure function: take the world
# in, hand the world back.
#
# BACKWARD COMPATIBLE ON PURPOSE. If a request carries no "world" key the
# globals are left exactly as they are and the server behaves as it always
# has. That means an unmodified client keeps working, and the stateless
# path can be adopted one endpoint at a time.
#
# Deliberately NOT in here: hw_jobs_used, hw_shots_used, hw_log, HW_BUDGET.
# Those are the credit guard, and they have to be server-wide or a client
# could reset its own budget by sending a fresh world.
# =====================================================================

WORLD_FIELDS = ("current_year", "mood", "bond", "lev", "deb",
                "pending", "next_uid", "known", "last_seen", "gate_log")

_qg_key = None      # the world the current qg was built from


def _world_key():
    """A cheap stable fingerprint of everything rebuild() reads. Rounded,
    because float noise well below 0.03 cannot change the circuit -- every
    use site thresholds at 0.03 -- and we do not want a rebuild for it."""
    eff = effective_bonds()
    return json.dumps([
        [round(v, 4) for v in mood],
        [[round(v, 4) for v in row] for row in eff],
        [[round(v, 4) for v in row] for row in lev],
        [[round(v, 4) for v in row] for row in deb],
    ], separators=(",", ":"))


def world_export():
    """The world as a JSON-safe dict, to hand back to the client."""
    return {
        "current_year": current_year,
        "mood":      [round(v, 6) for v in mood],
        "bond":      [[round(v, 6) for v in r] for r in bond],
        "lev":       [[round(v, 6) for v in r] for r in lev],
        "deb":       [[round(v, 6) for v in r] for r in deb],
        "pending":   copy.deepcopy(pending),
        "next_uid":  next_uid,
        "known":     copy.deepcopy(known),
        "last_seen": list(last_seen),
        "gate_log":  list(gate_log),
        # THE ENGINE'S READ OF THE WORLD, if there was one. Travels with the
        # world so /scout and /observe report the same numbers the last /turn
        # measured, instead of quietly falling back to the local simulation
        # and showing figures that disagree by a few percent.
        "tomo":      engine_view.as_dict() if engine_view is not None else None,
        "v": 1,
    }


def _grid(src, name):
    """Validate an N x N float grid out of untrusted JSON. The client is
    authoritative over the world now, so everything arriving here is
    untrusted input and a malformed blob must give a 400 rather than an
    IndexError three functions deeper."""
    if not isinstance(src, list) or len(src) != N:
        raise ValueError(f"world.{name} must be a {N}x{N} array")
    out = []
    for row in src:
        if not isinstance(row, list) or len(row) != N:
            raise ValueError(f"world.{name} must be a {N}x{N} array")
        out.append([max(-1.0, min(1.0, float(x))) for x in row])
    return out


# ONE SHOT, not 256. The tomography graph-v1 returns is computed exactly --
# the probe came back with values like 3.4e-16, which no sampled estimator
# produces -- so shots only affect the measurements histogram, which the world
# read does not use. Paying for 256 of them bought nothing but latency.
WORLD_SHOTS = int(os.environ.get("EIGENSTATE_WORLD_SHOTS", "1"))

# Read the world off the engine every Nth month rather than every month. 1 is
# every month; 2 halves the round trips for numbers that move slowly anyway.
# The months in between show the local simulation, which agrees to within a
# few percent -- so this is a real honesty/latency dial and the gate log always
# says which kind of month you are looking at.
WORLD_EVERY = max(1, int(os.environ.get("EIGENSTATE_WORLD_EVERY", "1")))


def sync_world_from_engine():
    """Send the whole world to Moth's engine and read the state back.

    This is what makes "every kingdom is a qubit on the engine" literally
    true rather than nearly true. world_ops() already serialises the entire
    world -- every mood as a Bloch vector, every bond as a signed ZZ,
    leverage as ZX/XZ, debt as XY/YX, with open oaths folded in -- and it
    builds that purely from the intent tables, never from the local graph.
    The engine returns full tomography. So the only thing that was ever
    missing was reading the answer back instead of reading the simulation.

    ONE CALL PER MONTH, deliberately. The round trip is seconds, and doing
    this per readout would put a turn well past what anyone will sit through.
    Called where rebuild() already runs, so the local graph stays available
    for the things that genuinely cannot travel: battle odds and the deciding
    shot, which are composed rotations rather than state targets.

    NEVER FATAL. If the engine is slow, rate-limited, or down, the local
    simulation is a correct fallback and the month continues. A network hiccup
    must not cost the player their turn, so every failure path here ends in
    "carry on locally" plus a line in the gate log saying so.
    """
    global engine_view

    if not want_hardware("world"):
        engine_view = None
        return None

    if WORLD_EVERY > 1 and (current_year % WORLD_EVERY) != 0:
        engine_view = None
        log_gate(f"month {current_year}: local read "
                 f"(engine every {WORLD_EVERY} months)")
        return None

    try:
        meta = graph_engine().measure(world_ops(), N, shots=WORLD_SHOTS)
    except Exception as e:
        engine_view = None
        log_gate(f"world read fell back to local ({type(e).__name__})")
        return None

    v = EngineView(meta.get("tomography"))
    if not v.ok():
        engine_view = None
        log_gate("world read fell back to local (engine sent no tomography)")
        return None

    engine_view = v
    log_gate(f"world measured on {graph_engine().where(meta)} "
             f"in {meta.get('wall_s')}s -- every number below is from there")
    return meta


def _commit_clean(c):
    """Coerce one commitment out of untrusted JSON.

    THE BUG THIS EXISTS FOR. GameMaker has no integer type -- every number
    is a double -- so a commitment stored here as {"a": 2} comes back from
    the client as {"a": 2.0}. Python carries that happily until something
    does eff[a][b] with it, and then you get "list indices must be integers
    or slices, not float" raised from effective_bonds, four calls away from
    the actual mistake.

    The bond grids were validated on import; the commitments were not, so
    the first month with an open oath on the board took the server down. Any
    number here that ends up indexing a list or a qubit has to be an int at
    the boundary, not wherever it happens to be used.
    """
    if not isinstance(c, dict) or "uid" not in c:
        raise ValueError("world.pending entries must be commitment objects")

    kind = str(c.get("kind", "pact"))
    if kind not in COMMIT_KINDS:
        kind = "pact"
    axis = c.get("axis")

    out = {
        "uid":      int(c.get("uid", 0)),
        "kind":     kind,
        "a":        int(c.get("a", 0)),
        "b":        int(c.get("b", 0)),
        "axis":     axis if axis in ("X", "Y", "Z") else "Y",
        "strength": float(c.get("strength", 0.5)),
        "sworn":    int(c.get("sworn", 1)),
        "matures":  int(c.get("matures", 2)),
        "owner":    int(c.get("owner", 0)),
        "pressure": [],
    }

    for f in ("a", "b", "owner"):
        if not 0 <= out[f] < N:
            raise ValueError(f"world.pending: {f}={out[f]} is not a kingdom")

    for pr in (c.get("pressure") or []):
        if not isinstance(pr, dict):
            continue
        _on = pr.get("on")
        _ax = pr.get("axis")
        # `on` is used directly as a qubit index in the rotations list, so
        # it gets the same treatment as a and b above.
        _on = None if _on is None else int(_on)
        if _on is not None and not 0 <= _on < N:
            raise ValueError(f"world.pending: pressure on={_on} is not a kingdom")
        out["pressure"].append({
            "by":     int(pr.get("by", 0)),
            "axis":   _ax if _ax in ("X", "Y", "Z") else "Y",
            "amount": float(pr.get("amount", 0.0)),
            "on":     _on,
        })

    return out


def world_import(d):
    """Load a world sent by the client. Returns True if it took one.

    Rebuilds the graph only when the incoming world differs from whatever
    the cached qg was built from. On one instance serving one player's
    sequential turns that hits nearly every time, so the common case costs
    a hash rather than two tomography passes."""
    global current_year, mood, bond, lev, deb
    global pending, next_uid, known, last_seen, gate_log, qg, _qg_key
    global engine_view

    if not isinstance(d, dict):
        return False

    # VALIDATE INTO LOCALS FIRST, ASSIGN GLOBALS ONLY ONCE EVERYTHING PASSES.
    #
    # This ordering is not style. The first version assigned as it went and
    # raised on the length check afterwards, which left mood as a 2-element
    # list on the module. The request that raised got its 400, and then the
    # NEXT request on that instance died with an IndexError from somewhere
    # unrelated. On a shared instance one client's bad payload broke a
    # different client's game. Found by tools/test_stateless.py, which is
    # the entire argument for having written it.
    _mood = [max(-1.0, min(1.0, float(v))) for v in d.get("mood", [0.0] * N)]
    if len(_mood) != N:
        raise ValueError(f"world.mood must have {N} entries")
    _bond = _grid(d.get("bond", [[0.0] * N for _ in range(N)]), "bond")
    _lev  = _grid(d.get("lev",  [[0.0] * N for _ in range(N)]), "lev")
    _deb  = _grid(d.get("deb",  [[0.0] * N for _ in range(N)]), "deb")

    _raw_pending = d.get("pending") or []
    if not isinstance(_raw_pending, list):
        raise ValueError("world.pending must be an array")
    _pending   = [_commit_clean(c) for c in _raw_pending]
    _next_uid  = int(d.get("next_uid", 1))
    _known     = copy.deepcopy(d.get("known", [None] * N)) or [None] * N
    _last_seen = [int(v) for v in d.get("last_seen", [0] * N)]
    _gate_log  = list(d.get("gate_log", []))
    _year      = int(d.get("current_year", current_year))

    if len(_known) != N:
        raise ValueError(f"world.known must have {N} entries")
    if len(_last_seen) != N:
        raise ValueError(f"world.last_seen must have {N} entries")
    # nothing below here can fail, so the module state is never half-loaded
    mood, bond, lev, deb = _mood, _bond, _lev, _deb
    pending, next_uid = _pending, _next_uid
    known, last_seen, gate_log = _known, _last_seen, _gate_log
    current_year = _year

    # Restore the engine's view if this world carries one, and CLEAR it
    # otherwise. Clearing is the load-bearing half: engine_view is module
    # state, so without this a view built for one player's world would be
    # used to answer the next request, whoever it belongs to.
    _t = d.get("tomo")
    _v = EngineView(_t) if isinstance(_t, dict) else None
    engine_view = _v if (_v is not None and _v.ok()) else None

    key = _world_key()
    if qg is None or _qg_key != key:
        rebuild()
        _qg_key = key
    return True


def world_seal():
    """Call after all mutation in a handler. Records which world the qg now
    corresponds to, so the next request can skip the rebuild."""
    global _qg_key
    _qg_key = _world_key()


def world_load(data):
    """Endpoint helper: pull the world out of a request body if it sent one.

    Raising ValueError here surfaces as a 400 via the handler, which is the
    right answer for a malformed blob -- silently falling back to whatever
    the instance happened to hold last is how you end up serving somebody
    else's kingdom."""
    if not isinstance(data, dict):
        return False
    return world_import(data.get("world")) if "world" in data else False


def log_gate(text):
    gate_log.append(text)


def set_mood(i, delta, why):
    """mood[] IS hostility: positive = hostile. The Bloch Z is its negative,
    because hostility is read as -Z.

    SIGN BUG FIXED HERE. mood[] used to be stored as Z directly while every
    caller passed "+delta to make them angrier", so every hostile event was
    quietly making kingdoms peaceful. Over 20 years the whole world drifted
    to content and nothing ever fought."""
    mood[i] = max(-1.0, min(1.0, mood[i] + delta))
    log_gate(f"set_bloch(q{i} -> Z={-mood[i]*MOOD_CEILING:+.2f}, "
             f"hostility {mood[i]:+.2f})  {why}")


def set_bond(a, b, target, fraction, why, pauli="ZZ"):
    """Move a pair toward aligned (+1) or opposed (-1) by `fraction`."""
    cur = bond[a][b]
    bond[a][b] = bond[b][a] = max(-1.0, min(1.0, cur + (target - cur) * fraction))
    log_gate(f"set_relationship(q{min(a,b)},q{max(a,b)} -> ZZ={target:+d}, "
             f"{int(fraction*100)}%)  {why}")


# ======================================================================
# THE NINE CORRELATORS  --  v12.
#
# QuantumGraph's whole reason for existing is that it gives all nine
# two-qubit Pauli correlators per pair, not one number. Up to v11 this game
# used three of them: ZZ for bonds, ZX/XZ for leverage. Two thirds of the
# instrument was idle.
#
# Measured on Moth's fork, on this world, at fraction 0.9:
#
#   set ZX=1 -> ZX=+0.988  XZ=-0.026   asymmetry +1.014   <- asymmetric
#   set XY=1 -> XY=+0.986  YX=-0.039   asymmetry +1.024   <- asymmetric
#   set ZY=1 -> ZY=+0.986  YZ=+0.990   asymmetry -0.004   <- SYMMETRIC, useless
#
# So there are exactly TWO independent "who leads whom" channels, not one.
# And setting ZX left the connected ZZ at +0.008, so leverage is genuinely
# INDEPENDENT of alliance: you can hold sway over a kingdom you have no bond
# with at all.
#
# WHAT DOES NOT WORK, tested and abandoned: a coordination hidden in another
# basis. Asking for {"YY": 1, "ZZ": 0} yields YY=+0.78 WITH ZZ=-0.78 --
# entanglement shows up in every basis at once, and in the z-basis that pair
# agreed in 0.7% of shots. There is no such thing here as a secret alliance
# that looks like nothing. A pair correlated in Y is visibly ANTI-correlated
# in Z, which is a different and honest mechanic: they never both commit.
# ======================================================================
LEV_CHANNELS = {
    # game meaning        forward pauli, reverse pauli
    "influence": ("ZX", "XZ"),   # you shape what they decide
    "debt":      ("XY", "YX"),   # they owe you
}

deb    = [[0.0] * N for _ in range(N)]   # the second channel's intent table
DEB_DECAY = 0.88


def set_debt(a, b, amount, why):
    """A second, independent kind of power over someone. Verified asymmetric
    (XY vs YX differ by +1.02 when driven), and independent of ZZ, so a debt
    is not an alliance and does not become one."""
    deb[a][b] = max(-1.0, min(1.0, deb[a][b] + amount))
    deb[b][a] = -deb[a][b]
    log_gate(f"set_relationship(q{min(a,b)},q{max(a,b)} -> "
             f"{'XY' if a < b else 'YX'}, {int(abs(amount)*100)}%)  {why}")


def set_leverage(a, b, amount, why):
    """ASYMMETRIC influence -- only expressible because we have the full
    two-qubit tomography rather than a single number per pair.

    BUG FIXED HERE: this used ZX no matter which way round a and b were,
    but ZX is asymmetric -- the Z applies to the LOWER-indexed qubit. So
    "a gains sway over b" silently became its opposite whenever a > b,
    which is why an invaded kingdom ended up holding leverage over the
    invader."""
    lev[a][b] = max(-1.0, min(1.0, lev[a][b] + amount))
    lev[b][a] = -lev[a][b]
    pauli = "ZX" if a < b else "XZ"
    log_gate(f"set_relationship(q{min(a,b)},q{max(a,b)} -> {pauli}, "
             f"{int(abs(amount)*100)}%)  {why}")


# ----------------------------------------------------------------------
PAULIS = ("XX", "XY", "XZ", "YX", "YY", "YZ", "ZX", "ZY", "ZZ")


class EngineView:
    """Read the world out of graph-v1's tomography instead of the local sim.

    graph-v1 hands back exactly the two things the game reads -- a Bloch
    vector per qubit and nine Paulis per pair -- so this exposes the same two
    methods QuantumGraph does and every caller downstream is unchanged.

        "tomography": {
          "bloch":         {"0": {"X":..,"Y":..,"Z":..}, ...},
          "relationships": {"0,1": {"XX":..,"XY":.., ... ,"ZZ":..}, ...}
        }

    Keys arrive as STRINGS, and pairs as "a,b" ascending. Every caller in this
    file already normalises to (min, max) and handles direction itself, so no
    transpose is needed here -- and it must not be added, because getting the
    ZX/XZ order wrong would silently invert which kingdom holds leverage over
    which.
    """

    def __init__(self, tomo):
        self.bloch = (tomo or {}).get("bloch") or {}
        self.rel = (tomo or {}).get("relationships") or {}

    def ok(self):
        return bool(self.bloch)

    def get_bloch(self, i):
        d = self.bloch.get(str(int(i))) or {}
        return {k: float(d.get(k, 0.0)) for k in ("X", "Y", "Z")}

    def get_relationship(self, a, b):
        d = self.rel.get(f"{int(a)},{int(b)}") or {}
        return {p: float(d.get(p, 0.0)) for p in PAULIS}

    def as_dict(self):
        return {"bloch": self.bloch, "relationships": self.rel}


engine_view = None      # set by sync_world_from_engine, travels in the world


def view():
    """Where the game reads the world FROM.

    The engine's tomography when we have it, the local graph otherwise. This
    is a function rather than a variable because rebuild() reassigns qg, and a
    cached reference would go stale and silently serve last month's world.
    """
    return engine_view if engine_view is not None else qg


def connected(a, b, key="ZZ"):
    """<AB> - <A><B>: zero for kingdoms that merely happen to share a mood,
    non-zero only where there is a real correlation."""
    _v = view()
    r = _v.get_relationship(min(a, b), max(a, b))
    ba = _v.get_bloch(a)
    bb = _v.get_bloch(b)
    raw = r[key]
    return max(-1.0, min(1.0, (raw - ba[key[0]] * bb[key[1]]) * 1.35))


def leverage_read(a, b):
    """Who has the upper hand. Positive: a sways b more than b sways a."""
    r = view().get_relationship(min(a, b), max(a, b))
    v = r["ZX"] - r["XZ"]
    return max(-1.0, min(1.0, v if a < b else -v))


def debt_read(a, b):
    """The second channel. Positive: b owes a."""
    r = view().get_relationship(min(a, b), max(a, b))
    v = r["XY"] - r["YX"]
    return max(-1.0, min(1.0, v if a < b else -v))


def tangle_read(a, b):
    """HOW MUCH two kingdoms are locked together, regardless of WHICH WAY.

    ZZ tells you the sign -- allied or opposed. It does not tell you the
    magnitude of the entanglement, because a deeply entangled pair is
    correlated in every basis. Measured here:

        allied     ZZ=+0.85 YY=-0.87 XX=+0.57  -> tangle 1.35
        opposed    ZZ=-0.81 YY=+0.84 XX=+0.54  -> tangle 1.28
        unrelated  ZZ=-0.02 YY=+0.04 XX=+0.03  -> tangle 0.06

    So love and hatred are equally entangling, and this is the number that
    says so. compute_ending already treats them as equivalent when deciding
    DISSOLVED versus CONSUMED -- this makes the quantity the game was already
    reasoning about visible to the player."""
    zz = connected(a, b, "ZZ")
    yy = connected(a, b, "YY")
    xx = connected(a, b, "XX")
    return min(1.0, math.sqrt(zz * zz + yy * yy + xx * xx) / math.sqrt(3.0))


def read_state():
    out = []
    eff = effective_bonds()
    for i in range(N):
        bl = view().get_bloch(i)
        r = math.sqrt(bl["X"] ** 2 + bl["Y"] ** 2 + bl["Z"] ** 2)
        # v8: intel actually goes stale. `hostility` stays live because the
        # rival AI runs on the client and has to think with real numbers;
        # `known_*` is the last thing a scout brought home, and is what the
        # UI should draw. Previously intel_fresh was hardcoded True, so
        # scouting bought you nothing at all.
        fresh = (i == 0) or (current_year - last_seen[i] <= INTEL_TTL)
        snap = {
            "hostility": round(max(-1.0, min(1.0, -bl["Z"] / MOOD_CEILING)), 3),
            "conviction": round(min(1.0, abs(bl["Z"]) / MOOD_CEILING), 3),
            "independence": round(min(1.0, r), 3),
        }
        if known[i] is None or fresh:
            known[i] = dict(snap)
        out.append({
            "id": i,
            "intel_fresh": fresh,
            "known_hostility": known[i]["hostility"],
            "known_conviction": known[i]["conviction"],
            "known_independence": known[i]["independence"],
            # surfaced so the player can SEE the finite budget of loyalty
            # each kingdom has, and spend it or deny it deliberately
            "bond_budget_left": round(max(0.0, MAX_BOND_TOTAL - sum(
                abs(eff[i][j]) for j in range(N) if j != i)), 3),
            # THE BUDGET, shipped properly. `max` is the constant, which the
            # client previously had no way to know, so it could not draw
            # "0.4 of 1.25" without hardcoding 1.25 and drifting from it.
            "bond_budget_max": MAX_BOND_TOTAL,
            "bond_budget_used": round(sum(
                abs(eff[i][j]) for j in range(N) if j != i), 3),
            # who they have spent it ON, biggest first, oaths included
            "commitments": bond_commitments(i),
            # Z is deliberately capped at MOOD_CEILING so relationships
            # remain possible, so rescale to the full -1..1 the game's
            # wording bands expect. The physics keeps its headroom; the
            # player still sees "seething".
            "hostility": round(max(-1.0, min(1.0, -bl["Z"] / MOOD_CEILING)), 3),
            "conviction": round(min(1.0, abs(bl["Z"]) / MOOD_CEILING), 3),
            "independence": round(min(1.0, r), 3),  # how much their own
            "bond_to_player": 0.0 if i == 0 else round(connected(0, i), 3),
            "leverage_over_you": 0.0 if i == 0 else round(leverage_read(i, 0), 3),
            "debt_over_you": 0.0 if i == 0 else round(debt_read(i, 0), 3),
            "tangle_with_you": 0.0 if i == 0 else round(tangle_read(i, 0), 3),
            "bloch": {k: round(v, 3) for k, v in bl.items()},
            "last_scouted": last_seen[i],
        })
    pairs = []
    for a in range(N):
        for b in range(a + 1, N):
            pairs.append({"a": a, "b": b,
                          "correlation": round(connected(a, b), 3),
                          "leverage": round(leverage_read(a, b), 3),
                          # v12: the other six correlators, finally used
                          "debt":   round(debt_read(a, b), 3),
                          "tangle": round(tangle_read(a, b), 3)})
    return out, pairs


def quantum_report():
    """What the game shows the player to make the physics visible."""
    return {
        "gates": gate_log[-14:],
        "circuit_depth": qg.qc.depth(),
        "gate_count": len(qg.qc.data),
        "qubits": N,
        "shots": SHOTS,
        "backend": backend_mode(),
        "fork": _qgmod.__file__,
        "backend_note": SETTINGS["note"],
        "sampled": SETTINGS["sampled"],
        "hardware": hardware_status(),
        "hw_enabled": hw_enabled,
        "hw_scope": hw_scope,
        "hw_log": hw_log[-6:],
        "hw_budget": hw_budget_report(),
    }


# ----------------------------------------------------------------------
def apply_event(ev, moods_only=False, bonds_only=False):
    # NB: these are deliberately NOT called mood/bond/lev -- those names are
    # the global intent tables, and shadowing them here made mood[a] resolve
    # to the local function and blow up.
    def do_mood(i, d, why):
        if not bonds_only: set_mood(i, d, why)
    def do_bond(a_, b_, t, f, why):
        if not moods_only: set_bond(a_, b_, t, f, why)
    def do_lev(a_, b_, amt, why):
        if not moods_only: set_leverage(a_, b_, amt, why)

    kind = ev.get("type")
    a = ev.get("faction")
    b = ev.get("other_faction")
    if a is not None:
        a = int(a)
    if b is not None:
        b = int(b)

    if kind == "attacked":
        do_bond(a, b, -1, BOND_STEP, "war")
        do_mood(b, MOOD_STEP, "invaded")
        do_mood(a, MOOD_STEP * 0.4, "the war hardens the aggressor")
        # winning gives you a hold over them: asymmetric, and only
        # expressible because we have the full tomography
        do_lev(a, b, 0.35, "the victor gains sway")
    elif kind == "aided":
        do_bond(a, b, +1, BOND_STEP * 0.7, "aid")
        do_mood(b, -MOOD_STEP * 0.7, "relieved")
        # aid buys OBLIGATION, on its own channel. Previously this was folded
        # into influence, so feeding a neighbour and bullying one produced the
        # same kind of power. They are different kinds of power.
        if not moods_only: set_debt(a, b, 0.35, "a debt is owed")
    elif kind == "bound":
        do_bond(a, b, +1, BOND_STEP * 1.2, "a pact")
    elif kind == "brokered":
        do_bond(a, b, +1, BOND_STEP, "peace brokered")
        do_mood(a, -MOOD_STEP * 0.4, "peace")
        do_mood(b, -MOOD_STEP * 0.4, "peace")
    elif kind == "provoked":
        do_mood(a, MOOD_STEP, "provoked")
    elif kind == "supported":
        do_mood(a, -MOOD_STEP, "supported")
    elif kind == "war_weariness":
        do_mood(a, MOOD_STEP * 0.3, "war weariness")
    elif kind == "peace_year":
        do_mood(a, -mood[a] * 0.15, "a quiet year")
    elif kind == "spied_on":
        do_bond(a, b, -1, BOND_STEP * 0.3, "spies discovered")
    elif kind == "nudge_bond" and b is not None:
        d = float(ev.get("delta", 0.0))
        do_bond(a, b, 1 if d > 0 else -1, min(0.9, abs(d)), "diplomacy")
    elif kind == "nudge_mood":
        do_mood(a, float(ev.get("delta", 0.0)), "the mood shifts")
    # BATTLE FEEDBACK: a defeat reshapes what a kingdom is, which is the
    # loop from the article -- outcomes change the qubits, and the qubits
    # decide the next outcomes.
    elif kind == "battle_result":
        outcome = ev.get("outcome", "stalemate")
        if outcome == "decisive":
            do_mood(b, MOOD_STEP * 1.2, "humiliated")
            do_lev(a, b, 0.45, "a decisive victory")
        elif outcome == "costly":
            do_mood(a, MOOD_STEP * 0.5, "a bloody victory")
            do_mood(b, MOOD_STEP * 0.5, "a bloody defeat")
        elif outcome == "repelled":
            do_mood(a, -MOOD_STEP * 0.3, "thrown back")
            do_lev(b, a, 0.35, "the defender proved themselves")


# ----------------------------------------------------------------------
def _prepared(rotations=None):
    """The live circuit with a list of (qubit, axis, angle) rotations applied
    in order, then measured.

    ORDER AND AXIS BOTH MATTER, and that is the point. Two rotations about
    the same axis on the same qubit compose as one rotation of the summed
    angle, so aligned pressure adds perfectly and opposed pressure cancels
    perfectly. Rotations about different axes do not commute, so mixed
    pressure is genuinely not the sum of its parts."""
    circ = qg.qc.copy()
    for (q, axis, ang) in (rotations or []):
        if abs(ang) < 1e-9:
            continue
        if axis == "Y":   circ.ry(ang, q)
        elif axis == "X": circ.rx(ang, q)
        else:             circ.rz(ang, q)   # coercion: no effect alone
    creg = ClassicalRegister(N, "m")
    circ.add_register(creg)
    circ.measure(range(N), creg)
    return circ


def measure_once(extra_ry=None, rotations=None, hardware=False, kind=""):
    """One shot of the live circuit. This is what decides outcomes.

    extra_ry is kept for the v7 call sites; rotations is the v8 form."""
    rots = list(rotations or [])
    if extra_ry is not None:
        rots.append((extra_ry[0], "Y", extra_ry[1]))
    counts, where, fell_back, reason = run_counts(_prepared(rots), 1,
                                                  hardware=hardware, kind=kind)
    measure_once.last_where = where
    measure_once.last_fell_back = fell_back
    measure_once.last_reason = reason
    return list(counts.keys())[0][::-1]


def probability_of(qubits, rotations=None, shots=P_SHOTS):
    # always local: this is a readout for the UI, never a decision
    """P(every listed qubit measures 1), estimated from real shots.

    This is what lets the game show the player an HONEST number before a
    measurement, interference included, instead of a hand-made bias word.
    The odds it prints are the odds the single deciding shot will actually
    obey."""
    counts, _w, _fb, _r = run_counts(_prepared(rotations), shots, hardware=False)
    hit = 0
    for bitstr, n in counts.items():
        bits = bitstr[::-1]
        if all(bits[q] == "1" for q in qubits):
            hit += n
    return hit / max(1, sum(counts.values()))


# ----------------------------------------------------------------------
def commit_rotations(c):
    """Every rotation a commitment carries into its maturity measurement:
    the oath itself, plus everything anyone has done to it since.

    ORDER MATTERS AND IS DELIBERATE: everything the world did to this oath
    goes in FIRST, and the oath's own weight lands LAST, just before the
    measurement. That ordering is what makes coercion (Rz) worth anything.
    A Z rotation applied last would commute straight through a Z-basis
    measurement and provably do nothing; applied first it rotates the plane
    the oath's own force then has to act in. Interference has to happen
    before the thing it interferes with."""
    cfg = COMMIT_KINDS[c["kind"]]
    rots = []
    for p in c["pressure"]:
        rots.append((p["on"], p["axis"], p["amount"]))
    rots.append((c["a"], c["axis"], cfg["angle"] * c["strength"] * cfg["sign"]))
    rots.append((c["b"], c["axis"], cfg["angle"] * c["strength"] * cfg["sign"] * 0.7))
    return rots


def commit_open(kind, a, b, axis, strength, span, owner):
    global next_uid
    if kind not in COMMIT_KINDS:
        kind = "pact"
    c = {
        "uid": next_uid,
        "kind": kind,
        "a": int(a), "b": int(b),
        "axis": axis if axis in ("X", "Y", "Z") else "Y",
        "strength": max(0.15, min(1.0, float(strength))),
        "sworn": current_year,
        "matures": current_year + max(1, int(span or COMMIT_KINDS[kind]["span"])),
        "owner": int(owner),
        "pressure": [],
    }
    next_uid += 1
    pending.append(c)
    log_gate(f"commit #{c['uid']} {kind} q{a}-q{b} axis {c['axis']} "
             f"{int(c['strength']*100)}% -- measures month {c['matures']}")
    return c


def commit_find(uid):
    for c in pending:
        if c["uid"] == int(uid):
            return c
    return None


def commit_pressure(uid, by, axis, amount, on=None):
    """Feed or break somebody's open commitment. `on` picks which of the two
    qubits you lean on, which is a real choice: leaning on the party who
    wants it least is worth more."""
    c = commit_find(uid)
    if c is None:
        return False
    tgt = int(on) if on is not None else c["b"]
    if tgt not in (c["a"], c["b"]):
        tgt = c["b"]
    c["pressure"].append({"by": int(by), "on": tgt,
                          "axis": axis if axis in ("X", "Y", "Z") else "Y",
                          "amount": max(-1.2, min(1.2, float(amount)))})
    log_gate(f"pressure on #{c['uid']} by q{by}: {axis} {amount:+.2f} on q{tgt}")
    return True


def world_ops():
    """The whole world as a graph-v1 `operations` list.

    This is rebuild() rewritten as data instead of method calls, and it is
    deliberately the SAME two-stage shape, in the same order, from the same
    tables. If the two ever disagree the bug is here, so keep them adjacent in
    your head: moods first, then relationships, and a refresh in between.

    THE REFRESH IS NOT OPTIONAL. set_relationship derives its rotation from the
    current tomography, so a relationship op that follows a blind mood op works
    off a stale read and lands on the wrong axis. Measured, the hard way: asked
    for ZZ=-1 with update=False on the moods, got ZZ=4e-16 and ZX=-0.995.
    Setting update=True on the LAST mood op forces the refresh, which is
    exactly what rebuild()'s update_tomography(shots=SHOTS) does locally.

    What travels: every kingdom's mood as a Bloch vector, every bond as a
    SIGNED ZZ (so wars are as expressible as alliances), leverage as ZX or XZ
    by direction, debt as XY or YX. Open commitments are already folded in by
    effective_bonds(), so an unresolved oath arrives as a real correlation and
    the pressure people put on it arrives with it.

    What does NOT travel: the composed rotations a battle uses. Those are raw
    gates and this engine takes targets. Battles stay local and say so.
    """
    ops = []

    # Stage 1: moods. Same mapping as rebuild().
    for i in range(N):
        z = -mood[i] * MOOD_CEILING
        x = math.sqrt(max(0.0, 1.0 - z * z))
        ops.append({"type": "bloch", "qubit": i,
                    "paulis": {"X": x, "Y": 0.0, "Z": z},
                    "update": False})
    if ops:
        ops[-1]["update"] = True        # the refresh; see the docstring

    # Stage 2: relationships, which therefore get the last word. Order within
    # the stage matches rebuild()'s loops so the two land the same state.
    eff = effective_bonds()
    for a in range(N):
        for b in range(a + 1, N):
            if abs(eff[a][b]) > 0.03:
                ops.append({"type": "relationship", "qubits": [a, b],
                            "paulis": {"ZZ": 1.0 if eff[a][b] > 0 else -1.0},
                            "fraction": min(0.95, abs(eff[a][b])),
                            "update": True})
            if abs(lev[a][b]) > 0.03:
                p = "ZX" if lev[a][b] > 0 else "XZ"
                ops.append({"type": "relationship", "qubits": [a, b],
                            "paulis": {p: 1.0},
                            "fraction": min(0.9, abs(lev[a][b])),
                            "update": True})
            if abs(deb[a][b]) > 0.03:
                p = "XY" if deb[a][b] > 0 else "YX"
                ops.append({"type": "relationship", "qubits": [a, b],
                            "paulis": {p: 1.0},
                            "fraction": min(0.9, abs(deb[a][b])),
                            "update": True})
    return ops


def commit_mature(c):
    """The deferred measurement. Four outcomes, from the two kingdoms' bits
    in ONE joint shot -- so a commitment can be broken by the state of
    kingdoms who were never party to it."""
    global hw_jobs_used          # the engine spends credits; guard below
    sign_cfg = COMMIT_KINDS[c["kind"]]["sign"]
    rots = commit_rotations(c)
    oracle_meta = None

    # ---- REAL HARDWARE ------------------------------------------------
    # No sign restriction any more. labyrinth-v1 forced ZZ=+1, so sieges and
    # embargoes had to stay local or be misrepresented; graph-v1 applies the
    # sign we send (asked -1, measured -1.0), so every kind of oath is
    # eligible and the whole world goes with it.
    eng = graph_engine() if want_hardware("oath") else None

    # THE SPEND GUARD. This path does not go through run_counts(), so without
    # it the budget would ignore the one code path that actually costs
    # credits: 5 per engine run.
    if eng is not None and hw_budget_left() <= 0:
        hw_log.append(f"oath #{c['uid']}: hardware budget spent "
                      f"({hw_jobs_used}/{HW_BUDGET} jobs), decided locally")
        eng = None

    bits = None
    if eng is not None and eng.available():
        try:
            # counted BEFORE the call: a job that errors after submission may
            # still have been billed, and over-counting is the safe direction
            hw_jobs_used += 1
            oracle_meta = eng.measure(world_ops(), N, shots=1)
            bits = oracle_meta["bits"]
            hw_log.append(
                f"oath #{c['uid']} ({c['kind']}): measured on "
                f"{eng.where(oracle_meta)} bits={bits} "
                f"ibm_job={oracle_meta.get('ibm_job_id')} "
                f"in {oracle_meta.get('wall_s')}s "
                f"({hw_jobs_used}/{HW_BUDGET} jobs used)")
        except Exception as e:
            hw_log.append(f"oath #{c['uid']}: engine failed "
                          f"({type(e).__name__}: {e}), decided locally")
            oracle_meta = None
            bits = None

    if oracle_meta is None:
        bits = measure_once(rotations=rots, hardware=False,
                            kind=f"oath #{c['uid']}")
    # Same two bits read the same way whether they came from here or from a
    # QPU. That is the payoff of sending the real state: no separate decoding
    # path, no chance of the two disagreeing.
    a_in = bits[c["a"]] == "1"
    b_in = bits[c["b"]] == "1"
    sign = sign_cfg

    if a_in and b_in:
        outcome = "sealed"
    elif a_in and not b_in:
        outcome = "hollow"      # you are bound, they are not
    elif b_in and not a_in:
        outcome = "captured"    # they got the better of the bargain
    else:
        outcome = "broken"

    a, b = c["a"], c["b"]
    if outcome == "sealed":
        set_bond(a, b, sign, 0.85, f"{c['kind']} sealed")
        if sign > 0:
            set_mood(a, -MOOD_STEP * 0.3, "an oath kept")
            set_mood(b, -MOOD_STEP * 0.3, "an oath kept")
        else:
            set_mood(b, MOOD_STEP * 0.8, "held under")
            set_leverage(a, b, 0.40, f"{c['kind']} succeeded")
    elif outcome == "hollow":
        set_bond(a, b, sign, 0.30, f"{c['kind']} half-kept")
        set_leverage(b, a, 0.30, "one side over-committed")
        set_mood(a, MOOD_STEP * 0.4, "made a fool of")
    elif outcome == "captured":
        set_bond(a, b, sign, 0.30, f"{c['kind']} turned")
        set_leverage(b, a, 0.45, "the other side took the profit")
        set_mood(a, MOOD_STEP * 0.5, "outmanoeuvred")
    else:
        set_bond(a, b, -sign, 0.25, f"{c['kind']} collapsed")
        set_mood(a, MOOD_STEP * 0.5, "an oath broken")
        set_mood(b, MOOD_STEP * 0.5, "an oath broken")

    return {"uid": c["uid"], "kind": c["kind"], "a": a, "b": b,
            "axis": c["axis"], "owner": c["owner"],
            "outcome": outcome, "bits": bits,
            "measured_on": (graph_engine().where(oracle_meta)
                            if oracle_meta is not None else "local"),
            "fell_back": (False if oracle_meta is not None
                          else measure_once.last_fell_back),
            "fallback_reason": ("" if oracle_meta is not None
                                else measure_once.last_reason),
            "hardware": oracle_meta,
            "tilt": round(commit_tilt(c), 3),
            "sworn": c["sworn"], "matures": c["matures"]}


def commit_board():
    """What the client draws. p_seal is measured, not modelled: it is the
    real probability this commitment seals if it were measured right now,
    so it visibly moves as other people interfere with it."""
    out = []
    for c in pending:
        rots = commit_rotations(c)
        out.append({
            "uid": c["uid"], "kind": c["kind"],
            "a": c["a"], "b": c["b"], "axis": c["axis"],
            "owner": c["owner"],
            "strength": round(c["strength"], 3),
            "sworn": c["sworn"], "matures": c["matures"],
            "months_left": max(0, c["matures"] - current_year),
            "progress": round(commit_progress(c), 3),
            "tilt": round(commit_tilt(c), 3),
            "p_seal": round(probability_of([c["a"], c["b"]], rots), 3),
        })
    return out


@app.route("/newgame", methods=["POST"])
def newgame():
    with lock:
        global current_year
        current_year = 1
        fresh_graph()

        # Four starting dispositions from two real shots, so kingdoms are
        # not all equally extreme and the middling ones can exist.
        b1 = measure_once()
        b2 = measure_once()
        LEAN = [-0.85, -0.30, 0.30, 0.85]   # in hostility units now
        for i in range(1, N):
            step = (2 if b1[i] == "1" else 0) + (1 if b2[i] == "1" else 0)
            set_mood(i, LEAN[step], "born so")
        rebuild()

        # traits sampled from each kingdom's OWN qubit, so appearance
        # encodes disposition rather than being an unrelated coin flip
        traits = ["" for _ in range(N)]
        for _ in range(16):
            bits = measure_once()
            for i in range(N):
                traits[i] += bits[i]

        # DELIBERATELY NOT synced here. Month 1's world is all zeros and
        # near-zeros, so an engine read shows the player nothing they could
        # not guess -- and /newgame is the worst moment in the game to spend a
        # round trip, because it is the one the player waits on at the letter
        # screen with nothing else happening. From month 2 the numbers matter
        # and the wait is inside a turn they chose to take.
        factions, pairs = read_state()
        for i in range(N):
            last_seen[i] = 1          # you begin the game briefed
        factions, pairs = read_state()
        world_seal()
        return jsonify({"measured_bits": b1, "traits": traits,
                        "factions": factions, "pairs": pairs,
                        "board": commit_board(),
                        "quantum": quantum_report(),
                        # THE WORLD GOES HOME WITH THE CLIENT. /newgame is
                        # the only endpoint that creates one rather than
                        # receiving one, so this is where a run's state is
                        # born. Everything after this is a pure function.
                        "world": world_export()})


@app.route("/turn", methods=["POST"])
def turn():
    with lock:
        global current_year, gate_log, matured_log
        data = request.get_json(force=True)
        world_load(data)
        current_year = int(data.get("year", current_year))
        gate_log = []

        events = data.get("events", [])

        # ORDER IS LOAD-BEARING. A set_bloch is a single-qubit rotation and
        # can undo entanglement, so applying moods after relationships wipes
        # the relationships out. First pass: moods only. Second pass:
        # relationships, which therefore get the last word.
        decay()
        for ev in events:
            apply_event(ev)

        # ---- v8: commitments -------------------------------------------
        # New oaths sworn this month. They are NOT measured now.
        for c in data.get("commitments", []):
            commit_open(c.get("kind", "pact"), c.get("a", 0), c.get("b", 0),
                        c.get("axis", "Y"), c.get("strength", 0.6),
                        c.get("span"), c.get("owner", c.get("a", 0)))

        # Anybody leaning on anybody's open oath, yours included.
        for p in data.get("pressures", []):
            commit_pressure(p.get("uid"), p.get("by", 0), p.get("axis", "Y"),
                            p.get("amount", 0.0), p.get("on"))

        # An oath nobody tends loses its force, and so does the pressure
        # people put on it. Neglect is a decision.
        for c in pending:
            c["strength"] *= COMMIT_DECAY
            for pr in c["pressure"]:
                pr["amount"] *= PRESSURE_DECAY
            c["pressure"] = [pr for pr in c["pressure"] if abs(pr["amount"]) > 0.04]

        # THE DEFERRED MEASUREMENT. Everything that touched these qubits
        # since the oath was sworn is in the circuit now, so this is decided
        # by the whole world's state and not by the moment of swearing.
        matured_log = []
        still = []
        for c in pending:
            if current_year >= c["matures"] or c["strength"] < 0.12:
                matured_log.append(commit_mature(c))
            else:
                still.append(c)
        pending[:] = still

        diluted = enforce_monogamy()
        rebuild()
        # ONE engine read of the whole world, if the scope allows it. Must sit
        # AFTER rebuild and BEFORE read_state, or the numbers reported are not
        # the ones just measured.
        sync_world_from_engine()
        world_seal()

        factions, pairs = read_state()
        return jsonify({"world": world_export(),
                        "factions": factions, "pairs": pairs,
                        "board": commit_board(), "matured": matured_log,
                        # WHOSE RELATIONSHIPS GOT CROWDED OUT THIS MONTH.
                        # The client turns each of these into a chronicle
                        # line; without it the single most interesting
                        # consequence in the game happens in silence.
                        "diluted": diluted,
                        "quantum": quantum_report()})


@app.route("/resolve", methods=["POST"])
def resolve():
    with lock:
        global current_year, hw_jobs_used
        data = request.get_json(force=True)
        world_load(data)
        current_year = int(data.get("year", current_year))
        answers = []

        for q in data.get("questions", []):
            kind = q.get("kind")

            if kind == "initiative":
                # LOCAL, ALWAYS. This is one shot of the real graph, so who
                # stirs this month follows from the kingdoms' actual moods.
                # v14 routed it through labyrinth-v1, which drops
                # initial_states, so every kingdom came back with the same
                # marginal and the acting kingdom was chosen with no reference
                # to whether it was angry. A quiet map and a 10s stall every
                # eighth turn. Reverted on purpose; see /timing.
                bits = measure_once()
                answers.append({"kind": "initiative",
                                "acts": [bits[i] == "1" for i in range(N)],
                                "bits": bits,
                                "measured_on": "local"})

            elif kind == "battle":
                a = int(q["a"]); b = int(q["b"])
                a_str = float(q.get("attacker_strength", 1))
                d_str = float(q.get("defender_strength", 1))
                bias = max(-1.2, min(1.2,
                       1.2 * (a_str - d_str) / max(1.0, a_str + d_str)))
                # leverage tilts the odds: sway over someone is worth troops
                bias += 0.4 * leverage_read(a, b)

                # v8 INTERFERENCE. Everything landing on these two kingdoms
                # this month arrives as a rotation, and they compose in the
                # circuit before the shot. Same-axis pressure adds or cancels
                # exactly; different axes do neither, because they do not
                # commute. This is where "two plans collide" stops being a
                # figure of speech.
                # Same ordering rule as commitments: outside pressure first,
                # the battle's own weight of numbers last. Otherwise a Z
                # contribution would sit against the measurement and be
                # provably inert.
                rots = []
                for r in q.get("rotations", []):
                    rots.append((int(r.get("q", a)),
                                 r.get("axis", "Y"),
                                 max(-1.2, min(1.2, float(r.get("angle", 0.0))))))
                rots.append((a, q.get("axis", "Y"), bias))

                # The honest odds, measured from the same prepared circuit
                # the deciding shot comes from -- interference included.
                p_win = probability_of([a], rots)
                use_hw = bool(q.get("hardware", want_hardware("battle")))
                bits = measure_once(rotations=rots, hardware=use_hw,
                                    kind=f"battle {a}v{b}")

                ac = bits[a] == "1"
                dc = bits[b] == "1"
                outcome = ("decisive" if ac and not dc else
                           "costly" if ac and dc else
                           "repelled" if dc else "stalemate")
                answers.append({"kind": "battle", "a": a, "b": b,
                                "outcome": outcome,
                                "bias": round(bias, 3),
                                "p_win": round(p_win, 3),
                                "rotations": len(rots),
                                "measured_on": measure_once.last_where,
                                "fell_back": measure_once.last_fell_back,
                                "fallback_reason": measure_once.last_reason,
                                "bond": round(connected(a, b), 3),
                                "leverage": round(leverage_read(a, b), 3)})

        # /resolve measures but does not mutate the intent tables, so the
        # world it hands back is the one it received. Returned anyway so the
        # client never has to care which endpoints are which.
        world_seal()
        return jsonify({"answers": answers, "quantum": quantum_report(),
                        "world": world_export()})


@app.route("/scout", methods=["POST"])
def scout():
    with lock:
        global current_year
        data = request.get_json(force=True)
        world_load(data)
        current_year = int(data.get("year", current_year))
        # v8: a scout actually refreshes intel on its target now, which is
        # what makes spending an action on espy worth anything.
        tgt = data.get("target")
        if tgt is not None:
            last_seen[int(tgt)] = current_year
        factions, pairs = read_state()
        world_seal()
        return jsonify({"factions": factions, "pairs": pairs,
                        "board": commit_board(),
                        "quantum": quantum_report(),
                        "world": world_export()})


@app.route("/observe", methods=["POST"])
def observe():
    """Everything, for the observatory screen: the full two-qubit
    tomography, so the player can look straight at the physics."""
    with lock:
        world_load(request.get_json(force=True, silent=True) or {})
        factions, pairs = read_state()
        tomo = []
        for a in range(N):
            for b in range(a + 1, N):
                r = view().get_relationship(a, b)
                tomo.append({"a": a, "b": b,
                             "paulis": {k: round(v, 3) for k, v in r.items()}})
        world_seal()
        return jsonify({"factions": factions, "pairs": pairs,
                        "tomography": tomo, "board": commit_board(),
                        "quantum": quantum_report(),
                        "world": world_export()})


@app.route("/health", methods=["GET"])
def health():
    """A cheap GET so a launcher or the game can ask 'are you up?' without
    starting a new game or touching the quantum state.

    Everything else is a POST, so there was no way to poll for readiness. The
    launcher needs this because the first /newgame takes a couple of seconds
    of tomography and we must not fire the game at a server that is still
    importing qiskit."""
    return jsonify({
        "ok": True,
        "version": "clean-3",
        # the client can feature-detect the stateless path
        "stateless": True,
        # Report the FILE, not just a string I have to remember to bump. A
        # hardcoded version that disagrees with the running file is exactly
        # the confusion this line exists to end.
        "file": os.path.basename(os.path.abspath(__file__)),
        "backend": backend_mode(),
        "graph_ready": qg is not None,
        "hardware": hw_mode(),
        "hw_enabled": hw_enabled,
        "hw_scope": hw_scope,
    })


@app.route("/credentials", methods=["POST"])
def credentials():
    """The player's own API key, entered in game.

    POST {"token": "...", "remember": false}   load a key for this session
    POST {"clear": true}                        forget it, and delete the file
    POST {}                                     report status

    THE TOKEN IS NEVER RETURNED. Only a masked form, enough to recognise which
    key is loaded and useless to anyone who sees it. It is not written to any
    log, and it is sent to exactly one place: the Moth API.
    """
    global hw_enabled
    with lock:
        data = request.get_json(force=True, silent=True) or {}

        if data.get("clear"):
            clear_credentials()
            hw_enabled = False
            return jsonify({"ok": True, "has_key": False,
                            "message": "Key forgotten. Playing on local "
                                       "simulation.",
                            "hardware": hardware_status()})

        if "token" in data:
            set_credentials(data.get("token"),
                            url=data.get("url"),
                            remember=bool(data.get("remember")))
            if not token_present():
                hw_enabled = False
                return jsonify({"ok": False, "has_key": False,
                                "message": "That key was empty.",
                                "hardware": hardware_status()})

            # A key that does not work should say so NOW, not fall back
            # silently in the middle of an oath.
            eng = graph_engine()
            st = hardware_status()
            if eng is None:
                hw_enabled = False
                return jsonify({"ok": False, "has_key": True,
                                "message": "Key accepted but the engine did "
                                           "not answer. Playing locally.",
                                "hardware": st})
            hw_enabled = True
            return jsonify({"ok": True, "has_key": True,
                            "message": "Connected to " + str(st["engine"])
                                       + ". Oaths can be settled there.",
                            "hardware": st})

        return jsonify({"ok": True, "has_key": token_present(),
                        "hardware": hardware_status()})


@app.route("/hardware", methods=["POST"])
def hardware():
    """Runtime toggle. The game can offer this as a keypress.

    POST {"enable": true, "scope": "battles"}   route battles to the device
    POST {"enable": false}                       everything local again
    POST {"retry": true}                         re-attempt a device that failed
    POST {"budget": 50}                          raise the hard job ceiling
    POST {"reset_budget": true}                  zero the counter
    POST {}                                      just report status
    """
    global hw_enabled, hw_scope, HW_BUDGET, hw_jobs_used, hw_shots_used
    with lock:
        data = request.get_json(force=True, silent=True) or {}
        if data.get("retry"):
            reset_hardware()
        # raising the ceiling is deliberate and explicit; there is no path
        # that quietly grants itself more
        if "budget" in data:
            HW_BUDGET = max(0, int(data["budget"]))
        if data.get("reset_budget"):
            hw_jobs_used = 0
            hw_shots_used = 0
        if "enable" in data:
            hw_enabled = bool(data["enable"])
        if data.get("scope") in ("oaths", "off"):
            hw_scope = data["scope"]
        return jsonify({"hw_enabled": hw_enabled, "hw_scope": hw_scope,
                        "hardware": hardware_status(),
                        "hw_budget": hw_budget_report(),
                        "hw_log": hw_log[-6:]})


# ======================================================================
# TIMING  --  where does a turn actually go?
#
# Added because I twice guessed wrong about what makes turns slow. Guessing is
# not allowed here: this wraps the suspects, counts calls and total seconds,
# and GET /timing prints the table. Zero cost when nobody looks at it beyond a
# time.time() per call.
#
# Read it as: calls x mean = total. The row with the biggest TOTAL is the one
# worth fixing, not the row with the biggest mean.
# ======================================================================
_timing = {}


def _timed(fn, name=None):
    label = name or fn.__name__

    def wrapper(*a, **kw):
        t0 = time.time()
        try:
            return fn(*a, **kw)
        finally:
            row = _timing.setdefault(label, [0, 0.0])
            row[0] += 1
            row[1] += time.time() - t0
    wrapper.__name__ = getattr(fn, "__name__", label)
    return wrapper


for _name in ("rebuild", "read_state", "commit_board", "quantum_report",
              "apply_event", "measure_once", "probability_of",
              "commit_mature", "decay", "enforce_monogamy"):
    if _name in globals() and callable(globals()[_name]):
        globals()[_name] = _timed(globals()[_name], _name)


# Per-request timing, plus the GAP between requests. The gap is the important
# half: if the server answers /turn in 60ms and the next request arrives 4
# seconds later, those 4 seconds are the game's own doing and no amount of
# server work will fix them. Nothing above this line could have told us that.
_reqlog = []
_req_t0 = {}


@app.before_request
def _t_start():
    _req_t0[threading.get_ident()] = time.time()


@app.after_request
def _t_end(resp):
    t0 = _req_t0.pop(threading.get_ident(), None)
    if t0 is not None and request.path != "/timing":
        gap = None
        if _reqlog:
            gap = round(t0 - _reqlog[-1]["ended"], 3)
        _reqlog.append({"path": request.path,
                        "server_s": round(time.time() - t0, 3),
                        "gap_before_s": gap,
                        "ended": time.time()})
        del _reqlog[:-40]
    return resp


@app.route("/timing", methods=["GET", "POST"])
def timing():
    if request.method == "POST":
        _timing.clear()
        _reqlog.clear()
        return jsonify({"cleared": True})
    rows = []
    for k, (n, tot) in sorted(_timing.items(), key=lambda kv: -kv[1][1]):
        rows.append({"what": k, "calls": n,
                     "total_s": round(tot, 3),
                     "mean_ms": round(1000.0 * tot / max(1, n), 1)})
    reqs = [{k: v for k, v in r.items() if k != "ended"} for r in _reqlog]
    server_tot = round(sum(r["server_s"] for r in _reqlog), 3)
    gap_tot = round(sum(r["gap_before_s"] or 0.0 for r in _reqlog), 3)
    return jsonify({"rows": rows,
                    "requests": reqs,
                    "server_total_s": server_tot,
                    "gap_total_s": gap_tot,
                    "verdict": ("client/game side" if gap_tot > server_tot
                                else "server side"),
                    "network": {"http_calls": GraphEngine.net_calls,
                                "http_total_s": round(
                                    GraphEngine.net_seconds, 2)},
                    "backend": backend_mode(),
                    "shots": SHOTS,
                    "rebuild_passes": REBUILD_PASSES,
                    "p_shots": P_SHOTS,
                    "note": "biggest total_s is the one to fix"})


if __name__ == "__main__":
    fresh_graph()
    print("Eigenstate quantum brain clean-2 (bond budget visible; oaths on graph-v1) -> http://localhost:5055")
    print("  POST /newgame /turn /resolve /scout /observe /hardware")
    app.run(port=5055)
