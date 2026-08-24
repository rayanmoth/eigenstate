"""Vercel entry point.

Vercel's Python runtime looks for a WSGI callable named `app` in the module
it builds, so this is deliberately almost empty: put the repo root on the
path, import the real server, re-export its Flask app.

The root has to go on sys.path because eigenstate_server.py lives there
alongside the three vendored libraries, and its own path setup is relative
to its own __file__. Importing it from a subdirectory works precisely
because of that.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eigenstate_server import app          # noqa: E402  (path setup first)

# Vercel invokes this. Nothing else in this file should ever grow.
application = app
