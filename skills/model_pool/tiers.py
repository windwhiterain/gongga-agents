"""Which of the three tiers a model belongs to, and on what evidence.

The three names -- `flash`, `pro`, `sota` -- are this tool's, not any vendor's, so
something has to define them. Two facts about a model are published and can be
defined from:

- **what it costs**, the roster's own price (the fact a pool spreads load over),
- **how big it is**, the roster's `[provider.model.params]` (total and active).

**The tier is the price class.** A pool exists to spend several allowances
across several price levels, so the class that matters is what a request there
costs; the bands are wide enough that the roster's own clusters sit in them.

**The price is not a capability ranking, and the roster proves it.** Measured
against the Artificial Analysis intelligence index in October 2026, DeepSeek V4.1
Flash scores 45 and its own V4 Pro 36; MiMo V2.6 Pro scores 46 at $0.87 per
million output tokens while Kimi K3.1 Pro scores 55 at ten times that. So a
`flash` draw is cheap *and may be the smartest model here*, and asking for `sota`
buys the expensive frontier, not the best answer. `model_pool tiers` prints the
parameters and the price next to each tier so this stays visible.

Order of the rules: an override, then the price, then the parameter count (for a
model priced nowhere, such as a local build), then the name.
"""

import re

DEFAULTS = {
    # Edges in output USD per million tokens; the last band takes what is left.
    "band": [
        {"tier": "flash", "max_output_usd": 1.5},
        {"tier": "pro", "max_output_usd": 5.0},
    ],
    "sota": "sota",
    # Used only when the roster states a price in CNY and no USD one.
    "cny_per_usd": 6.9,
    # Used only for a model the roster prices nowhere.
    "params_band": [
        {"tier": "flash", "max_total_billion": 100},
        {"tier": "pro", "max_active_billion": 50},
    ],
    "default": "pro",
    # Used only for a model with no price and no parameter count.
    "rule": [
        {"tier": "flash", "any": ["flash", "mini", "lite", "turbo"]},
        {"tier": "sota", "any": ["max", "sota", "ultra", "opus"]},
        {"tier": "pro", "any": ["pro"]},
    ],
    # One model placed against the rules above, by `<provider>/<id>` or bare id.
    "model": {},
}


def output_usd(model, settings):
    """The model's output price per million tokens in USD, or `None` when unpriced.

    The roster's own `price_usd` wins, because a vendor states it. A price in CNY
    is converted at the configured rate, which is a comparison and never a quote.
    """
    price = model.get("price") or {}
    stated = (price.get("price_usd") or {}).get("output")
    if isinstance(stated, (int, float)) and not isinstance(stated, bool):
        return float(stated)
    local = price.get("output")
    if not isinstance(local, (int, float)) or isinstance(local, bool):
        return None
    currency = price.get("currency")
    if currency == "USD":
        return float(local)
    if currency in ("CNY", "RMB"):
        rate = float(settings.get("cny_per_usd") or 6.9)
        return float(local) / rate if rate > 0 else None
    return None


def params_of(model):
    """`(total, active)` in billions as the roster states them, and `None` where it does not."""
    params = model.get("params") or {}
    total = params.get("total")
    active = params.get("active")
    ok = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    return (float(total) if ok(total) else None, float(active) if ok(active) else None)


def by_price(value, settings):
    """The tier a price lands in, or `None` when the roster states no price."""
    if value is None:
        return None
    for band in settings.get("band") or []:
        edge = band.get("max_output_usd")
        if edge is not None and value <= float(edge):
            return band["tier"]
    return settings.get("sota") or "sota"


def by_params(total, active, settings):
    """The tier a size lands in, for a model the roster prices nowhere."""
    for band in settings.get("params_band") or []:
        limit_total = band.get("max_total_billion")
        limit_active = band.get("max_active_billion")
        if limit_total is not None and total is not None and total <= float(limit_total):
            return band["tier"]
        if limit_active is not None and active is not None and active <= float(limit_active):
            return band["tier"]
    return None


def by_name(model_id, settings):
    """The tier a name suggests, for a model with no price and no size."""
    tokens = set(token for token in re.split(r"[^0-9a-z]+", model_id.lower()) if token)
    lowered = model_id.lower()
    for rule in settings.get("rule") or []:
        for word in rule.get("any") or []:
            word = word.lower()
            if (word in lowered) if re.search(r"[^0-9a-z]", word) else (word in tokens):
                return rule["tier"]
    return settings.get("default") or "pro"


def classify(provider, model, settings):
    """`(tier, evidence)` for one roster row, in the order the module documents."""
    override = (settings.get("model") or {}).get(f"{provider['id']}/{model['id']}")
    if override is None:
        override = (settings.get("model") or {}).get(model["id"])
    if override:
        return override, "override"
    price = output_usd(model, settings)
    tier = by_price(price, settings)
    if tier:
        return tier, f"price: {price:g} USD per 1M output tokens"
    total, active = params_of(model)
    tier = by_params(total, active, settings)
    if tier:
        size = f"{total:g}B" if total is not None else ""
        if total is not None and active is not None and active != total:
            size += f" / {active:g}B active"
        return tier, f"params: {size or 'stated'}, and the roster prices it nowhere"
    return by_name(model["id"], settings), "name: the roster prices it nowhere and states no size"
