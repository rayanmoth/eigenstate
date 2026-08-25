#!/usr/bin/env python3
"""Fix a GameMaker HTML5 export: canvas sizing, black page, real fullscreen.

GameMaker regenerates index.html on every export, so hand-editing it means
redoing the same three fixes forever. Run this instead:

    python3 tools/patch_html5.py <export-dir>

It is idempotent -- run it twice and nothing doubles up -- so it is safe to
put in a build script or just run after every export.

WHAT IT FIXES

1. SIZING. GameMaker anchors the canvas top-left at its authored pixel size
   and leaves the rest of the page white. The CSS below scales the canvas to
   whichever window dimension runs out first, keeps the aspect ratio exactly,
   centres it, and paints everything else black. image-rendering: pixelated
   is the one that matters most for this game -- without it the browser
   smooths a 640x360 pixel-art upscale into mush.

2. FULLSCREEN. window_set_fullscreen() does not work reliably from GML in
   HTML5, and cannot work at all when called on load: browsers only grant
   fullscreen from inside a genuine user gesture, so a call from
   settings_apply() during startup is refused before it reaches the game.
   The fix has to live in the page. Double-click the canvas, or use the
   button in the corner.

   Note F is NOT bound here on purpose -- F is "lean" in Eigenstate and
   stealing it would break the oath board.
"""
import sys, os, re

MARKER = "<!-- eigenstate-html5-patch -->"

BLOCK = """<!-- eigenstate-html5-patch -->
<style>
  html, body {
    margin: 0; padding: 0; height: 100%;
    background: #000; overflow: hidden;
  }
  body { display: grid; place-items: center; }

  /* GameMaker wraps the canvas in a div; neither should add layout of its own */
  #gm4html5_div_id, div[align="center"] {
    display: contents;
  }

  /* Scale to whichever dimension runs out first, exact 16:9, no smoothing.
     640x360 is the game's GUI size. */
  canvas {
    display: block;
    width:  min(100vw, calc(100vh * 640 / 360));
    height: min(100vh, calc(100vw * 360 / 640));
    image-rendering: pixelated;
    image-rendering: crisp-edges;   /* older Firefox */
    outline: none;
  }

  #eg-full {
    position: fixed; top: 10px; right: 12px; z-index: 10;
    font: 15px/1 system-ui, sans-serif;
    color: #8a8f9a; background: rgba(0,0,0,.35);
    border: 1px solid #2a2f3a; border-radius: 4px;
    padding: 5px 8px; cursor: pointer;
    opacity: .25; transition: opacity .2s;
  }
  #eg-full:hover { opacity: 1; color: #e8e8ee; }
</style>
<script>
(function () {
  function full() {
    var d = document, e = d.documentElement;
    var on = d.fullscreenElement || d.webkitFullscreenElement;
    try {
      if (on) (d.exitFullscreen || d.webkitExitFullscreen).call(d);
      else (e.requestFullscreen || e.webkitRequestFullscreen).call(e);
    } catch (err) { /* refused; nothing useful to do about it */ }
  }

  window.eigenstateFullscreen = full;

  window.addEventListener('load', function () {
    // A button, because it is discoverable and cannot collide with a game
    // key. Nearly transparent until you reach for it.
    var b = document.createElement('div');
    b.id = 'eg-full';
    b.textContent = '\\u26F6';
    b.title = 'Fullscreen (or double-click the game)';
    b.addEventListener('click', function (ev) { ev.preventDefault(); full(); });
    document.body.appendChild(b);

    // Double-click anywhere on the game. Both of these are real user
    // gestures, which is the whole reason this lives here and not in GML.
    var c = document.querySelector('canvas');
    if (c) c.addEventListener('dblclick', full);

    // Stop the page scrolling when the game uses the arrow keys or space.
    window.addEventListener('keydown', function (ev) {
      if ([' ', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].indexOf(ev.key) >= 0)
        ev.preventDefault();
    }, { passive: false });
  });
})();
</script>
"""


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        print("usage: patch_html5.py <export-dir>")
        return 2

    d = sys.argv[1]
    path = os.path.join(d, "index.html")
    if not os.path.isfile(path):
        print(f"no index.html in {d}")
        print("point this at the folder GameMaker exported, the one with")
        print("index.html and html5game/ inside it.")
        return 1

    html = open(path, encoding="utf-8").read()

    if MARKER in html:
        # strip the old block so re-running always ends with exactly one
        html = re.sub(re.escape(MARKER) + r".*?</script>\s*", "",
                      html, flags=re.S)
        print("removed the previous patch")

    if "</head>" not in html:
        print("index.html has no </head>; refusing to guess where to put this")
        return 1

    html = html.replace("</head>", BLOCK + "</head>", 1)
    open(path, "w", encoding="utf-8").write(html)

    print(f"patched {path}")
    print("  canvas scales to the window, aspect ratio kept, no smoothing")
    print("  page background black")
    print("  fullscreen: corner button, or double-click the game")
    return 0


if __name__ == "__main__":
    sys.exit(main())
