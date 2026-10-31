"""Draws one model this machine can run right now, for a spawn to be pointed at.

Two decisions, in this order:

1. **The tier**, by weight: `flash`, `pro`, `sota`. A tier with nothing usable
   drops out and its weight is spread over the tiers that are left.
2. **The model inside that tier**, in proportion to the allowance it has left,
   falling back to roster order when no route there is scored.

The model this prints is what `set_session --model` and `send_message` take:
`<provider>/<model id>`, exactly the spelling the server resolves.
"""

import argparse
import copy
import json
import os
import random
import sys
import time
import tomllib

import entitlement
import quota
import roster
import tiers

AGENTS = os.path.join(os.path.expanduser("~"), ".agents")
CONFIG_PATH = os.path.join(AGENTS, "model_pool.toml")
CONFIG_ENV = "MODEL_POOL_CONFIG"

DEFAULTS = {
    "pool": {
        # How long one reading answers for. `0` asks the provider every draw.
        "refresh": 300,
        # How long one quota script may take before its provider is unscored.
        "timeout": 8,
        # A provider no script could report on is still drawable, unscored.
        "fail_open": True,
        # How an unscored route competes inside its tier: a route no number
        # describes is neither excluded nor privileged, it carries this share.
        "unscored_share": 0.5,
        # How long a mark keeps a provider or a route out of the draw.
        "cooldown": 1800,
        "minimum": {"percent": 5, "money": 1, "other": 0},
        # Provider ids never drawn, whatever the roster says.
        "exclude": [],
        # How long one entitlement verdict answers for. The roster says what a
        # provider sells; only the provider says what this account bought, so a
        # verdict is learned by asking once and then trusted for a day.
        "probe_refresh": 86400,
        "probe_timeout": 20,
    },
    "tier": [
        {"id": "flash", "weight": 0.60},
        {"id": "pro", "weight": 0.35},
        {"id": "sota", "weight": 0.05},
    ],
}


def merge(base, overlay):
    """Defaults kept, overrides winning, dictionaries merged key by key and lists replaced."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = copy.deepcopy(base)
        for key, value in overlay.items():
            merged[key] = merge(base[key], value) if key in base else value
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return overlay
    return overlay


def config_path(explicit):
    return explicit or os.environ.get(CONFIG_ENV) or CONFIG_PATH


def load_config(explicit):
    """The bundled defaults, with this machine's overrides laid over them."""
    path = config_path(explicit)
    if not os.path.isfile(path):
        return merge(DEFAULTS, {}), path, False
    with open(path, "rb") as handle:
        return merge(DEFAULTS, tomllib.load(handle)), path, True


def weights_of(config, overrides):
    """Tier weights in declared order, with `flash=0.6,pro=0.35` written over them."""
    order = [row["id"] for row in config["tier"]]
    weights = {row["id"]: float(row.get("weight") or 0) for row in config["tier"]}
    if overrides:
        for piece in overrides.split(","):
            name, _, value = piece.partition("=")
            name = name.strip()
            if not value or name not in weights:
                raise SystemExit(f"--weights: expected <tier>=<number> for one of {', '.join(order)}, got {piece!r}")
            weight = float(value)
            if weight < 0:
                raise SystemExit(f"--weights: {name} cannot be negative")
            weights[name] = weight
    if sum(weights.values()) <= 0:
        raise SystemExit("every tier weighs nothing: no draw is possible")
    return order, weights


def note(text, quiet=False):
    if not quiet:
        print(text, file=sys.stderr)


class Route:
    """One provider/model pair, with what a draw needs to know about it."""

    def __init__(self, provider, model, tier):
        self.provider = provider
        self.model = model
        self.tier = tier
        self.key = f"{provider['id']}/{model['id']}"
        self.unit = None
        self.reading = None
        self.usable = None
        self.why = "not read yet"
        self.share = None
        self.weight = 0.0
        self.mark = None
        self.evidence = None
        self.verdict = None

    def line(self):
        return self.key


def readings_of(catalog, providers, config, state, refresh, timeout, cached_only):
    """Every provider's reading, from the cache when it is fresh and the scripts otherwise."""
    for provider in providers:
        query = catalog.quota_target(provider)
        if query is None:
            continue
        if refresh > 0 and quota.fresh(state, provider["id"], refresh) is not None:
            continue
        if cached_only and (state.get("readings") or {}).get(provider["id"]):
            continue
        if cached_only:
            continue
        path = catalog.script_path(query.get("script"))
        if path is None:
            quota.remember(state, provider["id"], None, f"no script at {query.get('script')}")
            continue
        credential = catalog.quota_credential(provider, query)
        if not credential:
            item = query.get("script_env") or provider.get("key_env") or "credential"
            quota.remember(state, provider["id"], None, f"no {item} in the environment or the private catalog")
            continue
        reading, failure = quota.run_script(path, credential, timeout)
        quota.remember(state, provider["id"], reading, failure)
    try:
        quota.save_state(state)
    except OSError as error:
        # A cache that cannot be written costs the next draw a script run, not
        # this one: the readings already in memory are the ones being used.
        note(f"model_pool: cannot save the reading cache: {error}")


def evaluate(route, catalog, config, state):
    """Fill in what the draw needs to know about one route.

    Usable is three-valued: `True` with allowance, `False` when the provider
    itself says there is none, and `None` when nothing could be reported on --
    which `fail_open` then decides, unscored either way.
    """
    pool = config["pool"]
    credential = catalog.credential(route.provider)
    if not credential:
        name = route.provider.get("key_env") or "no key_env"
        route.usable = False
        route.weight = 0.0
        route.why = f"no credential: the roster names {name}, and neither it nor the private catalog has one"
        return route
    query = catalog.quota_target(route.provider)
    route.unit = (query or {}).get("unit")
    cached = (state.get("readings") or {}).get(route.provider["id"]) or {}
    route.reading = cached.get("reading")
    route.share = quota.share_of(route.reading)
    route.usable, route.why = quota.state_of(route.reading, route.unit, pool.get("minimum") or {})
    if query is None:
        route.why = "the roster publishes no quota for this provider"
    elif route.reading is None and cached.get("failure"):
        route.why = cached["failure"]
    if route.usable is None:
        route.usable = bool(pool.get("fail_open", True))
        route.why = ("drawable unscored: " if route.usable else "unscored and fail_open is off: ") + route.why
    elif route.share is None and route.usable:
        route.why += ", unscored"
    if cached.get("failure") and route.reading is not None:
        route.why += f" (the last query failed: {cached['failure']})"
    route.weight = route.share if route.share is not None else float(pool.get("unscored_share", 0.5))
    probe = (state.get("probes") or {}).get(route.key) or {}
    inside = probe.get("at") is not None and (
        float(pool.get("probe_refresh") or 0) <= 0
        or time.time() - float(probe["at"]) <= float(pool["probe_refresh"])
    )
    if inside and probe.get("verdict") == entitlement.REFUSED:
        route.verdict = entitlement.REFUSED
        route.verdict_detail = probe.get("detail")
        route.usable = False
        route.weight = 0.0
        route.why = f"probed out: {probe.get('detail') or 'the provider denies this model'}"
    for key in (route.key, route.provider["id"]):
        mark = quota.marked(state, key)
        if mark is not None:
            route.mark = mark
            route.usable = False
            until = time.strftime("%H:%M", time.localtime(float(mark["until"])))
            route.why = f"marked until {until}" + (f": {mark['reason']}" if mark.get("reason") else "")
            break
    return route


def weighted_pick(keys, weights, rng):
    """One key, drawn in proportion to its weight."""
    total = sum(weights[key] for key in keys)
    if total <= 0:
        return rng.choice(keys)
    point = rng.random() * total
    for key in keys:
        point -= weights[key]
        if point < 0:
            return key
    return keys[-1]


def pick_route(routes, rng):
    """One route of one tier, in proportion to the allowance it has left.

    An unscored route carries a nominal weight, so a wallet that states no
    ceiling still competes with a plan that states one. When nothing carries any
    weight at all -- every route spent, or the nominal share configured to zero
    -- roster order answers, because a spawn still has to run somewhere.
    """
    live = [route for route in routes if route.weight > 0]
    if not live:
        return routes[0], "roster order: no route of this tier carries a weight"
    chosen = weighted_pick([route.key for route in live], {route.key: route.weight for route in live}, rng)
    total = sum(route.weight for route in live)
    for route in live:
        if route.key == chosen:
            nominal = " (nominal, unscored)" if route.share is None else ""
            return route, f"weighted by allowance: {route.weight:.2f} of {total:.2f} in this tier{nominal}"
    return live[0], "weighted by allowance"


def draw(routes, order, weights, rng):
    """The tier first, then the model: `(route, how)`."""
    by_tier = {}
    for route in routes:
        if route.usable:
            by_tier.setdefault(route.tier, []).append(route)
    live = [tier for tier in order if by_tier.get(tier)]
    if live:
        tier = weighted_pick(live, weights, rng)
        route, how = pick_route(by_tier[tier], rng)
        return route, f"tier {tier} drawn at weight {weights[tier]:g}; {how}"
    # Nothing is usable: a spawn still has to run somewhere. Relax the marks
    # first, then the allowance, and say so on stderr.
    unmarked = [route for route in routes if route.mark is None]
    for candidate, why in ((unmarked, "every route is out of allowance"),
                           (routes, "every route is marked or out of allowance")):
        if not candidate:
            continue
        by_tier = {}
        for route in candidate:
            by_tier.setdefault(route.tier, []).append(route)
        live = [tier for tier in order if by_tier.get(tier)]
        tier = weighted_pick(live, {key: weights.get(key, 0) or 1 for key in live}, rng)
        route, how = pick_route(by_tier[tier], rng)
        return route, f"{why}; falling back to {tier}: {how}"
    return None, "the roster has no model to draw"


def explain_empty(catalog, config, provider_id, tier_id):
    """Why a filter matched nothing, naming what would have matched."""
    if provider_id and provider_id not in catalog.by_id:
        return f"no provider {provider_id!r}; known: {', '.join(sorted(catalog.by_id))}"
    tiers = [row["id"] for row in config["tier"]]
    if tier_id and tier_id not in tiers:
        return f"no tier {tier_id!r}; declared: {', '.join(tiers)}"
    scope = f"provider {provider_id}" if provider_id else "the roster"
    if tier_id:
        return f"{scope} has no model in tier {tier_id!r}"
    return f"{scope} carries no model"


def tier_settings(config):
    """The tier rules: this machine's overrides laid over the module's defaults."""
    return merge(tiers.DEFAULTS, config.get("tiers") or {})


def collect(catalog, config, provider_id, tier_id):
    order, _ = weights_of(config, None)
    settings = tier_settings(config)
    routes = []
    excluded = set(config["pool"].get("exclude") or [])
    for provider, model in catalog.models(provider_id):
        tier, evidence = tiers.classify(provider, model, settings)
        if tier not in order or provider["id"] in excluded:
            continue
        if tier_id and tier != tier_id:
            continue
        route = Route(provider, model, tier)
        route.evidence = evidence
        routes.append(route)
    return routes


def prepared(args, config, catalog):
    """The filtered routes, read and evaluated: `(routes, state)`."""
    routes = collect(catalog, config, args.provider, args.tier)
    if not routes:
        raise SystemExit(explain_empty(catalog, config, args.provider, args.tier))
    pool = config["pool"]
    state = quota.load_state()
    providers = {route.provider["id"]: route.provider for route in routes}.values()
    readings_of(
        catalog, providers, config, state,
        float(args.refresh if args.refresh is not None else pool["refresh"]),
        float(args.timeout if args.timeout is not None else pool["timeout"]),
        args.cached,
    )
    if getattr(args, "probe", False):
        probes = state.setdefault("probes", {})
        timeout = float(config["pool"]["probe_timeout"])
        for route in probe_stale(state, routes, config):
            credential = catalog.credential(route.provider)
            if not credential:
                continue
            verdict, detail = entitlement.probe(route.provider, route.model["id"], credential, timeout)
            probes[route.key] = {"at": time.time(), "verdict": verdict, "detail": detail}
        try:
            quota.save_state(state)
        except OSError as error:
            note(f"model_pool: cannot save the entitlement cache: {error}")
    for route in routes:
        evaluate(route, catalog, config, state)
    return routes, state


def cmd_pick(args, config, catalog):
    order, weights = weights_of(config, args.weights)
    routes, _state = prepared(args, config, catalog)
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    route, how = draw(routes, order, weights, rng)
    if route is None:
        raise SystemExit("the roster has no model to draw")
    if not route.usable:
        # A spawn has to run somewhere, so the draw answers even when nothing is
        # left; saying so on stderr keeps the model on stdout exactly one line.
        note(f"model_pool: nothing is usable, drawn anyway: {how}", args.quiet)
    if args.explain:
        note(f"draw: {how}", args.quiet)
        for row in routes:
            share = "-" if row.share is None else f"{row.share:.2f}"
            note(f"  {row.tier:<5} {row.key:<44} {row.usable!s:<5} {share:<5} {row.weight:<5.2f} {row.why}", args.quiet)
    if args.json:
        print(json.dumps({
            "model": route.key,
            "provider": route.provider["id"],
            "id": route.model["id"],
            "name": route.model.get("name"),
            "tier": route.tier,
            "share": route.share,
            "reason": how,
            "weights": {tier: weights[tier] for tier in order},
        }, ensure_ascii=False, separators=(",", ":")))
        return
    print(route.key)


def cmd_status(args, config, catalog):
    routes, state = prepared(args, config, catalog)
    payload = []
    for route in sorted(routes, key=lambda item: (item.provider["id"], item.model["id"])):
        cached = (state.get("readings") or {}).get(route.provider["id"]) or {}
        detail = (route.reading or {}).get("detail")
        payload.append({
            "model": route.key,
            "tier": route.tier,
            "usable": route.usable,
            "share": route.share,
            "why": route.why,
            "detail": detail,
            "read_at": cached.get("at"),
            "unit": route.unit,
        })
    if args.json:
        print(json.dumps({"routes": payload}, ensure_ascii=False, indent=1))
        return
    for row in payload:
        share = "-" if row["share"] is None else f"{row['share']:.2f}"
        if not row["usable"]:
            state_text = "out"
        else:
            state_text = "unscored" if row["share"] is None else "usable"
        age = ""
        if row["read_at"]:
            age = f" ({int(time.time() - float(row['read_at']))}s old)"
        print(f"{row['tier']:<5} {row['model']:<38} {state_text:<8} {share:<5} {row['why']}{age}")
        if row["detail"]:
            print(f"      {row['detail']}")


def cmd_probe(args, config, catalog):
    """Ask every live route whether this account may run it, and remember the answer."""
    routes, state = prepared(args, config, catalog)
    probes = state.setdefault("probes", {})
    timeout = float(args.timeout if args.timeout is not None else config["pool"]["probe_timeout"])
    for route in routes:
        credential = catalog.credential(route.provider)
        if not credential:
            print(f"{route.key:<44} skipped  no credential")
            continue
        if not args.force:
            cached = probes.get(route.key) or {}
            refresh = float(config["pool"].get("probe_refresh") or 0)
            if cached.get("at") is not None and refresh > 0 and time.time() - float(cached["at"]) <= refresh:
                verdict, detail = cached.get("verdict"), cached.get("detail")
                print(f"{route.key:<44} {verdict:<8} {detail} (cached)")
                continue
        verdict, detail = entitlement.probe(route.provider, route.model["id"], credential, timeout)
        probes[route.key] = {"at": time.time(), "verdict": verdict, "detail": detail}
        print(f"{route.key:<44} {verdict:<8} {detail}")
    try:
        quota.save_state(state)
    except OSError as error:
        note(f"model_pool: cannot save the entitlement cache: {error}")


def probe_stale(state, routes, config):
    """The routes with no entitlement verdict inside the refresh interval."""
    refresh = float(config["pool"].get("probe_refresh") or 0)
    probes = state.get("probes") or {}
    stale = []
    for route in routes:
        cached = probes.get(route.key) or {}
        if refresh > 0 and cached.get("at") is not None and time.time() - float(cached["at"]) <= refresh:
            continue
        stale.append(route)
    return stale


def cmd_tiers(args, config, catalog):
    """The three tiers, their weights, and every model under the rule that placed it."""
    order, weights = weights_of(config, args.weights)
    routes = collect(catalog, config, args.provider, args.tier)
    for tier in order:
        rows = [route for route in routes if route.tier == tier]
        print(f"{tier:<5} weight {weights[tier]:g}  {len(rows)} model(s)")
        for route in rows:
            total, active = tiers.params_of(route.model)
            size = "-" if total is None else f"{total:g}B"
            if total is not None and active is not None and active != total:
                size += f"/{active:g}B"
            print(f"      {route.key:<44} {size:>10}  {route.evidence}")


def cmd_mark(args, config, catalog):
    state = quota.load_state()
    minutes = float(args.minutes if args.minutes is not None else config["pool"]["cooldown"] / 60)
    key = args.target
    if "/" in key:
        provider_id, model_id = key.split("/", 1)
        if provider_id not in catalog.by_id:
            raise SystemExit(f"no provider {provider_id!r} in the roster")
        if not any(model.get("id") == model_id for model in catalog.by_id[provider_id].get("model") or []):
            raise SystemExit(f"provider {provider_id} has no model {model_id!r}")
    elif key not in catalog.by_id:
        raise SystemExit(f"no provider {key!r} in the roster")
    if args.clear:
        state.setdefault("marks", {}).pop(key, None)
        quota.save_state(state)
        print(f"cleared {key}")
        return
    until = time.time() + max(0.0, minutes) * 60
    state.setdefault("marks", {})[key] = {
        "until": until, "reason": args.reason or "marked by hand", "at": time.time(),
    }
    quota.save_state(state)
    print(f"marked {key} out for {minutes:g} min (until {time.strftime('%Y-%m-%d %H:%M', time.localtime(until))})")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="model_pool",
        description="Draw a model this machine can run now, as <provider>/<model id>.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", help="only draw from this provider id")
    common.add_argument("--tier", help="only draw from this tier (flash, pro, sota)")
    common.add_argument("--weights", help="tier weights, e.g. flash=0.5,pro=0.45,sota=0.05")
    common.add_argument("--refresh", type=float, help="seconds a cached reading answers for (0 = ask again)")
    common.add_argument("--timeout", type=float, help="seconds one quota script may take")
    common.add_argument("--cached", action="store_true", help="answer from cached readings only, never run a script")
    common.add_argument("--probe", action="store_true",
                        help="also ask the providers this account is allowed to run (one cheap request per route)")
    common.add_argument("--config", help=f"config file, default {CONFIG_PATH}")
    sub = parser.add_subparsers(dest="command")

    pick = sub.add_parser("pick", parents=[common], help="draw one model and print it (default)")
    pick.add_argument("--json", action="store_true", help="print one JSON object instead of the bare model")
    pick.add_argument("--explain", action="store_true", help="print the candidates and why on stderr")
    pick.add_argument("--quiet", action="store_true", help="no explanation at all")
    pick.add_argument("--seed", type=int, help="draw with this seed, for a repeatable answer")
    pick.set_defaults(handler=cmd_pick)

    status = sub.add_parser("status", parents=[common], help="every route, its tier, and whether it is drawable")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    probe = sub.add_parser("probe", parents=[common], help="ask each provider whether this account may run the model")
    probe.add_argument("--force", action="store_true", help="ask again even when a verdict is still fresh")
    probe.set_defaults(handler=cmd_probe)

    tiers = sub.add_parser("tiers", parents=[common], help="the tiers, their weights, and the models in each")
    tiers.set_defaults(handler=cmd_tiers)

    mark = sub.add_parser("mark", help="keep a provider or a route out of the draw for a while")
    mark.add_argument("target", help="<provider>/<model id>, or a provider id")
    mark.add_argument("--minutes", type=float, help="how long, default the pool's cooldown")
    mark.add_argument("--reason", help="why it is out")
    mark.add_argument("--clear", action="store_true", help="release the mark instead of setting one")
    mark.add_argument("--config", help=f"config file, default {CONFIG_PATH}")
    mark.set_defaults(handler=cmd_mark)
    return parser


def main(argv):
    parser = build_parser()
    if not argv:
        argv = ["pick"]
    elif argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["pick", *argv]
    args = parser.parse_args(argv)
    config, _path, _exists = load_config(getattr(args, "config", None))
    catalog = roster.load()
    if catalog is None:
        raise SystemExit(f"no roster: neither {roster.COMMUNITY_CATALOG} nor {roster.PRIVATE_CATALOG} exists")
    args.handler(args, config, catalog)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BrokenPipeError:
        sys.exit(0)
