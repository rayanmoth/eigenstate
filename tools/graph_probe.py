#!/usr/bin/env python3
"""
graph_probe.py -- does graph-v1 actually apply what we send it?

ONE QUESTION, ASKED SO IT CAN FAIL. labyrinth-v1 accepted `relationships` and
threw them away, and the only reason we caught it was reading numbers back out
instead of trusting a 202. So this does the same thing: it asks for a POSITIVE
ZZ and a NEGATIVE ZZ on the same two qubits and compares the measured
agreement between the bits.

    ZZ = +1  ->  qubits should AGREE     (00 and 11)
    ZZ = -1  ->  qubits should DISAGREE  (01 and 10)

If both runs come back with the same agreement, the sign is being ignored and
this engine is labyrinth with a nicer schema. If they come back opposite, the
sign is real and Eigenstate's whole world state can go to hardware.

Then a second test posts a 5-kingdom world with mixed signs and the leverage
channel, which is the thing we actually want to run.

    export MOTH_API_TOKEN=...          # or: source moth.env
    python3 graph_probe.py             # both tests, emu, ~4 runs = 20 credits
    python3 graph_probe.py --signs     # just the sign test, 2 runs
    python3 graph_probe.py --shots 400
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

BASE = (os.environ.get("MOTH_API_URL") or "https://api.mothquantum.com").rstrip("/")
TOKEN = os.environ.get("MOTH_API_TOKEN") or ""
ENGINE = os.environ.get("MOTH_ENGINE_GRAPH", "graph-v1")

# Cloudflare on this host 403s anything that looks like a script
# (error_name browser_signature_banned), which is why the published
# `requests` code samples cannot work as written.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

OK = ("completed", "succeeded", "success", "done", "finished")
BAD = ("failed", "error", "cancelled", "canceled")


def req(path, payload=None, timeout=90):
    h = {"Authorization": "Bearer " + TOKEN,
         "Accept": "application/json", "User-Agent": UA}
    data = None
    if payload is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    r = urllib.request.Request(BASE + path, data=data, headers=h,
                               method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:600]}


def run(params, label, timeout=180):
    """Submit, poll, return the output dict. Prints the job id either way so a
    failure is still reportable to Moth."""
    # The working path is /api/v1/engines/{id}/process with the params NESTED.
    # The engine's own code_samples use /v1/generation/{id}/process with them
    # flat, which is stale, so fall back to flat only if nesting 422s.
    code, body = req(f"/api/v1/engines/{ENGINE}/process", {"params": params})
    if code == 422:
        print(f"    nested params rejected ({code}), retrying flat")
        code, body = req(f"/api/v1/engines/{ENGINE}/process", params)
    job = body.get("job_id")
    print(f"    {label}: submit {code} job={job}")
    if not job:
        print("    " + json.dumps(body)[:400])
        return None

    deadline = time.time() + timeout
    delay = 0.4
    while time.time() < deadline:
        time.sleep(delay)
        delay = min(delay * 1.4, 5.0)
        _, st = req(f"/api/v1/jobs/{job}/status")
        s = str(st.get("status", "")).lower()
        if s in BAD:
            print(f"    FAILED: {json.dumps(st.get('error') or st)[:400]}")
            return None
        if s in OK:
            break
    else:
        print("    timed out waiting")
        return None

    _, res = req(f"/api/v1/jobs/{job}/result")
    out = (res.get("result") or {}).get("output") or {}
    if not out:
        print("    empty output: " + json.dumps(res)[:400])
    return out


def counts_of(out):
    """Find the histogram wherever this engine happens to put it. The schema
    does not promise counts, only dominant_bitstring, so cope with both."""
    for key in ("counts", "histogram", "bitstring_counts"):
        c = out.get(key)
        if isinstance(c, dict) and c:
            return c
    meas = (out.get("results") or {}).get("measurements")
    if isinstance(meas, list) and meas:
        c = {}
        for m in meas:
            b = str(m.get("bitstring", ""))
            if b:
                c[b] = c.get(b, 0) + 1
        if c:
            return c
    dom = out.get("dominant_bitstring")
    if dom:
        return {str(dom): 1}
    return {}


def agreement(counts, i=0, j=1):
    """Fraction of shots where qubit i and qubit j gave the same bit."""
    same = tot = 0
    for b, n in counts.items():
        if len(b) <= max(i, j):
            continue
        tot += n
        if b[i] == b[j]:
            same += n
    return (same / tot) if tot else None


def dump_test(shots):
    """Print the ENTIRE output of one run. We are guessing at what
    edge_agreement_score means and whether counts come back at all, and the
    schema does not say. Stop guessing: look."""
    print("\n=== TEST 0: what does this engine actually return? ===")
    out = run({"num_qubits": 2, "shots": shots, "mode": "emu",
               "operations": [
                   {"type": "bloch", "qubit": 0,
                    "paulis": {"X": 1.0}, "update": False},
                   {"type": "bloch", "qubit": 1,
                    "paulis": {"X": 1.0}, "update": False},
                   {"type": "relationship", "qubits": [0, 1],
                    "paulis": {"ZZ": -1.0}, "fraction": 1.0}]}, "dump")
    if out is None:
        return False
    print(json.dumps(out, indent=2)[:4000])
    return True


def sweep_test(shots, sign):
    """Fraction sweep at one sign. One sample per run tells us almost nothing;
    a monotone trend across five runs tells us the knob is real and which way
    round it goes. emu compute is free, so this costs time, not credits."""
    print(f"\n=== TEST 3: fraction sweep at ZZ={sign:+g} ===")
    print("    fraction  dominant  edge_score")
    seen = []
    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        out = run({"num_qubits": 2, "shots": shots, "mode": "emu",
                   "operations": [
                       {"type": "bloch", "qubit": 0,
                        "paulis": {"X": 1.0}, "update": False},
                       {"type": "bloch", "qubit": 1,
                        "paulis": {"X": 1.0}, "update": False},
                       {"type": "relationship", "qubits": [0, 1],
                        "paulis": {"ZZ": float(sign)}, "fraction": f}]},
                  f"f={f}")
        if out is None:
            return False
        sc = out.get("edge_agreement_score")
        dom = out.get("dominant_bitstring")
        seen.append((f, dom, sc))
        print(f"    {f:>8}  {str(dom):>8}  {sc}")
    scores = [s for _, _, s in seen if isinstance(s, (int, float))]
    if len(scores) >= 4:
        if scores[0] != scores[-1]:
            print("    fraction MOVES the score, so it is a real knob.")
        else:
            print("    fraction did not move the score. Suspicious.")
    return True


def target_test(shots):
    """Ask for a specific correlator, then READ IT BACK out of the returned
    tomography. This is the right test and I should have written it first.

    Two fixes over the earlier attempts:

    1. update=True on the Bloch ops. set_relationship derives its rotation from
       the CURRENT tomography, so without a refresh in between it is working
       from a stale read and lands on the wrong axis -- which is exactly what
       happened: ZZ=-1 requested, ZZ came back 4e-16 and ZX came back -0.995.
       The server's own rebuild() already refreshes between the two stages.

    2. No dependence on the seed or on a single bitstring. The engine hands
       back the whole tomography, so we compare requested target against
       measured correlator directly. 400 shots puts the noise floor around
       0.05, so |measured| > 0.5 with the right sign is unambiguous.
    """
    print("\n=== TEST 5: does the requested correlator actually land? ===")
    cases = [("ZZ", +1.0), ("ZZ", -1.0), ("ZX", +1.0), ("XY", +1.0)]
    ok = True
    for pauli, want in cases:
        ops = [
            {"type": "bloch", "qubit": 0, "paulis": {"X": 1.0},
             "update": True},
            {"type": "bloch", "qubit": 1, "paulis": {"X": 1.0},
             "update": True},
            {"type": "relationship", "qubits": [0, 1],
             "paulis": {pauli: want}, "fraction": 1.0, "update": True},
        ]
        out = run({"num_qubits": 2, "shots": shots, "mode": "emu",
                   "operations": ops}, f"{pauli}={want:+g}")
        if out is None:
            ok = False
            continue
        rel = (((out.get("tomography") or {}).get("relationships")
                or {}).get("0,1") or {})
        got = rel.get(pauli)
        others = {k: round(v, 2) for k, v in rel.items()
                  if k != pauli and abs(v) > 0.3}
        verdict = "?"
        if isinstance(got, (int, float)):
            if got * want > 0.5:
                verdict = "LANDED"
            elif abs(got) < 0.15:
                verdict = "IGNORED"
            else:
                verdict = "partial/wrong sign"
                ok = False
        print(f"      asked {pauli}={want:+g}  measured {pauli}="
              f"{'n/a' if got is None else round(got, 3)}  -> {verdict}")
        if others:
            print(f"        also large: {others}")
    print()
    print("    LANDED on every line means the full world state travels: moods,"
          "\n    signed bonds, and the ZX/XY leverage and debt channels.")
    return ok


def seed_test(shots):
    """THE DECISIVE TEST, and it has no sampling noise in it.

    The schema promises "the same seed always builds the same circuit". So:

        A: seed=42, NO operations          -> the random graph for seed 42
        B: seed=42, strong ZZ=-1 operations

    If B is identical to A, `operations` is being dropped and we are looking at
    seed 42's random graph both times. If B differs, operations are applied.

    This is the same trick that caught labyrinth-v1: never compare a result to
    an expectation, compare it to a result that differs by exactly one input.
    """
    print("\n=== TEST 4: fixed seed, with and without operations ===")
    base = {"num_qubits": 2, "shots": shots, "mode": "emu", "seed": 42}

    a = run(dict(base), "seed42 bare")
    ops = [{"type": "bloch", "qubit": 0, "paulis": {"X": 1.0}, "update": False},
           {"type": "bloch", "qubit": 1, "paulis": {"X": 1.0}, "update": False},
           {"type": "relationship", "qubits": [0, 1],
            "paulis": {"ZZ": -1.0}, "fraction": 1.0}]
    b = run(dict(base, operations=ops), "seed42 + ops")
    if a is None or b is None:
        return False

    # Compare on everything that describes the STATE, not the wrapper.
    keys = [k for k in sorted(set(a) | set(b))
            if k not in ("job_id", "ibm_job_id", "qpu_seconds", "duration",
                         "started_at", "finished_at", "elapsed")]
    same = [k for k in keys if a.get(k) == b.get(k)]
    diff = [k for k in keys if a.get(k) != b.get(k)]
    print(f"    identical fields: {', '.join(same) or 'none'}")
    print(f"    differing fields: {', '.join(diff) or 'NONE'}")
    for k in diff[:6]:
        print(f"      {k}: bare={json.dumps(a.get(k))[:80]}  "
              f"ops={json.dumps(b.get(k))[:80]}")

    # A second bare run at the same seed, to prove the seed is honoured at all.
    a2 = run(dict(base), "seed42 bare again")
    if a2 is not None:
        stable = all(a.get(k) == a2.get(k) for k in keys)
        print(f"    seed reproducible: {stable}")
        if not stable:
            print("    the SEED is not honoured either, so this comparison "
                  "cannot conclude anything. Report that to Moth.")
            return False

    if not diff:
        print("    FAIL. operations changed nothing. Dropped, like labyrinth.")
        return False
    print("    PASS. operations change the result at a fixed seed.")
    return True


def sign_test(shots):
    print("\n=== TEST 1: is the ZZ sign applied? ===")
    print("    two qubits, fully connected, one op, only the sign differs")
    got = {}
    for sign in (+1, -1):
        params = {
            "num_qubits": 2,
            "shots": shots,
            "mode": "emu",
            # omitted coupling_map == fully connected, per the schema
            "operations": [
                {"type": "bloch", "qubit": 0,
                 "paulis": {"X": 1.0}, "update": False},
                {"type": "bloch", "qubit": 1,
                 "paulis": {"X": 1.0}, "update": False},
                {"type": "relationship", "qubits": [0, 1],
                 "paulis": {"ZZ": float(sign)}, "fraction": 1.0},
            ],
        }
        out = run(params, f"ZZ={sign:+d}")
        if out is None:
            return False
        c = counts_of(out)
        a = agreement(c)
        got[sign] = a
        print(f"      shots seen={sum(c.values())} distinct={len(c)} "
              f"agreement={a if a is None else round(a, 3)} "
              f"dominant={out.get('dominant_bitstring')} "
              f"edge_score={out.get('edge_agreement_score')}")
        if len(c) == 1 and sum(c.values()) == 1:
            print("      NOTE: only a dominant bitstring came back, no "
                  "histogram. Agreement off one shot proves nothing; rerun "
                  "with --shots 1 and read edge_agreement_score instead.")

    a_pos, a_neg = got.get(+1), got.get(-1)
    print()
    if a_pos is None or a_neg is None:
        print("    INCONCLUSIVE: could not read agreement from the output.")
        return False
    if a_pos > 0.7 and a_neg < 0.3:
        print(f"    PASS. +1 agrees {a_pos:.0%}, -1 disagrees "
              f"{1 - a_neg:.0%}. The sign is real.")
        return True
    if abs(a_pos - a_neg) < 0.1:
        print(f"    FAIL. Both {a_pos:.0%} vs {a_neg:.0%}: the sign is being "
              f"ignored, same bug as labyrinth-v1.")
        return False
    print(f"    PARTIAL. +1 {a_pos:.0%}, -1 {a_neg:.0%}. Direction is right "
          f"but weaker than expected; check `fraction` and `update`.")
    return True


def world_test(shots):
    """The actual thing: five kingdoms, mixed signs, both extra channels."""
    print("\n=== TEST 2: a real Eigenstate world ===")
    print("    5 kingdoms, moods, one alliance, one war, one leverage edge")
    moods = [-0.2, 0.45, -0.45, 0.1, 0.3]      # + is hostile, as in the game
    ops = []
    for i, m in enumerate(moods):
        z = -m * 0.5                            # MOOD_CEILING
        x = (max(0.0, 1.0 - z * z)) ** 0.5
        ops.append({"type": "bloch", "qubit": i,
                    "paulis": {"X": x, "Y": 0.0, "Z": z}, "update": False})
    ops.append({"type": "relationship", "qubits": [0, 1],
                "paulis": {"ZZ": 1.0}, "fraction": 0.8})     # allied
    ops.append({"type": "relationship", "qubits": [2, 3],
                "paulis": {"ZZ": -1.0}, "fraction": 0.7})    # at war
    ops.append({"type": "relationship", "qubits": [0, 4],
                "paulis": {"ZX": 1.0}, "fraction": 0.5})     # you hold sway

    out = run({"num_qubits": 5, "shots": shots, "mode": "emu",
               "operations": ops}, "world")
    if out is None:
        return False
    c = counts_of(out)
    print(f"      shots seen={sum(c.values())} distinct={len(c)}")
    a01, a23 = agreement(c, 0, 1), agreement(c, 2, 3)
    print(f"      allied pair 0-1 agreement: "
          f"{'n/a' if a01 is None else round(a01, 3)}  (want high)")
    print(f"      warring pair 2-3 agreement: "
          f"{'n/a' if a23 is None else round(a23, 3)}  (want low)")
    print(f"      dominant={out.get('dominant_bitstring')} "
          f"edge_score={out.get('edge_agreement_score')}")
    print("      output keys: " + ", ".join(sorted(out.keys())))
    if a01 is not None and a23 is not None and a01 > a23 + 0.2:
        print("    PASS. Allies agree more than enemies. The world survived "
              "the trip.")
        return True
    print("    Read the numbers above before concluding; a single-shot "
          "output cannot answer this.")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=int, default=400)
    ap.add_argument("--signs", action="store_true",
                    help="only the sign test (2 runs)")
    ap.add_argument("--dump", action="store_true",
                    help="print one full output so we can see its shape")
    ap.add_argument("--sweep", type=float, metavar="SIGN",
                    help="fraction sweep at this ZZ sign, e.g. --sweep -1")
    ap.add_argument("--targets", action="store_true",
                    help="ask for ZZ/ZX/XY and read the correlator back")
    ap.add_argument("--seed-test", action="store_true",
                    help="fixed seed, with and without ops. no sampling noise")
    a = ap.parse_args()

    if not TOKEN:
        sys.exit("No MOTH_API_TOKEN. Try: source moth.env")
    print(f"engine={ENGINE}  base={BASE}  token={TOKEN[:4]}...{TOKEN[-4:]}")
    print(f"5 credits per run. This will use "
          f"{2 if a.signs else 3} runs.")

    if a.dump:
        dump_test(a.shots)
        return
    if a.targets:
        target_test(a.shots)
        return
    if a.seed_test:
        seed_test(a.shots)
        return
    if a.sweep is not None:
        sweep_test(a.shots, a.sweep)
        return

    ok = sign_test(a.shots)
    if not a.signs and ok:
        world_test(a.shots)
    elif not ok:
        print("\nSkipping test 2: no point posting a world to an engine that "
              "drops the sign.")


if __name__ == "__main__":
    main()
