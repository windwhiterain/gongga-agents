"""Route a machine's hosts through named upstreams, and drive a mihomo core.

Two halves, one package:

    up(key=None)  down()  serve()  rules()  status()  doctor()  env()
    add(value, via, rule_type="suffix")  rm(value)  check(host)
    upstream(action="list", name=None, address=None, kind="http", auth=None)

The core half talks to a running mihomo through its API:

    core_install()  core_subscribe()  core_render()  core_start()  core_stop()
    core_state()  core_status()  groups()  nodes(group)  use_node(group, node)
    bench(group=None, throughput=False, ...)  mode(which=None)  reload(path=None)
    connect(controller, secret=None, save=False)

The implementation is the modules beside this file -- `proxy.py` for routing,
`corectl.py` for owning a core, `mihomo.py` for driving one. The command line of
`proxy.py` still exists for a human at a shell.
"""

import os
import sys

#: The implementation sits beside this file; the harness imports this package by
#: path, which does not put this directory on `sys.path`.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mihomo  # noqa: E402  -- the core-driving half, beside this file
import proxy  # noqa: E402  -- the routing half, beside this file


def _core(*words):
    """Hand a lifecycle verb to the half that owns it: `corectl`.

    The words that reach it are ones this package made: a port number, a version,
    a path. Nothing a caller wrote is joined into a string.
    """
    return proxy.run_core([word for word in words if word is not None])


def up(key=None, replace: bool = False, mixed_port: int = None, controller_port: int = None):
    """Install the core if needed, take a subscription link, and start it.

    No `key` reuses the subscription already saved; `replace` fetches the link
    again even when one is stored.
    """
    return _core(
        "up", key,
        "--replace" if replace else None,
        "--mixed-port", None if mixed_port is None else str(mixed_port),
        "--controller-port", None if controller_port is None else str(controller_port),
    )


def down():
    """Stop the core this tool started."""
    return proxy.down()


def serve():
    """Run the proxy in the foreground until interrupted."""
    return proxy.serve()


def rules():
    """The rule table, in the order it is consulted."""
    return proxy.rules()


def status():
    """Config, listener, rule count, upstreams, system proxy, and last decisions."""
    return proxy.status()


def doctor():
    """Check every link in the chain and say which one is broken."""
    return proxy.doctor()


def env():
    """The shell exports that point a client at this proxy, and why git is left alone."""
    return proxy.env()


def add(value, via, rule_type="suffix"):
    """Route `value` through the upstream named `via`; `rule_type` is exact, suffix, prefix or cidr."""
    return proxy.add(value, via, rule_type)


def rm(value):
    """Remove a rule by its value."""
    return proxy.rm(value)


def check(host):
    """Is this host actually routed? Measured, through this proxy."""
    return proxy.check(host)


def upstream(action="list", name=None, address=None, kind="http", auth=None):
    """List the upstreams, or add one: `name` and `address`, `kind` http/socks5/direct."""
    return proxy.upstream(action, name, address, kind, auth)


def core_install(version=None, source=None):
    """Place the mihomo binary (and the geo database) under this skill's home."""
    return _core("install", "--version", version, "--from", source)


def core_subscribe(url=None, source=None):
    """Fetch a subscription into this skill's own directory, from a url or a file."""
    return _core("subscribe", url, "--from", source)


def core_render(mixed_port: int = None, controller_port: int = None):
    """Write the configuration the core starts from, and say what it chose."""
    return _core(
        "render",
        "--mixed-port", None if mixed_port is None else str(mixed_port),
        "--controller-port", None if controller_port is None else str(controller_port),
    )


def core_start(mixed_port: int = None, controller_port: int = None, core=None):
    """Render if needed, verify the config, and start the core detached."""
    return _core(
        "start",
        "--mixed-port", None if mixed_port is None else str(mixed_port),
        "--controller-port", None if controller_port is None else str(controller_port),
        "--core", core,
    )


def core_stop():
    """Stop the core this tool started, and nothing else."""
    return _core("stop")


def core_state():
    """What is running, from which files, on which ports."""
    return _core("status")


def _drive(handler):
    """Run `handler(core)` on the controller this machine knows, with mihomo's own
    error funnel: a `CoreError` becomes one line and exit 1, not a traceback."""
    discovered = mihomo.discover(None, None)
    if not discovered:
        raise RuntimeError("no controller found; call connect(controller), or set MIHOMO_CONTROLLER")
    core = mihomo.connect_from(discovered)
    return mihomo.funnel(lambda _words: handler(core), [])


def core_status():
    """Where the controller is, and whether it answers."""
    return _drive(lambda core: mihomo.cmd_doctor(mihomo.discover(None, None), core) or 0)


def groups():
    """The policy groups, and what each currently routes through."""
    return _drive(mihomo.cmd_groups)


def nodes(group):
    """The choices in one policy group, which one is in use, and their latency."""
    return _drive(lambda core: mihomo.cmd_nodes(core, group))


def use_node(group, node):
    """Point a group at a node, then read it back to prove it moved."""
    return _drive(lambda core: mihomo.cmd_use(core, group, node))


def bench(group=None, throughput: bool = False, seconds: int = 8, url=None,
          concurrency: int = 4, limit: int = None):
    """Latency of every node in a group, or with `throughput` their sustained bandwidth.

    No group means the largest one, and that fact is said in the output.
    """

    def run(core):
        chosen = group or mihomo.largest_group(core)
        address = url or (mihomo.DEFAULT_SPEED_URL if throughput else mihomo.DEFAULT_TEST_URL)
        return mihomo.cmd_bench(core, chosen, throughput, seconds, address, concurrency, limit, group is None)

    return _drive(run)


def mode(which=None):
    """The core's mode: `rule`, `global`, `direct`, or nothing to report it."""
    return _drive(lambda core: mihomo.cmd_mode(core, which))


def reload(path=None):
    """Apply an edited config without restarting the core."""
    return _drive(lambda core: mihomo.cmd_reload(core, mihomo.discover(None, None), path))


def connect(controller, secret=None, save: bool = False):
    """Remember a controller address (and secret) after proving it answers."""
    discovered = {
        "origin": mihomo.origin_from_controller(controller),
        "secret": secret or "",
        "source": "explicit --controller",
        "config": None,
    }
    mihomo.Core(discovered["origin"], discovered["secret"]).version()
    mihomo.out("controller        %s" % discovered["origin"])
    mihomo.out("secret            %s" % ("set" if discovered["secret"] else "none"))
    mihomo.out("reachable         yes")
    if save:
        mihomo.save_cache(discovered)
        mihomo.out("saved             %s" % mihomo.CACHE_PATH)
    return 0
