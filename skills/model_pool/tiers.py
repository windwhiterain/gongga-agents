"""What the four tiers mean, and where a model's tier comes from.

A tier is a **fact about the model**, written in the roster next to its price
(`[[provider.model]] tier = "..."`). So this module does not classify anything:
it names the tiers, and reads the one the roster states.

That is a deliberate reversal. The tier used to be computed here from the price,
the parameter count and the name, because nothing published it. The price turned
out to be the wrong thing to rank by -- measured against the Artificial Analysis
intelligence index in October 2026, DeepSeek V4.1 Flash scores 45 and its own V4
Pro 36 -- so the judgement moved to the file a person edits, where it can be
argued with, and this module became a reader.
"""

# The tiers, in the order a draw considers them. `test` is last and, by weight,
# never drawn unless asked for: those models cannot do the work, they are here to
# exercise the harness.
ORDER = ("flash", "pro", "sota", "test")

# What each tier is *for*. This is the rule a spawn is placed by, so it names the
# work and not the model: search, explore, investigate and implement are flash;
# hard implement and review are pro; plan and core review are sota. Reading it the
# other way round -- "flash is the cheap one, so put anything cheap there" -- is how
# a review ends up on a model picked for its price.
MEANING = {
    "flash": "search, explore, investigate, implement",
    "pro": "hard implement, review",
    "sota": "plan, core review",
    "test": "basically unusable for programming; only for testing the harness",
}


def tier_of(catalog, model):
    """`(tier, evidence)` for one roster row, with no classification of its own.

    A model the roster does not place is reported as unplaced rather than filed
    under a guess: a silent guess is how a `test` model ends up in a spawn.
    """
    tier = catalog.tier_of(model)
    if tier is None:
        return None, "the roster states no tier"
    if tier not in ORDER:
        return None, f'the roster states tier "{tier}", which is not one of {", ".join(ORDER)}'
    return tier, f"the roster says {tier}"


def params_of(model):
    """`(total, active)` in billions as the roster states them, `None` where it does not."""
    params = model.get("params") or {}
    total = params.get("total")
    active = params.get("active")
    ok = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    return (float(total) if ok(total) else None, float(active) if ok(active) else None)


def size_text(model):
    """The size as one column: `552B/16B`, `27B`, or `-` when unstated."""
    total, active = params_of(model)
    if total is None:
        return "-"
    if active is None or active == total:
        return f"{total:g}B"
    return f"{total:g}B/{active:g}B"
