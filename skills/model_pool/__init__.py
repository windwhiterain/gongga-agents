"""Draw the model a spawned agent should run on, from what this machine can run now.

    pick(tier=None, provider=None, ...)          # one `<provider>/<model id>`
    pick_config(tier, provider=None, ...)        # SPEC's ModelConfig for that model
    status(as_json=False, ...)                   # every route, its tier, drawable or not
    probe(force=False, ...)                      # ask each provider: may this account run it
    tiers(...)                                   # the tiers, their weights, their models
    mark(target, minutes=None, reason=None, clear=False, ...)   # keep a route out for a while

A draw is inside one tier of work (flash / pro / sota, named on every call) and
only from models that are still in the roster, credentialed, above their
allowance floor, entitled on this account, and not marked out. The
implementation is `pool.py` beside this file, whose command line still exists
for a human at a shell.
"""

import argparse
import os
import sys

#: The implementation sits beside this file; the harness imports this package by
#: path, which does not put this directory on `sys.path`.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pool  # noqa: E402  -- the implementation beside this file

#: Every field the command line declares, at its own default: a handler that reads
#: one this call did not name must see the default, not an AttributeError.
DEFAULTS = {
    "cached": False,
    "clear": False,
    "explain": False,
    "force": False,
    "json": False,
    "minutes": None,
    "probe": False,
    "provider": None,
    "quiet": False,
    "reason": None,
    "refresh": None,
    "seed": None,
    "target": None,
    "tier": None,
    "timeout": None,
    "weights": None,
    "config": None,
    "preference": None,
}


def _run(handler, **fields):
    """Load the roster and the settings the way the command line does, then run."""
    args = argparse.Namespace(**{**DEFAULTS, **fields})
    config, _path, _exists = pool.load_config(args.config)
    preference, _preference_path = pool.load_preference(args.preference)
    catalog = pool.roster.load()
    if catalog is None:
        raise RuntimeError("no roster at %s" % pool.roster.catalog_path())
    return handler(args, config, preference, catalog) or 0


def pick(tier=None, provider=None, weights=None, seed: int = None, refresh: float = None,
         timeout: float = None, cached: bool = False, probe_entitlement: bool = False,
         as_json: bool = False, explain: bool = False, quiet: bool = False,
         config=None, preference=None):
    """One `<provider>/<model id>`, drawn inside `tier`.

    `as_json` answers one JSON object instead of the bare model; `explain` writes
    the candidates and the reason to stderr, where it costs the answer no tokens.
    `probe_entitlement` also asks each provider whether this account may run the
    route (one cheap request per route); `cached` refuses to run a quota script
    at all and answers from what was last read.
    """
    return _run(
        pool.cmd_pick,
        tier=tier, provider=provider, weights=weights, seed=seed, refresh=refresh,
        timeout=timeout, cached=cached, probe=probe_entitlement, json=as_json,
        explain=explain, quiet=quiet, config=config, preference=preference,
    )


def pick_config(tier, provider=None, weights=None, seed: int = None, refresh: float = None,
                timeout: float = None, cached: bool = False, probe_entitlement: bool = False,
                thinking=None, ctx_limit=None, cache_tier=None, config=None, preference=None):
    """SPEC's `ModelConfig` for the model `pick` would draw, as a mapping.

    The three provider-defaulted fields are left `None` unless named, so the
    server re-defaults them for the model that was actually drawn.
    """
    return pool.pick_config(
        tier, provider=provider, weights=weights, seed=seed, refresh=refresh, timeout=timeout,
        cached=cached, probe=probe_entitlement, config=config, preference=preference,
        thinking=thinking, ctx_limit=ctx_limit, cache_tier=cache_tier,
    )


def status(as_json: bool = False, tier=None, provider=None, weights=None, cached: bool = False,
           probe_entitlement: bool = False, config=None, preference=None):
    """Every route the roster holds, its tier, and whether it is drawable right now."""
    return _run(
        pool.cmd_status, as_json=as_json, json=as_json, tier=tier, provider=provider,
        weights=weights, cached=cached, probe=probe_entitlement, config=config,
        preference=preference,
    )


def probe(force: bool = False, tier=None, provider=None, timeout: float = None,
          config=None, preference=None):
    """Ask each provider whether this account may run the route, and remember the answer.

    `force` asks again even when a verdict is still fresh.
    """
    return _run(
        pool.cmd_probe, force=force, tier=tier, provider=provider, timeout=timeout,
        config=config, preference=preference,
    )


def tiers(tier=None, provider=None, weights=None, config=None, preference=None):
    """The tiers, their weights, and every model the roster places in each."""
    return _run(pool.cmd_tiers, tier=tier, provider=provider, weights=weights,
                config=config, preference=preference)


def mark(target, minutes: float = None, reason=None, clear: bool = False, config=None):
    """Keep one route or one provider out of the draw for `minutes`.

    `target` is `<provider>/<model id>` or a provider id; `clear` releases the
    mark instead of setting one.
    """
    return _run(pool.cmd_mark, target=target, minutes=minutes, reason=reason, clear=clear, config=config)
