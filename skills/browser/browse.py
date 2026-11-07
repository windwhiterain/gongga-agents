# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "playwright==1.63.0",
# ]
# ///
"""Drive the browser this skill started: one CDP connection, one action.

    browse.py open <url>          # a new tab, and print its title and url
    browse.py text                # the visible text of the current tab
    browse.py html                # the raw html, to a file
    browse.py click <selector>    # click the first match of a css selector
    browse.py type <selector> <text>
    browse.py press <key>         # ctrl+l, enter, ...
    browse.py shot                # a screenshot, printed as @image: <path>
    browse.py cookies [domain]    # what the browser holds
    browse.py tabs                # what is open
    browse.py find <text>         # the links on the page, with their text

The browser is not started here: `browse start` owns that. This connects to the
port it recorded and says so plainly when nothing answers, because a browser
that is not up and a selector that matches nothing look the same otherwise.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HOME_DIR = os.environ.get("BROWSER_HOME", os.path.join(os.path.expanduser("~"), ".agents", "browser"))
PORT_FILE = os.path.join(HOME_DIR, "port")
CHOICE_FILE = os.path.join(HOME_DIR, "tab")
MODE_FILE = os.path.join(HOME_DIR, "mode")
# The cookie jar: every cookie a browser held, as playwright spells it. It is a
# secret as much as a key is, so it lives in this home and nowhere near a repo.
JAR_FILE = os.path.join(HOME_DIR, "cookies.json")


def note(line):
    print(line, file=sys.stderr, flush=True)


def first_line(exc):
    """The one line of a playwright error that says what went wrong.

    The traceback below it is the driver's own noise: an action that timed out
    says "waiting for locator" in its first line, and the rest is the same
    sentence repeated with a call log.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("Call log"):
            return line
    return type(exc).__name__


def out(line):
    print(line, flush=True)


def port():
    """The port the browser answers on, or None."""
    try:
        with open(PORT_FILE, encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def connect(pw):
    """The browser, or a message that says which step failed.

    # Invariant

    Two doors, tried in order, whichever browser this is. A browser started with a
    port of its own answers HTTP discovery on that port, so playwright finds the
    targets itself. A real user profile is reached through
    edge://inspect/#remote-debugging, and that server speaks CDP on a bare
    websocket with no discovery endpoint beside it. Asking both in that order is
    what lets one code path drive either, without asking which it is.
    """
    number = port()
    if number is None:
        raise SystemExit("no browser recorded; run 'browse start' or 'browse attach' first")
    failure = None
    for endpoint in ("http://127.0.0.1:%d" % number,
                     "ws://127.0.0.1:%d/devtools/browser" % number):
        try:
            return pw.chromium.connect_over_cdp(endpoint)
        except Exception as exc:
            failure = exc
    hint = ('; tick "Allow remote debugging for this browser instance" at '
            "edge://inspect/#remote-debugging and answer Allow on the prompt") if mode() == "attached" else (
            "; run 'browse start'")
    raise SystemExit("cannot reach the browser on port %d (%s)%s" % (number, first_line(failure), hint))


def mode():
    """Whose browser the recorded port is: `own` unless `attach` recorded otherwise."""
    try:
        with open(MODE_FILE, encoding="utf-8") as handle:
            return handle.read().strip() or "own"
    except OSError:
        return "own"


def current_page(browser):
    """The tab to act on.

    # Invariant

    Over CDP every tab reports `hasFocus()` true and `visibilityState` visible,
    so neither says which tab the human is looking at. The tab is therefore
    chosen by name and remembered: the one the caller selected, else the one
    this skill last acted on, else the newest tab.
    """
    pages = [page for context in browser.contexts for page in context.pages]
    if not pages:
        raise SystemExit("the browser has no tab open")
    chosen = pick(pages)
    return chosen


def pick(pages):
    """The tab this skill means: the remembered one, else the newest."""
    wanted = read_choice()
    if wanted:
        for page in pages:
            if wanted in page.url:
                return page
    return pages[-1]


def read_choice():
    try:
        with open(CHOICE_FILE, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def remember(page):
    """Which tab the next call means."""
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(CHOICE_FILE, "w", encoding="utf-8") as handle:
            handle.write(page.url)
    except OSError:
        pass


def selector_of(browser):
    """The tab to act on, brought to front.

    # Invariant

    A screenshot of a tab that is not in front times out: the browser does not
    paint a background tab. So the tab is brought to front before anything is
    read from it.
    """
    page = current_page(browser)
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


def main(argv):
    parser = argparse.ArgumentParser(prog="browse.py", description="drive this machine's browser")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("open", help="open a url in a new tab")
    p.add_argument("url")
    p = sub.add_parser("text", help="the visible text of the tab")
    p = sub.add_parser("html", help="the raw html, to a file")
    p.add_argument("--out")
    p = sub.add_parser("click", help="click the first match of a css selector")
    p.add_argument("selector")
    p = sub.add_parser("type", help="type into the first match of a css selector")
    p.add_argument("selector")
    p.add_argument("text")
    p = sub.add_parser("press", help="press a key")
    p.add_argument("key")
    p = sub.add_parser("shot", help="a screenshot")
    p.add_argument("--scale", type=float, default=1.0)
    p = sub.add_parser("cookies", help="what the browser holds")
    p.add_argument("domain", nargs="?")
    p = sub.add_parser("sniff", help="copy this browser's cookies to a jar file")
    p.add_argument("--domain", help="only cookies whose domain contains this")
    p.add_argument("--out", help="where the jar goes (default: this skill's home)")
    p = sub.add_parser("seed", help="put a jar file's cookies into this browser")
    p.add_argument("--from", dest="source", help="the jar (default: this skill's home)")
    p = sub.add_parser("tabs", help="what is open")
    p = sub.add_parser("use", help="which tab the next action means")
    p.add_argument("url", help="a substring of the tab's url")
    p = sub.add_parser("find", help="the links on the page, with their text")
    p.add_argument("text", nargs="?")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = connect(pw)
        try:
            if args.cmd == "open":
                page = browser.contexts[0].new_page()
                page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
                remember(page)
                out("url: %s" % page.url)
                out("title: %s" % page.title())
                return 0

            if args.cmd == "use":
                pages = [page for context in browser.contexts for page in context.pages]
                for page in pages:
                    if args.url in page.url:
                        remember(page)
                        out("using %s" % page.url)
                        return 0
                raise SystemExit("no tab whose url contains %r" % args.url)

            page = selector_of(browser)

            if args.cmd == "text":
                out(page.evaluate("document.body ? document.body.innerText : ''"))
                return 0

            if args.cmd == "html":
                path = args.out or os.path.join(tempfile.gettempdir(), "browse-%d.html" % os.getpid())
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(page.content())
                out("wrote %s" % path)
                return 0

            if args.cmd == "click":
                try:
                    page.click(args.selector, timeout=15000)
                except Exception as exc:
                    raise SystemExit("cannot click %s: %s" % (args.selector, first_line(exc)))
                out("clicked %s" % args.selector)
                return 0

            if args.cmd == "type":
                try:
                    page.fill(args.selector, args.text, timeout=15000)
                except Exception as exc:
                    raise SystemExit("cannot type into %s: %s" % (args.selector, first_line(exc)))
                out("typed into %s" % args.selector)
                return 0

            if args.cmd == "press":
                page.keyboard.press(args.key)
                out("pressed %s" % args.key)
                return 0

            if args.cmd == "shot":
                image_path = os.path.join(tempfile.gettempdir(), "browse-%d.png" % os.getpid())
                page.screenshot(path=image_path, scale="css" if args.scale == 1.0 else "device")
                if any(c.isspace() for c in image_path):
                    note("!! %r contains a space; the @ reference cannot be parsed" % image_path)
                if ")" in image_path:
                    note("!! %r contains ')'; it closes the @image(...) reference early" % image_path)
                # The meta spelling the server parses; `@image: <path>` is prose to it.
                out("@image(%s)" % image_path)
                return 0

            if args.cmd == "cookies":
                found = browser.contexts[0].cookies()
                if args.domain:
                    found = [c for c in found if args.domain in c["domain"]]
                for cookie in found:
                    out("%s %s = %s" % (cookie["domain"], cookie["name"], cookie.get("value", "")))
                out("%d cookie(s)" % len(found))
                return 0

            if args.cmd == "sniff":
                # Reading, never clearing: the jar is a copy, and the browser it
                # came from keeps everything it had.
                jar = [cookie for context in browser.contexts for cookie in context.cookies()]
                if args.domain:
                    jar = [c for c in jar if args.domain in c.get("domain", "")]
                path = args.out or JAR_FILE
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(jar, handle, ensure_ascii=False, indent=1)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                note("the jar holds live sessions: keep it off any repository")
                out("%d cookie(s) -> %s" % (len(jar), path))
                return 0

            if args.cmd == "seed":
                path = args.source or JAR_FILE
                try:
                    with open(path, encoding="utf-8") as handle:
                        jar = json.load(handle)
                except (OSError, ValueError) as exc:
                    raise SystemExit("cannot read the jar %s (%s); run 'browse sniff' in the "
                                     "browser that has the sessions" % (path, exc))
                if not isinstance(jar, list):
                    raise SystemExit("%s is not a jar: expected the list 'browse sniff' writes" % path)
                for context in browser.contexts:
                    context.add_cookies(jar)
                out("%d cookie(s) from %s" % (len(jar), path))
                return 0

            if args.cmd == "tabs":
                for context in browser.contexts:
                    for page in context.pages:
                        out("%s  %s" % (page.title(), page.url))
                return 0

            if args.cmd == "find":
                links = page.evaluate(
                    "Array.from(document.querySelectorAll('a')).map(a => "
                    "({text: (a.innerText||'').trim().slice(0,120), href: a.href})).filter(a => a.text)"
                )
                for link in links:
                    if not args.text or args.text.lower() in link["text"].lower():
                        out("%s  ->  %s" % (link["text"], link["href"]))
                out("%d link(s)" % len(links))
                return 0
        finally:
            # The browser belongs to whoever started it: closing the connection
            # must not close the browser.
            try:
                browser.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (LookupError, RuntimeError, ValueError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(1)
