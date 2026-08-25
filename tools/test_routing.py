#!/usr/bin/env python3
"""Prove api/index.py hands Flask the right path under every routing
behaviour Vercel might give us.

The rewrite carries the original path in __p. But the platform's behaviour
here has already changed once (the build log says so), so the middleware
also recovers from an /api/index prefix and from the path being passed
through untouched. All three are tested, because the failure mode is a
Werkzeug 404 on every endpoint and that looks like a code problem rather
than a routing one.
"""
import sys, os, io, contextlib, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, HERE)

# test_stateless calls sys.exit() when it finishes, so the import has to
# survive that. We only want its stubs, not its verdict.
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import test_stateless      # installs the qiskit/QuantumGraph stubs
except SystemExit:
    pass

spec = importlib.util.spec_from_file_location("vercel_entry", "api/index.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
c = mod.app.test_client()

OK = FAIL = 0


def chk(name, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  pass  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


print("=== A. the rewrite as configured ===")
r = c.get("/api/index?__p=health")
chk("200", r.status_code == 200, r.status_code)
chk("reached /health", (r.get_json() or {}).get("version") == "clean-4",
    r.get_json())

print("\n=== B. __p with a leading slash ===")
chk("200", c.get("/api/index?__p=/health").status_code == 200)

print("\n=== C. fallback: function path, no __p ===")
chk("/api/index/health", c.get("/api/index/health").status_code == 200)
chk("/api/health",       c.get("/api/health").status_code == 200)

print("\n=== D. fallback: real path passed through ===")
chk("/health", c.get("/health").status_code == 200)

print("\n=== E. POST with a body through the rewrite ===")
r = c.post("/api/index?__p=newgame", json={})
chk("newgame 200", r.status_code == 200, r.status_code)
chk("returns a world", "world" in (r.get_json() or {}))
w = (r.get_json() or {}).get("world")
r = c.post("/api/index?__p=turn", json={"world": w, "year": 2, "events": []})
chk("turn 200", r.status_code == 200, r.status_code)
chk("world advanced", (r.get_json() or {})["world"]["current_year"] == 2)

print("\n=== F. a real query param survives alongside __p ===")
chk("200", c.get("/api/index?__p=health&foo=bar").status_code == 200)

print(f"\n{'=' * 46}\n  {OK} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)