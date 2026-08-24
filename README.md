# Eigenstate — the quantum brain

The game is a GameMaker project. This folder is the server it talks to: five
kingdoms held as five qubits, and outcomes decided by measuring them.

## Run it

```bash
cp moth.env.example moth.env      # put your Moth key in it
./start_server.sh
```

It prints `ready after Ns` and then the health line. Stop it with
`./stop_server.sh`. Both are safe to run twice.

```bash
curl -s localhost:5055/health
```

`file` in that response is the actual running file, so it cannot lie to you
about which version is up.

## The whole folder

| file | what it is |
|---|---|
| `eigenstate_server.py` | the game brain. Every endpoint the GML calls. |
| `eigenstate_backend.py` | the only file that knows about backends or credentials. |
| `start_server.sh` / `stop_server.sh` | idempotent, port-aware. |
| `moth.env` | your key and mode. Gitignored. |
| `tools/moth_engines.py` | list engines your key can see, and their schemas. |
| `tools/graph_probe.py` | prove the engine applies what you send it. |

Nothing else. If a file appears here that isn't in that table, it's scratch.

## The model

A kingdom is a qubit. Its mood is a Bloch Z, hostile pushing negative, and X
takes whatever is left so there is always room for correlation.

Between two kingdoms, three separate things on three different Pauli pairs:

- **bond** is `ZZ`, positive allied, negative at war
- **leverage** is `ZX` or `XZ` depending on who holds it, which is what makes
  sway asymmetric
- **debt** is `XY` or `YX`, a second channel that does not interfere with the
  first

Open oaths are folded into the bonds by `effective_bonds()`, so an unresolved
pact is a real correlation in the circuit rather than a note in a list. That is
what lets other kingdoms lean on it.

Reading the world is free and instant: `EIGENSTATE_BACKEND=exact` uses Moth's
`ExpectationValue`, so tomography is read rather than sampled. Deciding is a
single shot, and the collapse is the outcome.

## What runs where

**Everything reads locally.** Tomography happens every month, several times a
month. Sending that to a device would be both slow and pointless.

**Oaths can be decided on real hardware.** `world_ops()` serialises the entire
world — every mood, every signed bond, both leverage channels — into a
`graph-v1` `operations` list, the engine prepares that exact state, and one
shot settles the oath. `hw_log` records what went where.

**Battles and initiative stay local.** Not a limitation of the platform: a
battle is composed rotations where the *order* is the mechanic, and `graph-v1`
takes state targets, not raw gates. There is no honest way to send it. Initiative
is asked every month and a round trip per turn is not a game.

## graph-v1, and why not labyrinth-v1

`labyrinth-v1` accepts `initial_states` and `relationships` and silently drops
both. Two jobs with wildly different moods returned `bloch_pre` identical to
sixteen decimal places. Every edge came back sign `+1`, so wars could not be
expressed at all, and it forces grid adjacency, so a fully connected graph is
not even sayable.

`graph-v1` takes an ordered `operations` list that is QuantumGraph's own API,
and applies it. Verified by asking for a correlator and reading it back out of
the tomography the engine returns:

```
asked ZZ=+1  measured ZZ=+1.0      asked ZX=+1  measured ZX=+1.0
asked ZZ=-1  measured ZZ=-1.0      asked XY=+1  measured XY=+1.0
```

Reproduce that yourself with `python3 tools/graph_probe.py --targets`.

## Quirks worth knowing

- Cloudflare 403s non-browser user-agents on `api.mothquantum.com`
  (`browser_signature_banned`), so every request sends a browser UA. The
  engine's own published `requests` samples cannot work as written.
- The published `code_samples` paths are stale. Real ones are
  `/api/v1/engines/{id}/process` and `/api/v1/jobs/{id}/status`.
- `seed` is not honoured: identical seeds build different circuits. Nothing
  here relies on it. Reported.
- `update: false` on a Bloch op leaves the following relationship op working
  from a stale tomography, and it lands on the wrong axis. `world_ops()`
  refreshes between the two stages, same as `rebuild()` does locally.

## Cost and timing

5 credits per engine run, and emulation compute is free. `EIGENSTATE_HW_BUDGET`
caps runs per session and the guard counts *before* the call, because a job
that errors after submission may still have been billed.

`GET /timing` breaks a turn down into server time, network time, and the gap
between requests. It exists because three separate guesses about what made
turns slow were all wrong. Turns are ~200ms locally; one oath on the engine is
~3 HTTP calls.

## MOTH_MODE=qpu

Needs `IBM_QUANTUM_TOKEN` and `IBM_QUANTUM_INSTANCE`. Moth forwards them to IBM
as *your* credentials, so QPU time bills to your IBM account. Without them,
`qpu` refuses rather than quietly running on the emulator. A QPU job can sit in
IBM's queue for minutes; `EIGENSTATE_HW_TIMEOUT` bounds it and it falls back to
local rather than hanging the game.
