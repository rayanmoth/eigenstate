# Deploying the Eigenstate server to Vercel

The server is a pure function of the world state as of `clean-3`, so it can
sit on a serverless host. Nothing is remembered between requests; the client
sends the world in and gets the world back.

## What is in the repo for this

```
api/index.py        Vercel entry point. Puts the root on sys.path,
                    re-exports the Flask app. Should never grow.
vercel.json         maxDuration 300, memory 2048, includeFiles "**",
                    and a rewrite so every path reaches the function.
requirements.txt    flask, qiskit, qiskit-aer, requests
.vercelignore       keeps venv, logs, tools and moth.env out of the bundle
```

`includeFiles: "**"` is deliberate. The three vendored libraries are not on
PyPI, and `eigenstate_server.py` finds them by putting its own directory's
subfolders on `sys.path`. Python dependency tracing will not follow that, so
the whole tree ships and `.vercelignore` does the trimming. If the bundle
ever gets tight, narrow this to the specific folders rather than trusting
tracing.

## Deploy

```bash
cd ~/Downloads/eigenstate
npx vercel login
npx vercel                 # preview deployment first
```

Then set the Moth credentials as environment variables in the Vercel
dashboard rather than shipping `moth.env` (which `.vercelignore` excludes on
purpose). Whatever names `eigenstate_backend.py` reads, set those.

Also worth setting:

```
EIGENSTATE_HW_SCOPE   = off       # emulation only for a public deployment
EIGENSTATE_HW_BUDGET  = 25        # the credit guard, server-side
EIGENSTATE_CORS_ORIGIN = *        # or your game's origin, once you have one
```

**Turn the hardware path off before anything is public.** graph-v1 is 5
credits a run. One player is a demo, a hundred is a bill, and the budget
guard is per-instance so it does not bound total spend across a fleet.

Then:

```bash
curl -s https://<your-deployment>.vercel.app/health
```

Expect `"version":"clean-3"` and `"stateless":true`. The first request pays
the qiskit import, roughly 30 seconds; the limit is 300, so it fits. With
Fluid Compute the instance is reused and subsequent requests skip it.

Finally, point the game at it: one line in `Create_0`.

```gml
SERVER_BASE = "https://<your-deployment>.vercel.app";
```

## What I could not test from here

I have no Vercel account in this sandbox, so the config is best-effort and
the deploy itself is unverified. Two things are most likely to need a nudge:

**Bundle size.** Measured locally the dependency set is about 250 MB against
the 500 MB Python limit. That should hold, but a transitive dependency could
change it. If the build fails on size, `VERCEL_SUPPORT_LARGE_FUNCTIONS=1`
raises the ceiling to 5 GB.

**The vendored import.** If `quantumgraph` fails to import in the build, the
server calls `sys.exit(1)`, which on a serverless host surfaces as an opaque
crash rather than the nice diagnostic it prints locally. Check the build log
for the "Missing module" line. The likely fix is adding
`qiskit-experiments` and `qiskit-ibm-runtime` to `requirements.txt`, which
MothQuantumGraph is supposed to make unnecessary but QuantumGraph is not.

Run `npx vercel dev` first if you want to catch both locally before pushing.

## What is verified

`tools/test_stateless.py`, 45 assertions, stubs out qiskit and QuantumGraph
so it runs anywhere in about a second:

```bash
python3 tools/test_stateless.py
```

It covers the world round-trip, an oath surviving several turns, two players
interleaving without colliding, replay determinism, malformed blobs giving
400 rather than 500, a rejected payload not poisoning the next request, the
rebuild cache firing only on a changed world, CORS headers, and the credit
guard not being resettable by a client. Run it before every deploy.
