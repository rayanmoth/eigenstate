"""
eigenstate_backend.py -- the one place a backend or a credential is chosen.

Nothing else in the project imports urllib, reads an environment variable for a
token, or knows what a backend is. That is the point of the file.

TWO KNOBS, AND THE DISTINCTION IS THE WHOLE DESIGN
------------------------------------------------------------------
1. THE GRAPH BACKEND        EIGENSTATE_BACKEND, fixed at startup
   What READS the world: tomography, Bloch vectors, correlators. Runs every
   month, several times a month, so it has to be instant and free. Default
   `exact`, which uses Moth's ExpectationValue and does no sampling at all.

2. THE DECIDING MEASUREMENT  EIGENSTATE_HARDWARE, routed per call
   The single shot that settles an oath. Rare, dramatic, and the only place a
   player would care that a real quantum computer was involved.

You do not toggle the world. You toggle individual outcomes. That is what makes
"play locally and fast, decide THIS oath on real hardware" possible, and it is
why turns stay at ~200ms whether hardware is on or off.

    EIGENSTATE_BACKEND=exact    exact expectation values (default)
    EIGENSTATE_BACKEND=aer      local Aer, sampled

    EIGENSTATE_HARDWARE=off     everything local (default)
    EIGENSTATE_HARDWARE=moth    deciding shots go to Moth's graph-v1
    MOTH_MODE=emu|qpu           emu is Aer on their side and free; qpu is IBM
    MOTH_API_TOKEN=...          your Moth key
    IBM_QUANTUM_TOKEN=...       required for MOTH_MODE=qpu
    IBM_QUANTUM_INSTANCE=...    required for MOTH_MODE=qpu (CRN)

WHY graph-v1 AND NOT labyrinth-v1
------------------------------------------------------------------
labyrinth-v1 accepts `initial_states` and `relationships` and silently drops
both, so only one scalar (`fraction`) survived the trip and every edge came
back sign +1. Wars could not be expressed at all.

graph-v1 takes an ordered `operations` list that is literally QuantumGraph's
own API, and it applies it. Verified by asking for a correlator and reading it
back out of the tomography the engine returns:

    asked ZZ=+1  measured ZZ=+1.0     asked ZX=+1  measured ZX=+1.0
    asked ZZ=-1  measured ZZ=-1.0     asked XY=+1  measured XY=+1.0

So the entire world model travels: moods as Bloch vectors, signed ZZ for
alliances and wars, ZX for leverage, XY for debt. Nothing is approximated and
nothing is refused.

KNOWN ENGINE QUIRKS, both worked around here
------------------------------------------------------------------
- Cloudflare 403s any non-browser User-Agent on api.mothquantum.com
  (error_name browser_signature_banned), so every request here sends a browser
  UA. The engine's own published `requests` code samples cannot work as
  written for this reason.
- The published code_samples use /v1/generation/{id}/process and /v1/status/{id}.
  The real paths are /api/v1/engines/{id}/process and /api/v1/jobs/{id}/status.
- `seed` is not honoured: identical seeds produce different circuits. We do not
  rely on it. Reported to Moth.
- `update: false` on a Bloch op means the following relationship op computes
  its rotation from a stale tomography and lands on the wrong axis. Always
  refresh between the mood stage and the relationship stage. world_ops() in the
  server does; this cost me an afternoon.
"""

import os
import json
import time

DEFAULT_MODE = "exact"
ENGINE_ID = os.environ.get("MOTH_ENGINE", "graph-v1")

# Cloudflare bans script user-agents on this host. Not optional.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
              "Safari/537.36")


# ======================================================================
# CREDENTIALS
#
# The player supplies their own key. No key means local simulation with
# nothing missing. Rules, not negotiable: the token lives in memory, on disk
# only if the player opts in, is never returned by any endpoint except masked,
# never written to a log or an error string, and goes to exactly one host.
# ======================================================================
_runtime = {"token": None, "url": None}

CRED_DIR = os.path.join(os.path.expanduser("~"), ".eigenstate")
CRED_FILE = os.path.join(CRED_DIR, "credentials.json")


def set_credentials(token, url=None, remember=False):
    _runtime["token"] = (token or "").strip() or None
    _runtime["url"] = (url or "").strip() or None
    reset_hardware()
    if remember and _runtime["token"]:
        _save_credentials()


def clear_credentials(forget_file=True):
    _runtime.update({"token": None, "url": None})
    reset_hardware()
    if forget_file and os.path.exists(CRED_FILE):
        try:
            os.remove(CRED_FILE)
        except OSError:
            pass


def token_value():
    return _runtime["token"] or os.environ.get("MOTH_API_TOKEN")


def token_present():
    return bool(token_value())


def token_masked():
    """Enough to tell which key is loaded, useless if it leaks."""
    t = token_value()
    if not t:
        return None
    if len(t) <= 8:
        return "*" * len(t)
    return t[:4] + "..." + t[-4:]


def _save_credentials():
    try:
        os.makedirs(CRED_DIR, mode=0o700, exist_ok=True)
        with open(CRED_FILE, "w") as f:
            json.dump({k: v for k, v in _runtime.items() if v}, f)
        os.chmod(CRED_FILE, 0o600)
    except OSError:
        pass          # a disk problem must never break the game


def load_saved_credentials():
    try:
        with open(CRED_FILE) as f:
            data = json.load(f)
        _runtime["token"] = data.get("token")
        _runtime["url"] = data.get("url")
        return bool(_runtime["token"])
    except (OSError, ValueError):
        return False


# ======================================================================
# MODES
# ======================================================================
def mode():
    return os.environ.get("EIGENSTATE_BACKEND", DEFAULT_MODE).strip().lower()


def hw_mode():
    """A pasted key implies hardware. Explicit env always wins, so
    EIGENSTATE_HARDWARE=off still means off even with a key loaded."""
    env = os.environ.get("EIGENSTATE_HARDWARE", "").strip().lower()
    if env:
        return env
    return "moth" if _runtime["token"] else "off"


def n_qubits():
    return int(os.environ.get("EIGENSTATE_QUBITS", "5"))


def hw_timeout():
    return float(os.environ.get("EIGENSTATE_HW_TIMEOUT", "45"))


def moth_mode():
    return os.environ.get("MOTH_MODE", "emu").strip().lower()


# ======================================================================
# GRAPH BACKENDS -- what reads the world, always local
# ======================================================================
def aer_backend():
    from qiskit_aer import AerSimulator
    return AerSimulator()


def exact_backend():
    """No sampling at all.

    Moth's fork ships ExpectationValue(n, k=2) and QuantumGraph special-cases
    it: instead of running tomography circuits it reads Pauli expectation
    values directly. ~10x faster turns and no bonds flickering across the
    ALLY/WAR line on shot noise alone.

    This costs nothing in honesty. Sampled and exact are both classical
    simulation on a laptop; Aer's shot noise is simulated, not hardware noise.
    The claim that has to stay true still does: deciding shots never come from
    here."""
    from quantumgraph.ExpectationValue import ExpectationValue
    return ExpectationValue(n_qubits(), k=2)


def make_backend():
    m = mode()
    if m in ("aer", "sim", "local"):
        return aer_backend()
    if m in ("exact", "expectation", "ev"):
        return exact_backend()
    raise RuntimeError(
        f"Unknown EIGENSTATE_BACKEND: {m!r}. Use 'exact' or 'aer'. "
        f"Reading the world on a device is not supported and should not be: "
        f"tomography is 15 circuits a pass, several passes a month.")


def graph_settings():
    m = mode()
    if m in ("exact", "expectation", "ev"):
        return {"shots": 0, "rebuild_passes": 2, "sampled": False,
                "note": "exact expectation values, no sampling"}
    return {"shots": 2000, "rebuild_passes": 2, "sampled": True,
            "note": "local Aer, sampled"}


# ======================================================================
# THE DECIDING MEASUREMENT
# ======================================================================
_engine = None
_hw_error = None


def reset_hardware():
    global _engine, _hw_error
    _engine = None
    _hw_error = None


class GraphEngine:
    """Moth's graph-v1: a QuantumGraph you drive over HTTP.

    One public method. You hand it the same operations list the local
    rebuild() applies, it prepares that exact state on Aer or an IBM QPU,
    samples it, and hands back bitstrings plus the tomography it measured so
    you can verify the state it actually built.
    """

    OK = ("completed", "succeeded", "success", "done", "finished")
    BAD = ("failed", "error", "cancelled", "canceled")

    net_calls = 0
    net_seconds = 0.0

    def __init__(self, token, url=None, engine=None, timeout=None):
        self.token = token
        self.url = (url or _runtime["url"] or os.environ.get("MOTH_API_URL")
                    or "https://api.mothquantum.com").rstrip("/")
        self.engine = engine or ENGINE_ID
        self.timeout = timeout or hw_timeout()
        self.label = self.engine

    def available(self):
        return bool(self.token)

    # -- plumbing ------------------------------------------------------
    def _req(self, path, payload=None):
        import urllib.request
        h = {"Authorization": "Bearer " + self.token,
             "Accept": "application/json",
             "User-Agent": BROWSER_UA}
        data = None
        if payload is not None:
            h["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=h,
                                     method="POST" if data else "GET")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        finally:
            GraphEngine.net_calls += 1
            GraphEngine.net_seconds += time.time() - t0

    def _qpu_params(self, params, mode_):
        if mode_ != "qpu":
            return
        ibm = os.environ.get("IBM_QUANTUM_TOKEN")
        inst = os.environ.get("IBM_QUANTUM_INSTANCE")
        if not ibm:
            raise RuntimeError(
                "MOTH_MODE=qpu needs IBM_QUANTUM_TOKEN. The engine takes "
                "qpu_token as a parameter and forwards it to IBM with YOUR "
                "credentials, not Moth's.")
        if not inst:
            raise RuntimeError(
                "MOTH_MODE=qpu needs IBM_QUANTUM_INSTANCE (the CRN); the "
                "engine's schema marks it required.")
        params["qpu_token"] = ibm
        params["qpu_instance"] = inst
        bn = os.environ.get("IBM_BACKEND")
        if bn:
            params["backend_name"] = bn

    # -- the one call --------------------------------------------------
    def measure(self, operations, n, shots=1, coupling_map=None, mode_=None):
        """Prepare `operations` on `n` qubits, sample it, return everything.

        coupling_map omitted means fully connected, which is QuantumGraph's
        default and what the game wants: any kingdom can bond with any other.
        labyrinth-v1 forced grid adjacency; this does not.
        """
        mode_ = mode_ or moth_mode()
        params = {
            "num_qubits": int(n),
            "shots": max(1, int(shots)),
            "mode": mode_,
            "operations": operations,
        }
        if coupling_map:
            params["coupling_map"] = coupling_map
        self._qpu_params(params, mode_)

        t0 = time.time()
        c0 = GraphEngine.net_calls
        sub = self._req(f"/api/v1/engines/{self.engine}/process",
                        {"params": params})
        job = sub.get("job_id")
        if not job:
            raise RuntimeError(f"no job_id from {self.engine}: "
                               f"{json.dumps(sub)[:300]}")

        deadline = time.time() + self.timeout
        delay = 0.4                     # first poll fast: emu often finishes
        while time.time() < deadline:   # before a 1s sleep would even end
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)
            st = self._req(f"/api/v1/jobs/{job}/status")
            s = str(st.get("status", "")).lower()
            if s in self.BAD:
                err = (st.get("error") or {})
                raise RuntimeError(f"job {s}: {err.get('type')} "
                                   f"{err.get('message')}")
            if s in self.OK:
                break
        else:
            raise TimeoutError(f"job {job} unfinished after "
                               f"{self.timeout:.0f}s")

        res = self._req(f"/api/v1/jobs/{job}/result")
        out = (res.get("result") or {}).get("output") or {}
        meas = out.get("measurements") or []
        if not meas:
            raise RuntimeError(f"no measurements in result for {job}")

        # measurements is a histogram, ordered most-frequent first, with
        # bitstring/count/probability. For a deciding shot we send shots=1 and
        # take the single entry; for verification we keep the whole thing.
        rev = os.environ.get("MOTH_BIT_ORDER", "").lower().startswith("rev")

        def fix(b):
            b = str(b)
            return b[::-1] if rev else b

        counts = {fix(m.get("bitstring", "")): int(m.get("count", 0))
                  for m in meas if m.get("bitstring")}
        return {
            "bits": fix(meas[0].get("bitstring", "")),
            "counts": counts,
            "tomography": out.get("tomography"),
            "job_id": job,
            "ibm_job_id": out.get("ibm_job_id"),
            "backend": out.get("backend"),
            "mode": out.get("mode") or mode_,
            "shots": out.get("shots"),
            "engine": self.engine,
            "wall_s": round(time.time() - t0, 2),
            "http_calls": GraphEngine.net_calls - c0,
        }

    def where(self, meta):
        """A human-readable 'measured on' string. Never claims a QPU it did
        not get: emu says emu."""
        meta = meta or {}
        if meta.get("ibm_job_id"):
            return f"{meta.get('backend') or 'ibm'} via moth/{self.engine}"
        return f"moth/{self.engine} ({meta.get('mode') or moth_mode()})"


def graph_engine():
    """The engine, or None. None is a normal state, not an error."""
    global _engine, _hw_error
    if hw_mode() in ("off", "", "none"):
        return None
    tok = token_value()
    if not tok:
        _hw_error = "no API key"
        return None
    if _engine is None:
        try:
            _engine = GraphEngine(tok)
            _hw_error = None
        except Exception as e:
            _hw_error = f"{type(e).__name__}: {e}"
            return None
    return _engine


def hardware_status():
    eng = graph_engine()
    ready = bool(eng is not None and eng.available())
    return {
        "mode": hw_mode(),
        "ready": ready,
        "engine": ENGINE_ID if ready else None,
        "moth_mode": moth_mode(),
        "qpu_ready": bool(os.environ.get("IBM_QUANTUM_TOKEN")
                          and os.environ.get("IBM_QUANTUM_INSTANCE")),
        "error": _hw_error,
        "timeout": hw_timeout(),
        "key": token_masked(),
        "has_key": token_present(),
        "saved": os.path.exists(CRED_FILE),
        "net": {"http_calls": GraphEngine.net_calls,
                "http_total_s": round(GraphEngine.net_seconds, 2)},
    }
