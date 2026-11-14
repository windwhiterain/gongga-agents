# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Own the core process: fetch a subscription, render a configuration, start, stop, report.

`mihomo.py` drives a core that is already running. This module owns the other half --
the part that a vendor client used to do for us: turn one subscription link into a
configuration file, place the core binary, start it detached, and stop only that one
process.

The division is deliberate. Everything a general reader of this skill cannot know --
which vendor, which account, which policy groups -- arrives as a subscription link or a
configuration file the user supplies. Nothing here names a client, because a client is
just another way of producing those two artifacts.

Three decisions worth stating:

  * The controller secret is generated here and kept next to the configuration, so the
    API is never open on an unauthenticated port, and re-rendering does not invalidate a
    saved controller address.
  * `stop` signals a pid only after that pid is proved to be the core: a recycled pid
    belongs to someone else, and killing it would take an unrelated process with it.
  * Routing is the configuration's own: the rule list that came with the subscription is
    passed through untouched. This tool does not add a second way to say where a host
    should go -- the standard Clash rule list is the one opinion, and `mihomo` executes it.
"""

import gzip
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import mihomo

PROXY_DIR = os.environ.get("PROXY_HOME") or os.path.join(os.path.expanduser("~"), ".proxy")
CORE_DIR = os.path.join(PROXY_DIR, "core")
SUBSCRIPTION_PATH = os.path.join(CORE_DIR, "subscription.yaml")
CONFIG_PATH = os.path.join(CORE_DIR, "config.yaml")
SECRET_PATH = os.path.join(CORE_DIR, "secret")
LOG_PATH = os.path.join(CORE_DIR, "core.log")
PID_PATH = os.path.join(CORE_DIR, "core.pid")
STAMP_PATH = os.path.join(CORE_DIR, "version.txt")
GEO_DATABASE_PATH = os.path.join(CORE_DIR, "geoip.metadb")

BINARY_NAME = "mihomo.exe" if os.name == "nt" else "mihomo"
DEFAULT_MIXED_PORT = 7897
DEFAULT_CONTROLLER_PORT = mihomo.DEFAULT_PORT
SUBSCRIPTION_USER_AGENT = "clash-verge/v1"
RELEASE_DOWNLOAD = "https://github.com/MetaCubeX/mihomo/releases/download/%s/%s"
RELEASE_LATEST = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
GEO_DATABASE_URL = "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb"

# The release names its asset after the platform and architecture; a CPU without the
# newer instruction set takes the "compatible" build, which is the Windows default here.
RELEASE_ASSETS = {
    ("win32", "amd64"): ("mihomo-windows-amd64-compatible-%s.zip", "zip"),
    ("win32", "x86_64"): ("mihomo-windows-amd64-compatible-%s.zip", "zip"),
    ("win32", "arm64"): ("mihomo-windows-arm64-%s.zip", "zip"),
    ("linux", "x86_64"): ("mihomo-linux-amd64-%s.gz", "gzip"),
    ("linux", "aarch64"): ("mihomo-linux-arm64-%s.gz", "gzip"),
    ("darwin", "x86_64"): ("mihomo-darwin-amd64-%s.gz", "gzip"),
    ("darwin", "arm64"): ("mihomo-darwin-arm64-%s.gz", "gzip"),
}

TOP_LEVEL = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):")


class CoreError(mihomo.CoreError):
    pass


# --- files ----------------------------------------------------------------------


def out(line=""):
    print(line)


def ensure_directory(path):
    os.makedirs(path, exist_ok=True)


def write_private(path, text):
    """Write a file only its owner should read: a subscription link is a credential."""
    ensure_directory(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def secret_for_core():
    """The controller secret, generated once so a saved address keeps working."""
    if os.path.exists(SECRET_PATH):
        value = read_text(SECRET_PATH).strip()
        if value:
            return value
    import secrets
    value = secrets.token_hex(16)
    write_private(SECRET_PATH, value + "\n")
    return value


# --- the subscription -----------------------------------------------------------


def validate_subscription(text):
    """A Clash configuration carries both a node list and a rule list at top level."""
    if not TOP_LEVEL.search(text) or "proxies:" not in text or "rules:" not in text:
        raise CoreError("this is not a Clash configuration: no top-level proxies: and rules:")
    return text


def fetch_subscription(url):
    """Download a subscription. The environment's proxy is honoured by urllib, which
    matters because a subscription host is often reachable only through one."""
    # A path is accepted as readily as a link: a Windows drive letter parses as a URL
    # scheme, so an existing file is recognised before the string is read as a link.
    if os.path.exists(url):
        return validate_subscription(read_text(url))
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "file":
        return validate_subscription(read_text(urllib.request.url2pathname(parsed.path)))
    if parsed.scheme == "":
        raise CoreError("no such file: %s" % url)
    if parsed.scheme not in ("http", "https"):
        raise CoreError("unsupported subscription scheme %r" % parsed.scheme)
    request = urllib.request.Request(url, headers={"User-Agent": SUBSCRIPTION_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise CoreError("the subscription answered HTTP %d" % error.code)
    except OSError as error:
        raise CoreError("could not download the subscription: %s" % error)
    return validate_subscription(body.decode("utf-8", "replace").replace("\r\n", "\n"))


# --- configuration --------------------------------------------------------------


def assemble(subscription_text, controller_port, secret, mixed_port):
    """Rewrite the settings this tool owns, and pass everything else through untouched.

    What is rewritten is exactly what a running core cannot work without: a controller
    endpoint, a secret for it, one mixed port, and no LAN exposure. The node catalog, the
    policy groups and -- above all -- the rule list are the subscription's.
    """
    text = subscription_text.replace("\r\n", "\n")
    lines = text.split("\n")
    output = []
    written = set()

    for line in lines:
        top = TOP_LEVEL.match(line)
        if top:
            key = top.group(1)
            if key == "external-controller":
                output.append("external-controller: 127.0.0.1:%d" % controller_port)
                written.add(key)
                continue
            if key == "secret":
                output.append('secret: "%s"' % secret)
                written.add(key)
                continue
            if key == "allow-lan":
                output.append("allow-lan: false")
                written.add(key)
                continue
            if key == "mixed-port":
                output.append("mixed-port: %d" % mixed_port)
                written.add(key)
                continue
            # A subscription usually opens an HTTP and a SOCKS listener as well. One mixed
            # port is this tool's interface, and the vendor's two would collide with
            # whatever else already holds them, so they are turned off explicitly.
            if key in ("port", "socks-port"):
                output.append("%s: 0" % key)
                written.add(key)
                continue
        output.append(line)

    while output and not output[-1].strip():
        output.pop()

    if "external-controller" not in written:
        output.append("external-controller: 127.0.0.1:%d" % controller_port)
    if "secret" not in written:
        output.append('secret: "%s"' % secret)
    if "allow-lan" not in written:
        output.append("allow-lan: false")
    if "mixed-port" not in written:
        output.append("mixed-port: %d" % mixed_port)
    output.append("")
    return "\n".join(output)


def subscription_is_newer():
    """Whether the subscription moved since the configuration was written.

    `start` renders only when this is true, so a hand-edited `config.yaml` survives; an
    explicit `core render` always rewrites it.
    """
    if not os.path.exists(CONFIG_PATH):
        return True
    if not os.path.exists(SUBSCRIPTION_PATH):
        return False
    return os.path.getmtime(SUBSCRIPTION_PATH) > os.path.getmtime(CONFIG_PATH)


def render(subscription_text, controller_port, mixed_port):
    """Write the configuration the core will be started from, and say what went in."""
    secret = secret_for_core()
    configuration = assemble(subscription_text, controller_port, secret, mixed_port)
    write_private(CONFIG_PATH, configuration)
    return {
        "path": CONFIG_PATH,
        "controller": "127.0.0.1:%d" % controller_port,
        "secret": secret,
        "mixed_port": mixed_port,
        "rules": "the configuration's own",
    }


# --- the binary -----------------------------------------------------------------


def platform_key():
    machine = os.uname().machine if hasattr(os, "uname") else os.environ.get("PROCESSOR_ARCHITECTURE", "")
    machine = (machine or "").lower()
    if os.name == "nt":
        platform = "win32"
        machine = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "amd64_arm": "amd64"}.get(machine, machine)
    else:
        platform = sys.platform
        machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    return platform, machine


def latest_version():
    request = urllib.request.Request(RELEASE_LATEST, headers={"User-Agent": "proxy-skill"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            tag = (json.loads(response.read().decode("utf-8")) or {}).get("tag_name") or ""
    except (OSError, ValueError) as error:
        raise CoreError("could not resolve the latest release (%s); pass --version vX.Y.Z or --from FILE" % error)
    if not tag.startswith("v"):
        raise CoreError("the release listing returned no version tag")
    return tag


def download(url, destination, timeout=600):
    request = urllib.request.Request(url, headers={"User-Agent": "proxy-skill"})
    ensure_directory(os.path.dirname(destination))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(destination, "wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as error:
        raise CoreError("%s answered HTTP %d" % (url, error.code))
    except OSError as error:
        raise CoreError("could not download %s: %s" % (url, error))


def place_binary(payload, binary_path):
    """Accept the release exactly as it ships: a zip, a gzip stream, or the bare binary."""
    ensure_directory(os.path.dirname(binary_path))
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".exe") or os.path.basename(name) == "mihomo":
                    with open(binary_path, "wb") as handle:
                        handle.write(archive.read(name))
                    break
            else:
                raise CoreError("the archive carries no executable")
    elif payload[:2] == b"\x1f\x8b":
        with open(binary_path, "wb") as handle:
            handle.write(gzip.decompress(payload))
    else:
        if os.name == "nt" and payload[:2] != b"MZ":
            raise CoreError("%s is not a Windows executable; give the release archive or the binary" % binary_path)
        with open(binary_path, "wb") as handle:
            handle.write(payload)
    try:
        os.chmod(binary_path, 0o700)
    except OSError:
        pass
    return binary_path


def install(version=None, source=None):
    """Place the core binary, and the geo database its own rules need, under the skill.

    Without the database a configuration that uses GEOIP rules makes the core download it
    at startup -- which fails on exactly the machine that needs the proxy to reach it.
    """
    binary_path = os.path.join(CORE_DIR, BINARY_NAME)
    if source:
        with open(source, "rb") as handle:
            payload = handle.read()
        place_binary(payload, binary_path)
        out("core binary       %s" % binary_path)
        out("from              %s" % source)
        return binary_path

    platform, machine = platform_key()
    asset = RELEASE_ASSETS.get((platform, machine))
    if not asset:
        raise CoreError("no release exists for %s-%s; pass --from FILE" % (platform, machine))
    tag = version or os.environ.get("PROXY_MIHOMO_VERSION") or latest_version()
    if not tag.startswith("v"):
        tag = "v" + tag
    pattern, packaging = asset
    name = pattern % tag
    url = RELEASE_DOWNLOAD % (tag, name)
    archive = os.path.join(CORE_DIR, "download-" + name)
    out("release           %s" % tag)
    download(url, archive)
    try:
        with open(archive, "rb") as handle:
            payload = handle.read()
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass
    place_binary(payload, binary_path)
    write_private(STAMP_PATH, tag + "\n")
    out("core binary       %s" % binary_path)
    out("packaging         %s" % packaging)

    try:
        out("geo database      %s" % ensure_geo_database())
    except CoreError as error:
        out("geo database      absent (%s)" % error)
    return binary_path


def ensure_geo_database():
    """Place the geo database the core's own GEOIP rules need.

    Without it the core tries to download one at startup, which fails on exactly the
    machine that needs a proxy to reach it -- and the failure takes the whole
    configuration down with it.
    """
    if os.path.exists(GEO_DATABASE_PATH):
        return GEO_DATABASE_PATH
    partial = GEO_DATABASE_PATH + ".part"
    try:
        download(GEO_DATABASE_URL, partial, timeout=180)
        os.replace(partial, GEO_DATABASE_PATH)
    except CoreError as error:
        raise CoreError("no geo database at %s, and fetching it failed (%s)" % (GEO_DATABASE_PATH, error))
    return GEO_DATABASE_PATH


def looks_like_binary(path):
    """Whether this file is a core, rather than a script or a module that happens to
    share its name.

    A name is not enough: on Windows `which("mihomo")` also matches `mihomo.py` through
    PATHEXT, and the skill's own module sits right there -- starting it fails with
    "not a valid Win32 application".
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    if os.name == "nt":
        return head[:2] == b"MZ"
    return head[:4] in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe") or not head.startswith(b"#!")


def find_binary(explicit=None):
    if explicit:
        if not os.path.exists(explicit):
            raise CoreError("no core binary at %s" % explicit)
        return explicit
    # `mihomo.exe` on Windows, not `mihomo`: the shorter name also matches this skill's
    # own `mihomo.py` through PATHEXT, and that file is not a program.
    found = shutil.which(BINARY_NAME)
    for candidate in (os.path.join(CORE_DIR, BINARY_NAME), found or ""):
        if candidate and os.path.exists(candidate) and looks_like_binary(candidate):
            return candidate
    raise CoreError("no core binary; run `proxy core install`, or pass --core PATH")


def verify(binary, configuration_path):
    """Ask the core itself whether the file is valid, before anything is started."""
    result = subprocess.run(
        [binary, "-t", "-f", configuration_path, "-d", CORE_DIR],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        detail = result.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise CoreError("the core rejected the configuration: %s" % (" / ".join(detail) or "unknown error"))
    return True


def free_port(preferred, span=20):
    """The port to use: `preferred` when it is free, else the next free one.

    A port is this machine's fact, not the provider's, so it is never a question: the
    default is tried, and a taken one is stepped over with a line saying so.
    """
    for candidate in range(preferred, preferred + span):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", candidate))
            return candidate
        except OSError:
            continue
        finally:
            probe.close()
    raise CoreError("no free port in %d..%d" % (preferred, preferred + span))


# --- the process ----------------------------------------------------------------


def process_image(pid):
    """The image name behind a pid, or None when nothing runs there.

    Deliberately not `os.kill(pid, 0)`: on Windows that call *terminates* the process
    with signal 0 rather than probing it.
    """
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None
        text = result.stdout.decode("utf-8", "replace").strip()
        if not text.startswith('"'):
            return None
        return text.split('","')[0].strip('"') or None
    try:
        with open("/proc/%d/comm" % pid, "r", encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        pass
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    return result.stdout.decode("utf-8", "replace").strip() or None


def read_pid():
    if not os.path.exists(PID_PATH):
        return 0
    try:
        return int(read_text(PID_PATH).strip() or "0")
    except ValueError:
        return 0


def running_pid():
    """The pid this tool started, if it is still the core. A recycled pid is not ours."""
    pid = read_pid()
    if not pid:
        return 0
    image = process_image(pid)
    if not image or "mihomo" not in image.lower():
        return 0
    return pid


def spawn(binary, configuration_path):
    ensure_directory(CORE_DIR)
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(LOG_PATH, "ab") as log:
        process = subprocess.Popen(
            [binary, "-d", CORE_DIR, "-f", configuration_path],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=flags, start_new_session=(os.name != "nt"), close_fds=True,
        )
    write_private(PID_PATH, "%d\n" % process.pid)
    return process


def wait_for_controller(origin, secret, process, timeout_seconds=30):
    """Readiness is the controller answering, not a port being open."""
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise CoreError("the core exited with %s; see %s" % (process.returncode, LOG_PATH))
        try:
            mihomo.Core(origin, secret, timeout=1.5).version()
            return True
        except mihomo.CoreError as error:
            last = error
        time.sleep(0.2)
    raise CoreError("the core did not answer on %s within %ds (%s); see %s"
                    % (origin, timeout_seconds, last, LOG_PATH))


def stop():
    pid = read_pid()
    if not pid:
        out("core              not started by this tool (no %s)" % PID_PATH)
        return 0
    image = process_image(pid)
    if not image:
        try:
            os.remove(PID_PATH)
        except OSError:
            pass
        out("core              pid %d is gone; cleared %s" % (pid, PID_PATH))
        return 0
    if "mihomo" not in image.lower():
        raise CoreError("pid %d is %s, not the core; leaving it alone" % (pid, image))
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 10
    while time.time() < deadline:
        if not process_image(pid):
            break
        time.sleep(0.2)
    remaining = process_image(pid)
    if remaining:
        if os.name == "nt":
            raise CoreError("pid %d (%s) survived the stop request" % (pid, remaining))
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)
        if process_image(pid):
            raise CoreError("pid %d survived SIGKILL" % pid)
    try:
        os.remove(PID_PATH)
    except OSError:
        pass
    out("core              stopped (pid %d)" % pid)
    return 0


# --- commands -------------------------------------------------------------------


def subscribe(url=None, source=None):
    if source:
        text = validate_subscription(read_text(source))
        source = source
    elif url:
        text = fetch_subscription(url)
        source = url.split("?")[0]
    else:
        raise CoreError("give a subscription URL, or the file to read it from")
    write_private(SUBSCRIPTION_PATH, text)
    nodes = sum(1 for line in text.split("\n") if TOP_LEVEL.match(line) is None and line.strip().startswith("- name:"))
    out("subscription      %s" % SUBSCRIPTION_PATH)
    out("from              %s" % source)
    out("nodes             %d" % nodes)
    out("next              render_config, then start")
    return 0


def render_config(mixed_port=None, controller_port=None):
    mixed_port = DEFAULT_MIXED_PORT if mixed_port is None else mixed_port
    controller_port = DEFAULT_CONTROLLER_PORT if controller_port is None else controller_port
    if not os.path.exists(SUBSCRIPTION_PATH):
        if os.path.exists(CONFIG_PATH):
            out("configuration     %s (no subscription to render from; kept)" % CONFIG_PATH)
            return 0
        raise CoreError("no subscription at %s; subscribe to one first" % SUBSCRIPTION_PATH)
    result = render(read_text(SUBSCRIPTION_PATH), controller_port, mixed_port)
    out("configuration     %s" % result["path"])
    out("controller        %s" % result["controller"])
    out("secret            set (kept in %s)" % SECRET_PATH)
    out("mixed port        %d" % result["mixed_port"])
    out("rules             %s" % result["rules"])
    return 0


def start(mixed_port=None, controller_port=None, core=None):
    mixed_port = DEFAULT_MIXED_PORT if mixed_port is None else mixed_port
    controller_port = DEFAULT_CONTROLLER_PORT if controller_port is None else controller_port
    pid = running_pid()
    if pid:
        discovered = mihomo.discover()
        out("core              already running (pid %d)" % pid)
        if discovered:
            out("controller        %s" % discovered["origin"])
        return 0

    binary = find_binary(core)
    if os.path.exists(SUBSCRIPTION_PATH) and subscription_is_newer():
        result = render(read_text(SUBSCRIPTION_PATH), controller_port, mixed_port)
    elif os.path.exists(CONFIG_PATH):
        secret = secret_for_core()
        result = {"path": CONFIG_PATH, "controller": "127.0.0.1:%d" % controller_port,
                  "secret": secret, "mixed_port": None, "rules": "as configured"}
    else:
        raise CoreError("nothing to start from; subscribe to a core first")

    ensure_geo_database()
    verify(binary, result["path"])
    process = spawn(binary, result["path"])
    origin = mihomo.origin_from_controller(result["controller"]) or "http://127.0.0.1:%d" % controller_port
    try:
        wait_for_controller(origin, result["secret"], process)
    except CoreError:
        stop()
        raise
    mihomo.save_cache({"origin": origin, "secret": result["secret"],
                       "source": "started from %s" % result["path"], "config": result["path"]})
    out("core              pid %d (%s)" % (process.pid, binary))
    out("configuration     %s" % result["path"])
    out("controller        %s" % origin)
    out("mixed port        %s" % (result["mixed_port"] or "see the configuration"))
    out("saved             %s" % mihomo.CACHE_PATH)
    out("next              core_status, then groups")
    return 0


def status():
    pid = running_pid()
    discovered = mihomo.discover()
    out("core              %s" % ("pid %d (%s)" % (pid, process_image(pid)) if pid else "not running"))
    out("binary            %s" % (os.path.join(CORE_DIR, BINARY_NAME)
                                  if os.path.exists(os.path.join(CORE_DIR, BINARY_NAME)) else "not installed"))
    out("subscription      %s" % (SUBSCRIPTION_PATH if os.path.exists(SUBSCRIPTION_PATH) else "absent"))
    out("configuration     %s" % (CONFIG_PATH if os.path.exists(CONFIG_PATH) else "absent"))
    if not discovered:
        out("controller        not found")
        return 0
    try:
        version = mihomo.Core(discovered["origin"], discovered.get("secret", ""), timeout=2.0).version()
    except mihomo.CoreError:
        out("controller        %s (not answering)" % discovered["origin"])
        return 0
    out("controller        %s (%s)" % (discovered["origin"], version))
    return 0


def ensure_core():
    """A core binary on this machine: the one already here, or a freshly fetched one."""
    try:
        return find_binary(None)
    except CoreError:
        out("core binary       absent, fetching one")
        return install()


def core_answering_elsewhere():
    """A core that answers but was not started by this tool.

    Starting a second one would take another pair of ports and leave two catalogs of
    nodes on one machine, so that is said out loud instead of done quietly.
    """
    if running_pid():
        return None
    discovered = mihomo.discover()
    if not discovered:
        return None
    try:
        mihomo.Core(discovered["origin"], discovered.get("secret", ""), timeout=2.0).version()
    except mihomo.CoreError:
        return None
    return discovered


def up(key=None, replace=False, mixed_port=None, controller_port=None):
    mixed_port = DEFAULT_MIXED_PORT if mixed_port is None else mixed_port
    controller_port = DEFAULT_CONTROLLER_PORT if controller_port is None else controller_port
    """`cli + key`: everything else is derived -- the core, its ports, its secret."""
    elsewhere = core_answering_elsewhere()
    if elsewhere and not replace:
        out("core              already answering at %s (%s)" % (elsewhere["origin"], elsewhere["source"]))
        out("")
        out("nothing to start: that core is already usable -- `proxy core doctor`, `proxy core groups`.")
        out("to make this tool own the core instead, call up again with replace=True.")
        return 1

    binary = ensure_core()
    if key:
        text = fetch_subscription(key)
        write_private(SUBSCRIPTION_PATH, text)
        source = "subscription link"
    elif os.path.exists(SUBSCRIPTION_PATH):
        text = read_text(SUBSCRIPTION_PATH)
        source = SUBSCRIPTION_PATH
    elif os.path.exists(CONFIG_PATH):
        text = None
        source = CONFIG_PATH
    else:
        raise CoreError("give the subscription link")

    was_running = running_pid()
    if was_running:
        stop()

    mixed_port = free_port(mixed_port)
    controller_port = free_port(controller_port)
    if text is None:
        result = {"path": CONFIG_PATH, "controller": "127.0.0.1:%d" % controller_port,
                  "secret": secret_for_core(), "mixed_port": None, "rules": "as configured"}
    else:
        result = render(text, controller_port, mixed_port)
    ensure_geo_database()
    verify(binary, result["path"])

    process = spawn(binary, result["path"])
    origin = mihomo.origin_from_controller(result["controller"]) or "http://127.0.0.1:%d" % controller_port
    try:
        wait_for_controller(origin, result["secret"], process)
    except CoreError:
        stop()
        raise
    mihomo.save_cache({"origin": origin, "secret": result["secret"],
                       "source": "started from %s" % result["path"], "config": result["path"]})

    out("core              pid %d (%s)" % (process.pid, binary))
    out("config            %s, from %s" % (result["path"], source))
    out("mixed port        %s" % (result["mixed_port"] or "see the configuration"))
    out("controller        %s" % origin)
    groups = 0
    try:
        groups = len(mihomo.groups_of(mihomo.Core(origin, result["secret"], timeout=3.0).proxies()))
    except mihomo.CoreError:
        pass
    if groups:
        out("policy groups     %d" % groups)
    out("")
    out("next              groups, then use_node or bench")
    return 0


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="proxy core",
        description="own the core process: subscribe, install, start, stop",
        epilog="driving one that already runs: doctor, groups, nodes GROUP, use GROUP NODE, "
               "bench [GROUP], mode [rule|global|direct], reload [PATH], connect HOST:PORT",
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("up", help="install the core if needed, take a subscription link, and start it")
    p.add_argument("key", nargs="?", help="the subscription link; omit to reuse the one already saved")
    p.add_argument("--replace", action="store_true",
                   help="start this tool's own core even when another one already answers")
    p.add_argument("--mixed-port", type=int, default=DEFAULT_MIXED_PORT)
    p.add_argument("--controller-port", type=int, default=DEFAULT_CONTROLLER_PORT)

    sub.add_parser("down", help="stop the core this tool started")

    p = sub.add_parser("subscribe", help="fetch a subscription into this skill's own directory")
    p.add_argument("url", nargs="?")
    p.add_argument("--from", dest="from_path", default=None, help="read the subscription from a file instead")

    p = sub.add_parser("render", help="write the configuration the core starts from")
    p.add_argument("--mixed-port", type=int, default=DEFAULT_MIXED_PORT)
    p.add_argument("--controller-port", type=int, default=DEFAULT_CONTROLLER_PORT)

    p = sub.add_parser("install", help="place the core binary under this skill")
    p.add_argument("--version", default=None)
    p.add_argument("--from", dest="from_path", default=None, help="an archive, a gzip stream, or the binary itself")

    p = sub.add_parser("start", help="render if needed, verify, and start the core detached")
    p.add_argument("--mixed-port", type=int, default=DEFAULT_MIXED_PORT)
    p.add_argument("--controller-port", type=int, default=DEFAULT_CONTROLLER_PORT)
    p.add_argument("--core", default=None, help="path to the core binary")

    sub.add_parser("stop", help="stop the core this tool started")
    sub.add_parser("status", help="what is running, and from which files")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    if args.cmd == "up":
        return up(key=args.key, replace=args.replace,
                  mixed_port=args.mixed_port, controller_port=args.controller_port)
    if args.cmd in ("down", "stop"):
        return stop()
    if args.cmd == "subscribe":
        return subscribe(url=args.url, source=args.from_path)
    if args.cmd == "render":
        return render_config(mixed_port=args.mixed_port, controller_port=args.controller_port)
    if args.cmd == "install":
        return install(version=args.version, source=args.from_path)
    if args.cmd == "start":
        return start(mixed_port=args.mixed_port, controller_port=args.controller_port, core=args.core)
    return status()


def run(argv):
    return mihomo.funnel(main, argv)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
