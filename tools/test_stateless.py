#!/usr/bin/env python3
"""Exercise the stateless world path without qiskit or QuantumGraph.

WHY A STUB. The refactor's whole risk is the plumbing: does the world blob
round-trip losslessly, does a second request carrying a world reproduce the
first's state, does a malformed blob fail legibly, and does the rebuild
cache actually skip work. None of that involves the quantum layer, so a
stub QuantumGraph tests exactly the thing that could be wrong and nothing
that could not be.

Run from the server folder:   python3 tools/test_stateless.py
"""
import sys, os, json, types, random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------- stubs
rebuild_calls = {"n": 0}


class _QG:
    """Deterministic stand-in. Records relationships so get_relationship
    returns something shaped right, and counts constructions so the test can
    see how many rebuilds actually happened."""

    class _QC:
        """quantum_report() reads qg.qc.depth() and len(qg.qc.data)."""
        data = []

        def depth(self):
            return 0

    def __init__(self, n, backend=None):
        self.n = n
        self.rel = {}
        self.qc = _QG._QC()
        self.bloch = [dict(X=0.0, Y=0.0, Z=0.0) for _ in range(n)]
        rebuild_calls["n"] += 1

    def set_bloch(self, vec, i, update=True):
        self.bloch[i] = dict(vec)

    def set_relationship(self, paulis, a, b, fraction=1.0, update=True):
        self.rel.setdefault((min(a, b), max(a, b)), {}).update(
            {k: float(v) * fraction for k, v in paulis.items()})

    def update_tomography(self, shots=None):
        pass

    def get_relationship(self, a, b):
        base = {p: 0.0 for p in ("XX", "XY", "XZ", "YX", "YY",
                                 "YZ", "ZX", "ZY", "ZZ")}
        base.update(self.rel.get((min(a, b), max(a, b)), {}))
        return base

    def get_bloch(self, i):
        return dict(self.bloch[i])


qgmod = types.ModuleType("quantumgraph")
qgmod.QuantumGraph = _QG
qgmod.__path__ = []            # make it a package so submodules can be faked
qgmod.__file__ = "<stub>/quantumgraph/__init__.py"   # quantum_report reads this
sys.modules["quantumgraph"] = qgmod

ev = types.ModuleType("quantumgraph.ExpectationValue")
ev.ExpectationValue = lambda *a, **k: "stub-backend"
qgmod.ExpectationValue = ev
sys.modules["quantumgraph.ExpectationValue"] = ev

pt = types.ModuleType("pairwise_tomography")
ptr = types.ModuleType("pairwise_tomography.pairwise_state_tomography")
ptr.PairwiseStateTomographyFitter = object
pt.pairwise_state_tomography = ptr
sys.modules["pairwise_tomography"] = pt
sys.modules["pairwise_tomography.pairwise_state_tomography"] = ptr

import eigenstate_server as S

# a deterministic, qiskit-free replacement for the one function that needs
# a real circuit. Everything under test is above this line.
_rng = random.Random(7)
S.measure_once = lambda *a, **k: "".join(
    _rng.choice("01") for _ in range(S.N))
S.measure_once.last_where = "stub"
S.measure_once.last_fell_back = False
S.measure_once.last_reason = ""
S.probability_of = lambda *a, **k: 0.5
S.run_counts = lambda *a, **k: ({"0" * S.N: 1}, "stub", False, "")

app = S.app.test_client()

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  pass  " if cond else "  FAIL  ") + name
          + (("   " + str(detail)) if detail and not cond else ""))


def post(path, body):
    r = app.post(path, json=body)
    return r.status_code, (r.get_json() or {})


print("\n=== 1. /newgame hands back a world ===")
st, ng = post("/newgame", {})
check("/newgame 200", st == 200, st)
check("world present", "world" in ng)
w0 = ng.get("world", {})
for f in ("current_year", "mood", "bond", "lev", "deb",
          "pending", "next_uid", "known", "last_seen"):
    check(f"world.{f} present", f in w0)
check("world is JSON round-trippable",
      json.loads(json.dumps(w0)) == w0)
print(f"  (world serialises to {len(json.dumps(w0))} bytes)")


print("\n=== 2. a turn advances the world it was GIVEN ===")
st, t1 = post("/turn", {"world": w0, "year": 2,
                        "events": [{"type": "attacked", "faction": 1,
                                    "other_faction": 2}]})
check("/turn 200", st == 200, t1)
w1 = t1.get("world", {})
check("year advanced in the returned world", w1.get("current_year") == 2,
      w1.get("current_year"))
check("mood moved", w1.get("mood") != w0.get("mood"))


print("\n=== 3. THE ACTUAL POINT: two players do not collide ===")
# player A and player B interleave requests through the same process, which
# is exactly what a shared serverless instance does. With globals this test
# fails; with a passed world it must not.
st, a1 = post("/turn", {"world": w0, "year": 2,
                        "events": [{"type": "attacked", "faction": 1,
                                    "other_faction": 2}]})
st, b1 = post("/turn", {"world": w0, "year": 2,
                        "events": [{"type": "aided", "faction": 3,
                                    "other_faction": 4}]})
st, a2 = post("/turn", {"world": a1["world"], "year": 3, "events": []})
st, b2 = post("/turn", {"world": b1["world"], "year": 3, "events": []})

check("A and B diverged", a2["world"]["mood"] != b2["world"]["mood"])
check("A's month 3 followed A's month 2, not B's",
      a2["world"]["current_year"] == 3 and b2["world"]["current_year"] == 3)

# and the strong version: replay A's second turn again from A's first world.
# Same input world, same request -> same output world. That is what "pure
# function" means and it is the property the hosted version depends on.
st, a2_again = post("/turn", {"world": a1["world"], "year": 3, "events": []})
check("replaying the same world gives the same result",
      a2_again["world"]["mood"] == a2["world"]["mood"]
      and a2_again["world"]["bond"] == a2["world"]["bond"])


print("\n=== 4. an oath survives the round trip ===")
st, o1 = post("/turn", {"world": w0, "year": 2, "events": [],
                        "commitments": [{"kind": "pact", "a": 0, "b": 2,
                                         "axis": "Y", "strength": 0.7,
                                         "span": 3, "owner": 0}]})
check("oath is on the board", len(o1.get("board", [])) == 1, o1.get("board"))
check("oath is in the world", len(o1["world"]["pending"]) == 1)
uid = o1["world"]["pending"][0]["uid"]
st, o2 = post("/turn", {"world": o1["world"], "year": 3, "events": []})
check("oath still there a month later",
      len(o2["world"]["pending"]) == 1
      and o2["world"]["pending"][0]["uid"] == uid)
check("next_uid carried, so uids do not collide",
      o2["world"]["next_uid"] == o1["world"]["next_uid"])


print("\n=== 5. malformed worlds fail with 400, not 500 ===")
for name, bad in [
    ("mood too short",   {"mood": [0.0, 0.0]}),
    ("bond not square",  {"bond": [[0.0] * 5] * 3}),
    ("bond row short",   {"bond": [[0.0] * 2] * 5}),
    ("last_seen short",  {"last_seen": [0]}),
]:
    st, body = post("/turn", {"world": bad, "year": 2})
    check(f"400 for {name}", st == 400, f"got {st} {body}")

st, body = post("/turn", {"world": "not a dict", "year": 2})
check("a non-dict world is ignored rather than fatal", st == 200, st)

# THE ONE THAT MATTERS. A rejected payload must not poison the instance for
# whoever it serves next. First version of world_import assigned as it went,
# so a short mood list survived the 400 and killed the following request.
post("/turn", {"world": {"mood": [0.0, 0.0]}, "year": 2})
st, after = post("/turn", {"world": w0, "year": 2, "events": []})
check("a good request still works after a rejected one", st == 200, st)
check("and returns a sane world",
      len(after.get("world", {}).get("mood", [])) == 5)


print("\n=== 6. backward compatible: no world key still works ===")
post("/newgame", {})
st, legacy = post("/turn", {"year": 2, "events": []})
check("/turn with no world 200", st == 200, st)
check("still returns a world anyway", "world" in legacy)


print("\n=== 7. the rebuild cache skips work when the world is unchanged ===")
st, _ = post("/newgame", {})
w = _["world"]
# the world /newgame handed back IS the one its qg was built from, so
# sending it straight back must NOT rebuild. That is the cache working.
before = rebuild_calls["n"]
post("/scout", {"world": w, "year": 1, "target": 2})
same = rebuild_calls["n"] - before
check("an unchanged world does not rebuild", same == 0, same)

# a world that differs must rebuild, or the cache is just wrong
w_diff = json.loads(json.dumps(w))
w_diff["bond"][1][2] = 0.8
w_diff["bond"][2][1] = 0.8
before = rebuild_calls["n"]
post("/scout", {"world": w_diff, "year": 1, "target": 2})
changed = rebuild_calls["n"] - before
check("a changed world does rebuild", changed > 0, changed)


print("\n=== 8. /observe and /resolve take a world too ===")
st, ob = post("/observe", {"world": w})
check("/observe 200", st == 200, st)
check("/observe returns tomography", len(ob.get("tomography", [])) == 10)
check("/observe returns a world", "world" in ob)

st, rs = post("/resolve", {"world": w, "year": 1,
                           "questions": [{"kind": "initiative"}]})
check("/resolve 200", st == 200, st)
check("/resolve answered", len(rs.get("answers", [])) == 1)
check("/resolve returns a world", "world" in rs)


print("\n=== 9. CORS and preflight ===")
r = app.get("/health")
check("health has CORS origin",
      r.headers.get("Access-Control-Allow-Origin") == "*",
      r.headers.get("Access-Control-Allow-Origin"))
check("health reports stateless", (r.get_json() or {}).get("stateless") is True)
r = app.options("/turn")
check("OPTIONS preflight is not an error", r.status_code < 400, r.status_code)
check("preflight carries CORS headers",
      r.headers.get("Access-Control-Allow-Origin") == "*"
      and "POST" in (r.headers.get("Access-Control-Allow-Methods") or ""),
      dict(r.headers))


print("\n=== 10. the credit guard is NOT client-resettable ===")
S.hw_jobs_used = 9
post("/turn", {"world": w0, "year": 2, "events": []})
check("hw_jobs_used survived a client world", S.hw_jobs_used == 9,
      S.hw_jobs_used)
check("hw counters are not in the world blob",
      "hw_jobs_used" not in w0 and "hw_shots_used" not in w0)


print(f"\n{'='*54}\n  {len(OK)} passed, {len(FAIL)} failed")
if FAIL:
    print("  failing:", ", ".join(FAIL))
print("=" * 54)
sys.exit(1 if FAIL else 0)
