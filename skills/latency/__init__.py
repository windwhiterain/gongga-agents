"""Locate where latency is lost, then shape it in front of the bottleneck.

    doctor(app=None)                       # is a proxy or tunnel in this flow's path?
    hops(dest, count, timeout, max_ttl, peer)     # which hop starts spiking
    top(peer, seconds, limit)                     # which host is eating the uplink
    probe(peer, target, seconds, interval, timeout)  # egress and RTT on one clock
    cap(action, rate, peer, verify)               # Windows QoS uplink cap
    prio(action, app, peer)                       # DSCP 46 for a game's packets

`cap` and `prio` change this machine (or a remembered peer's); everything else
measures. The implementation is `latency.py` beside this file, whose command
line still exists for a human at a shell: `caps` read a field list of their own,
so this package hands them one table with every field the command line has and
the call's own values on top -- one shape, both faces.
"""

import argparse
import os
import sys

#: The implementation sits beside this file; the harness imports this package by
#: path, which does not put this directory on `sys.path`.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import latency  # noqa: E402  -- the implementation beside this file

#: Every field the command line declares, at its own default. A handler that reads
#: a field the caller did not name must see that default, not an AttributeError.
DEFAULTS = {
    "action": None,
    "app": None,
    "count": 20,
    "dest": None,
    "interval": 150,
    "limit": 12,
    "max_ttl": 12,
    "peer": None,
    "rate": None,
    "seconds": 20,
    "target": "223.5.5.5",
    "timeout": 1200,
    "verify": 20,
}


def _run(handler, **fields):
    """Run one handler over the command line's own shape, and answer its exit code."""
    args = argparse.Namespace(**{**DEFAULTS, **fields})
    try:
        return handler(args)
    except latency.Failure as error:
        print("latency: %s" % error, file=sys.stderr)
        return 1


def doctor(app=None):
    """Is a proxy, tunnel or VPN in this flow's path, and which one.

    `app` names a process to inspect when the answer depends on who is talking
    (for example a game whose traffic is steered per-exe).
    """
    return _run(latency.cmd_doctor, app=app)


def hops(dest, count: int = 20, timeout: int = 1200, max_ttl: int = 12, peer=None):
    """Trace toward `dest` and measure every hop: which one starts spiking.

    `peer` asks a remembered machine to measure instead of this one, which is the
    only way to tell a local uplink problem from a path problem.
    """
    return _run(latency.cmd_hops, dest=dest, count=count, timeout=timeout, max_ttl=max_ttl, peer=peer)


def top(peer=None, seconds: int = 20, limit: int = 12):
    """Which host is eating the uplink, read from the local (or `peer`'s) mihomo."""
    return _run(latency.cmd_top, peer=peer, seconds=seconds, limit=limit)


def probe(peer=None, target: str = "223.5.5.5", seconds: int = 60, interval: int = 150,
          timeout: int = 1200):
    """Egress and RTT sampled on one clock, on one machine: the distribution, not a mean."""
    return _run(
        latency.cmd_probe,
        peer=peer, target=target, seconds=seconds, interval=interval, timeout=timeout,
    )


def cap(action, rate=None, peer=None, verify: int = 20):
    """Windows QoS uplink cap for the whole machine: `on` needs `rate` in Mbps, or `off`/`status`.

    `verify` is how many seconds to spend proving the cap actually held.
    """
    # The rate is read as text (`8`, `8M`, `800k`), so a JSON number is spelled first.
    return _run(latency.cmd_cap, action=action, rate=None if rate is None else str(rate),
                peer=peer, verify=verify)


def prio(action, app=None, peer=None):
    """DSCP 46 for a game's packets: `on` needs `app` (comma separated exe names), or `off`/`status`."""
    return _run(latency.cmd_prio, action=action, app=app, peer=peer)
