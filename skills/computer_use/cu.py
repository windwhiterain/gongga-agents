# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "comtypes; sys_platform == 'win32'",
#   "pyobjc-framework-Quartz; sys_platform == 'darwin'",
# ]
# ///
"""One command line for driving a desktop, on whichever platform this is.

The point of this file is the interface, not the mechanism: `shot`, `tree`,
`click`, `type`, `key`, `focus`, `windows`, `doctor` mean the same thing
everywhere, so only the backend changes. Backends live in cuwin.py, cumac.py and
culinux.py.

Coordinates are screen pixels in the same space the UI tree reports. That is only
true because every backend makes its process DPI aware on Windows and works in
points-in-a-retina-aware context on macOS; a scaled screenshot has to be divided
back by --scale before it can be clicked.
"""

import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_backend():
    """Pick the module for this platform, and say so plainly when there is none."""
    if sys.platform == "win32":
        import cuwin
        return cuwin, "windows"
    if sys.platform == "darwin":
        import cumac
        return cumac, "macos"
    if sys.platform.startswith("linux"):
        import culinux
        return culinux, "linux"
    raise SystemExit("unsupported platform: %s" % sys.platform)


def out(line):
    print(line, flush=True)


def note(line):
    """Diagnostics go to stderr, which the harness still shows but keeps separate."""
    print(line, file=sys.stderr, flush=True)


def shown_id(backend, win):
    """An id in the form its own platform displays it."""
    formatter = getattr(backend, "format_id", None)
    return formatter(win["hwnd"]) if formatter else str(win["hwnd"])


def claim_target(backend, spec):
    """Focus the named window, or report who owns the keyboard right now.

    Never send input without knowing the target: Windows and macOS both refuse
    background processes the foreground, and a silent refusal means the
    keystrokes land wherever the user is looking.
    """
    if spec is None:
        win = backend.foreground()
        out("target: foreground hwnd=%s %r" % (shown_id(backend, win), win["title"]))
        return win
    win = backend.resolve_window(spec)
    if not backend.force_foreground(win["hwnd"]):
        raise SystemExit("could not focus %s %r; refusing to send input blind"
                         % (win["hwnd"], win["title"]))
    out("target: %s %r" % (shown_id(backend, win), win["title"]))
    return win


def default_shot_path():
    stamp = time.strftime("%H%M%S")
    return os.path.join(tempfile.gettempdir(), "cu-%s-%d.png" % (stamp, os.getpid() % 1000))


def doctor():
    """Can this skill drive this desktop at all, and on which backend."""
    backend, name = load_backend()
    checks, ok = backend.doctor()
    out("backend: %s (%s)" % (name, backend.__name__))
    for text, good in checks:
        out("  %s %s" % ("ok  " if good else "FAIL", text))
    stated = getattr(backend, "VERIFIED", None)
    if stated:
        out("  note %s" % stated)
    out("verdict: %s" % ("usable" if ok else "NOT usable, see above"))
    return 0 if ok else 1


def windows():
    """Every top-level window: id, pid, position, size, title; `*` is the foreground."""
    backend, _name = load_backend()
    current = backend.foreground()["hwnd"]
    for w in backend.list_windows():
        mark = "*" if w["hwnd"] == current else " "
        title = w["title"]
        if len(title) > 55:
            title = title[:52] + "..."
        # The backend decides how an id looks: Windows hwnds are decimal,
        # X11 ids are conventionally hexadecimal.
        shown = backend.format_id(w["hwnd"]) if hasattr(backend, "format_id") else str(w["hwnd"])
        out("%s%9s %7d @%d,%d %dx%d  %s"
            % (mark, shown, w["pid"], w["x"], w["y"], w["w"], w["h"], title))
    return 0


def shot(window=None, scale: float = 1.0, out_path=None):
    """A screenshot of the screen, or of `window`, printed as `@image(<path>)`.

    The path is made up when none is given. The line on stderr is the mapping
    from image pixels to screen pixels, which is what a click computed from a
    window capture needs.
    """
    backend, _name = load_backend()
    win = backend.resolve_window(window) if window else None
    path = out_path or default_shot_path()
    info = backend.capture(path, window=win, scale=scale)
    sw, sh = info["src"][2], info["src"][3]
    # This line is the mapping from image pixels to screen pixels, and the only
    # cheap source of it. Without the origin, a click computed from a window
    # capture lands a whole control away. stderr is merged into what the model
    # reads, so it costs tokens and has to earn them.
    note("captured %dx%d src=%dx%d@%d,%d scale=%g"
         % (info["w"], info["h"], sw, sh, info["src"][0], info["src"][1], scale))
    if any(c.isspace() for c in path):
        note("!! %r contains a space; the @ reference cannot be parsed -- "
             "capture somewhere else" % path)
    if ")" in path:
        note("!! %r contains ')'; that closes the @image(...) reference, so the "
             "server reads a shorter path -- capture somewhere else" % path)
    # The spelling the server parses: `@image(<path>)`, the same meta the SPEC
    # defines. A bare `@<path>` and the older `@image: <path>` are both read as
    # prose now -- no prefix, no attachment, and nothing warns about it.
    out("@image(%s)" % path)
    return 0


def tree(window=None, depth: int = 5, limit: int = 150):
    """The control tree of a window: what can be clicked, and what it is called."""
    backend, _name = load_backend()
    header, lines = backend.tree(window, depth=depth, limit=limit)
    out(header)
    for line in lines:
        out(line)
    return 0


def click(x: int, y: int, double: bool = False, right: bool = False, window=None):
    """Click screen pixels, in the same space the control tree reports."""
    backend, _name = load_backend()
    claim_target(backend, window)
    backend.click(x, y, double=double, right=right)
    out("clicked %d,%d" % (x, y))
    return 0


def type_text(text, window=None):
    """Type into `window`, or into whatever holds the focus."""
    backend, _name = load_backend()
    claim_target(backend, window)
    want = len(text) * 2
    sent = backend.type_text(text)
    out("typed %d chars (delivered %d/%d events)" % (len(text), sent, want))
    if sent != want:
        raise SystemExit("SendInput dropped events (delivered %d of %d)" % (sent, want))
    return 0


def key(combo, window=None):
    """Send one key combination, spelled `ctrl+l`."""
    backend, _name = load_backend()
    claim_target(backend, window)
    sent = backend.send_keys(combo)
    events = (len(combo.split("+")) * 2)
    out("sent %s (delivered %d/%d events)" % (combo, sent, events))
    if sent != events:
        raise SystemExit("SendInput dropped events (delivered %d of %d)" % (sent, events))
    return 0


def focus(window):
    """Bring a window to the front."""
    backend, _name = load_backend()
    win = backend.resolve_window(window)
    if not backend.force_foreground(win["hwnd"]):
        raise SystemExit("could not bring %s %r to the front"
                         % (shown_id(backend, win), win["title"]))
    out("focused %s %r" % (shown_id(backend, win), win["title"]))
    return 0


def main(argv):
    """The command line, kept for a human at a shell: the same calls, by name."""
    parser = argparse.ArgumentParser(prog="cu", description="drive the desktop")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="check that this skill can work here")
    sub.add_parser("windows", help="list top-level windows")
    p = sub.add_parser("shot", help="capture the screen or one window")
    p.add_argument("--window")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--out")
    p = sub.add_parser("tree", help="UIA control tree of a window")
    p.add_argument("--window")
    p.add_argument("--depth", type=int, default=5)
    p.add_argument("--max", dest="limit", type=int, default=150)
    p = sub.add_parser("click", help="click at screen coordinates")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--double", action="store_true")
    p.add_argument("--right", action="store_true")
    p.add_argument("--window")
    p = sub.add_parser("type", help="type text into the focused window")
    p.add_argument("text", nargs="+")
    p.add_argument("--window")
    p = sub.add_parser("key", help="send a key combination")
    p.add_argument("combo")
    p.add_argument("--window")
    p = sub.add_parser("focus", help="bring a window to the front")
    p.add_argument("window")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "windows":
        return windows()
    if args.cmd == "shot":
        return shot(args.window, args.scale, args.out)
    if args.cmd == "tree":
        return tree(args.window, args.depth, args.limit)
    if args.cmd == "click":
        return click(args.x, args.y, args.double, args.right, args.window)
    if args.cmd == "type":
        return type_text(" ".join(args.text), args.window)
    if args.cmd == "key":
        return key(args.combo, args.window)
    if args.cmd == "focus":
        return focus(args.window)

    parser.print_help()
    return 2


if __name__ == "__main__":
    # A backend raises for the errors a caller can act on (unknown window, missing
    # tool, no display). Printing a traceback for those hides the one useful line.
    try:
        sys.exit(main(sys.argv[1:]))
    except (LookupError, RuntimeError, ValueError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(1)
