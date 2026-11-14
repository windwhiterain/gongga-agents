"""Drive a browser: open a page, read it, act on it, and hold its cookies.

Six functions are the browser's own life, five are what a caller reads, eight are
what a caller does:

    start()  attach(port=None)  close(profile=None)  stop()  status()  doctor()
    open_url(url)  text()  html(out_path=None)  find(text=None)  tabs()
    click(selector)  type_text(selector, text)  press(key)
    shot(scale=1.0)  cookies(domain=None)  sniff(domain=None, out_path=None)
    seed(source=None)  use(url)

The implementation is the `browse.py` driver beside this file, which needs
playwright; the entry that came before ran it as `uv run browse.py` so the
dependency was resolved on demand, and this package keeps that contract rather
than importing a library the harness's interpreter does not have. Life is the
`lifecycle.sh` beside it: it starts, attaches to, and stops the browser, and
records which port is whose. Both are one program invocation with a list of
words -- no shell, and nothing a caller wrote is joined into a string.
"""

import os
import shutil
import subprocess

#: Where the driver and the lifecycle script sit: this package's own directory.
HERE = os.path.dirname(os.path.abspath(__file__))


def _uv(script, arguments):
    """Run `browse.py` under uv, which resolves its playwright pin on demand."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("no uv on PATH; install it from https://docs.astral.sh/uv/")
    return subprocess.run([uv, "run", "--quiet", os.path.join(HERE, script)] + list(arguments)).returncode


def _lifecycle(verb, *rest):
    """Run a lifecycle verb of `lifecycle.sh`: start, attach, close, stop, status, doctor."""
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("no bash on PATH; the lifecycle script needs it")
    return subprocess.run([bash, os.path.join(HERE, "lifecycle.sh"), verb, *rest]).returncode


def start():
    """Start a browser of this skill's own and record its port.

    One long-lived instance with its own profile stays the only one on that
    profile: a second --remote-debugging-port against a profile already open does
    not listen at all, so the instance is started with the port from the first
    moment. Headless unless BROWSER_HEADED=1.
    """
    return _lifecycle("start")


def attach(port=None):
    """Drive the browser the user is already running, with its consent.

    The door is the browser's own switch at edge://inspect/#remote-debugging:
    since Chromium 136 the port flag is ignored on a real profile. Nothing here
    starts, copies or stops that browser.
    """
    return _lifecycle("attach", *([str(port)] if port else []))


def close(profile=None):
    """Close a browser this skill started, by profile, and forget its port."""
    return _lifecycle("close", *([str(profile)] if profile else []))


def stop():
    """Stop the browser this skill started; one it merely attached to is left alone."""
    return _lifecycle("stop")


def status():
    """Whether a browser is up, on which port, and whether it is ours or the user's."""
    return _lifecycle("status")


def doctor():
    """Whether this machine can be driven at all: a browser, uv, a port where one is due."""
    return _lifecycle("doctor")


def open_url(url):
    """Open a url in a new tab and print its url and title."""
    return _uv("browse.py", ["open", url])


def text():
    """The visible text of the tab the next action means."""
    return _uv("browse.py", ["text"])


def html(out_path=None):
    """The raw html of that tab, written to a file and its path printed."""
    return _uv("browse.py", ["html"] + (["--out", out_path] if out_path else []))


def find(text=None):
    """The links on the page with their text, optionally only those containing `text`."""
    return _uv("browse.py", ["find"] + ([text] if text else []))


def tabs():
    """Every open tab: its title and url."""
    return _uv("browse.py", ["tabs"])


def use(url):
    """Make the tab whose url contains `url` the one the next action means."""
    return _uv("browse.py", ["use", url])


def click(selector):
    """Click the first match of a css selector."""
    return _uv("browse.py", ["click", selector])


def type_text(selector, text):
    """Fill the first match of a css selector with `text`."""
    return _uv("browse.py", ["type", selector, text])


def press(key):
    """Press one key: `enter`, `ctrl+l`, ..."""
    return _uv("browse.py", ["press", key])


def shot(scale=1.0):
    """A screenshot of the tab, printed as `@image(<path>)`."""
    return _uv("browse.py", ["shot", "--scale", str(scale)])


def cookies(domain=None):
    """The cookies this browser holds, all of them or only `domain`'s."""
    return _uv("browse.py", ["cookies"] + ([domain] if domain else []))


def sniff(domain=None, out_path=None):
    """Copy the browser's cookies to a jar file; reading only, nothing is cleared.

    The jar holds live sessions: it is as secret as a key, and its default home is
    the skill's own, not a repository.
    """
    return _uv(
        "browse.py",
        ["sniff"]
        + (["--domain", domain] if domain else [])
        + (["--out", out_path] if out_path else []),
    )


def seed(source=None):
    """Put a jar file's cookies into this browser."""
    return _uv("browse.py", ["seed"] + (["--from", source] if source else []))
