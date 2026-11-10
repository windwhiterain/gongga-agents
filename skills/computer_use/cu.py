# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "comtypes; sys_platform == 'win32'",
#   "pyvda; sys_platform == 'win32'",
#   "pyobjc-framework-Quartz; sys_platform == 'darwin'",
# ]
# ///
"""One command line for driving a desktop, on whichever platform this is.

The point of this file is the interface, not the mechanism: `shot`, `tree`,
`click`, `type`, `key`, `focus`, `windows`, `doctor` mean the same thing
everywhere, so only the backend changes. Backends live in cuwin.py, cumac.py and
culinux.py.

`desktop` is the exception: only Windows has virtual desktops this can drive
through an interface rather than by guessing hotkeys, so the other backends say
so instead of pretending.

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


def require_desktops(backend, name):
    """Virtual desktops are Windows only; say so rather than fail on a missing attribute."""
    if not hasattr(backend, "list_desktops"):
        raise RuntimeError("the %s backend has no virtual desktops; this command is Windows only" % name)


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


def main(argv):
    parser = argparse.ArgumentParser(prog="cu", description="drive the desktop")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="check that this skill can work here")
    p = sub.add_parser("windows", help="list top-level windows")
    p.add_argument("--all-desktops", action="store_true",
                   help="include windows on other virtual desktops, with their number (Windows)")
    desktop = sub.add_parser("desktop", help="list and drive virtual desktops (Windows)")
    actions = desktop.add_subparsers(dest="action")
    actions.add_parser("ls", help="list desktops; also what a bare `cu desktop` does")
    p = actions.add_parser("new", help="create a desktop without switching to it")
    p.add_argument("--name")
    p = actions.add_parser("go", help="switch to a desktop")
    p.add_argument("number", type=int)
    p = actions.add_parser("rm", help="remove a desktop; its windows go to the fallback")
    p.add_argument("number", type=int)
    p.add_argument("--fallback", type=int, help="desktop to land on if removing the current one")
    p = actions.add_parser("name", help="rename a desktop")
    p.add_argument("number", type=int)
    p.add_argument("name")
    p = actions.add_parser("move", help="move a window to a desktop")
    p.add_argument("window")
    p.add_argument("number", type=int)
    p = actions.add_parser("pin", help="show a window on every desktop")
    p.add_argument("window")
    p = actions.add_parser("unpin", help="stop showing a window on every desktop")
    p.add_argument("window")
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

    backend, name = load_backend()

    if args.cmd == "doctor":
        checks, ok = backend.doctor()
        out("backend: %s (%s)" % (name, backend.__name__))
        for text, good in checks:
            out("  %s %s" % ("ok  " if good else "FAIL", text))
        stated = getattr(backend, "VERIFIED", None)
        if stated:
            out("  note %s" % stated)
        out("verdict: %s" % ("usable" if ok else "NOT usable, see above"))
        return 0 if ok else 1

    if args.cmd == "windows":
        if args.all_desktops:
            require_desktops(backend, name)
        wins = backend.list_windows(all_desktops=True) if args.all_desktops else backend.list_windows()
        current = backend.foreground()["hwnd"]
        for w in wins:
            mark = "*" if w["hwnd"] == current else " "
            title = w["title"]
            if len(title) > 55:
                title = title[:52] + "..."
            # The backend decides how an id looks: Windows hwnds are decimal,
            # X11 ids are conventionally hexadecimal.
            shown = backend.format_id(w["hwnd"]) if hasattr(backend, "format_id") else str(w["hwnd"])
            # Empty without the flag, so the line stays byte-identical to before it.
            column = ""
            if args.all_desktops:
                number = w.get("desktop")
                column = "d%-3s" % (number if number is not None else "?")
            out("%s%9s %7d %s@%d,%d %dx%d  %s"
                % (mark, shown, w["pid"], column, w["x"], w["y"], w["w"], w["h"], title))
        return 0

    if args.cmd == "desktop":
        require_desktops(backend, name)
        action = args.action or "ls"
        if action == "ls":
            for d in backend.list_desktops():
                out("%s%2d  %-24s %s" % ("*" if d["current"] else " ", d["number"],
                                         d["name"] or "(unnamed)", d["id"]))
            return 0
        if action == "new":
            d = backend.create_desktop(args.name)
            out("created desktop %d %s; still on desktop %d"
                % (d["number"], d["id"], backend.current_desktop()["number"]))
            return 0
        if action == "go":
            if not backend.switch_desktop(args.number):
                raise SystemExit("switching to desktop %d did not take; still on %d"
                                 % (args.number, backend.current_desktop()["number"]))
            out("on desktop %d" % args.number)
            return 0
        if action == "rm":
            if not backend.remove_desktop(args.number, args.fallback):
                raise SystemExit("desktop %d is still there" % args.number)
            out("removed desktop %d; %d left, on desktop %d"
                % (args.number, len(backend.list_desktops()), backend.current_desktop()["number"]))
            return 0
        if action == "name":
            if not backend.rename_desktop(args.number, args.name):
                raise SystemExit("desktop %d did not take the name %r" % (args.number, args.name))
            out("desktop %d is now %r" % (args.number, args.name))
            return 0
        win = backend.resolve_window(args.window, all_desktops=True)
        shown = shown_id(backend, win)
        if action == "move":
            if not backend.move_window(win["hwnd"], args.number):
                raise SystemExit("could not move %s %r to desktop %d"
                                 % (shown, win["title"], args.number))
            out("moved %s %r to desktop %d" % (shown, win["title"], args.number))
            return 0
        pinned = action == "pin"
        if not backend.set_window_pinned(win["hwnd"], pinned):
            raise SystemExit("could not %s %s %r" % (action, shown, win["title"]))
        out("%s %s %r" % ("pinned" if pinned else "unpinned", shown, win["title"]))
        return 0

    if args.cmd == "shot":
        win = backend.resolve_window(args.window) if args.window else None
        path = args.out or default_shot_path()
        info = backend.capture(path, window=win, scale=args.scale)
        sw, sh = info["src"][2], info["src"][3]
        # This line is the mapping from image pixels to screen pixels, and the only
        # cheap source of it. Without the origin, a click computed from a window
        # capture lands a whole control away. stderr is merged into what the model
        # reads, so it costs tokens and has to earn them.
        note("captured %dx%d src=%dx%d@%d,%d scale=%g"
             % (info["w"], info["h"], sw, sh, info["src"][0], info["src"][1], args.scale))
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

    if args.cmd == "tree":
        header, lines = backend.tree(args.window, depth=args.depth, limit=args.limit)
        out(header)
        for line in lines:
            out(line)
        return 0

    if args.cmd == "click":
        claim_target(backend, args.window)
        backend.click(args.x, args.y, double=args.double, right=args.right)
        out("clicked %d,%d" % (args.x, args.y))
        return 0

    if args.cmd == "type":
        text = " ".join(args.text)
        claim_target(backend, args.window)
        want = len(text) * 2
        sent = backend.type_text(text)
        out("typed %d chars (delivered %d/%d events)" % (len(text), sent, want))
        if sent != want:
            raise SystemExit("SendInput dropped events (delivered %d of %d)" % (sent, want))
        return 0

    if args.cmd == "key":
        claim_target(backend, args.window)
        sent = backend.send_keys(args.combo)
        events = (len(args.combo.split("+")) * 2)
        out("sent %s (delivered %d/%d events)" % (args.combo, sent, events))
        if sent != events:
            raise SystemExit("SendInput dropped events (delivered %d of %d)" % (sent, events))
        return 0

    if args.cmd == "focus":
        win = backend.resolve_window(args.window)
        if not backend.force_foreground(win["hwnd"]):
            raise SystemExit("could not bring %s %r to the front"
                             % (shown_id(backend, win), win["title"]))
        out("focused %s %r" % (shown_id(backend, win), win["title"]))
        return 0

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
