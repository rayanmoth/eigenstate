"""Vercel entry point.

Vercel's Python runtime looks for a WSGI callable named `app`, so this is
deliberately almost empty: put the repo root on the path, import the real
server, re-export its Flask app.

The root has to go on sys.path because eigenstate_server.py lives there
alongside the three vendored libraries, and its own path setup is relative
to its own __file__.
"""
import os, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eigenstate_server import app as _flask_app     # noqa: E402


class _RestorePath:
    """Give Flask back the path the client actually asked for.

    vercel.json rewrites /(.*) to this function. The build log warns that
    "internal rewrites in backend framework projects now route requests
    using the rewritten destination path", and that is exactly what happens:
    a request for /health arrives with PATH_INFO = "/api/index", Flask has no
    such route, and every endpoint 404s with Werkzeug's default page.

    The rewrite now carries the original path in a __p query parameter, and
    this puts it back before Flask sees the request. The fallback branch
    strips an /api/index prefix instead, so this keeps working whichever way
    the platform decides to behave. Cheap insurance against a behaviour that
    has already changed once.
    """

    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        qs = environ.get("QUERY_STRING") or ""

        if "__p=" in qs:
            keep, path = [], None
            for k, v in urllib.parse.parse_qsl(qs, keep_blank_values=True):
                if k == "__p":
                    path = v
                else:
                    keep.append((k, v))
            if path is not None:
                if not path.startswith("/"):
                    path = "/" + path
                environ["PATH_INFO"] = path
                environ["QUERY_STRING"] = urllib.parse.urlencode(keep)
        else:
            # no __p: either the platform passed the real path through, or it
            # passed the function's own path. Strip the latter if we see it.
            p = environ.get("PATH_INFO") or ""
            for prefix in ("/api/index", "/api"):
                if p == prefix or p.startswith(prefix + "/"):
                    environ["PATH_INFO"] = p[len(prefix):] or "/"
                    break

        return self.wsgi(environ, start_response)


_flask_app.wsgi_app = _RestorePath(_flask_app.wsgi_app)

app = _flask_app
application = _flask_app
