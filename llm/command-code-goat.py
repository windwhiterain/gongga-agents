#!/usr/bin/env python3
"""`command-code-goat` balance: the Command Code GOAT plan's credit windows.

`GET https://api.commandcode.ai/alpha/billing/credits` answers with three enforced
allowances, all in dollars: the monthly credit the plan grants, a five-hour
window, and a weekly window. The reading is what is left of the **tightest** one,
because that is the one that will refuse the next request -- with monthly credits
still in hand a burst can still hit a window cap.

Only the endpoint is documented by Command Code; the field names below follow the
response it answers with today (`credits.monthlyCredits`, and
`windowLimits.fiveHour`/`weekly` as `cap`/`used`), which is why `--raw` is worth
keeping: a provider that renames a field should be visible immediately rather than
as a silent `no reading`.

Port of `dsh-balance/command-code-goat.mjs`.
"""

import math
import os
import sys

import contract

PROVIDER = "command-code-goat"
BASE_URL = "https://api.commandcode.ai/alpha/billing"
ENV_NAME = "COMMAND_CODE_GOAT_API_KEY"

# The rolling windows the endpoint reports, as (label, key) pairs.
WINDOWS = (("five-hour", "fiveHour"), ("weekly", "weekly"))


def cents(value):
    """Round one dollar amount to cents, for a note a person reads."""
    return math.floor(value * 100 + 0.5) / 100


def parse(payload):
    """Read the least remaining allowance out of the credits response.

    The endpoint states a cap for the five-hour and weekly windows only, so the
    monthly credit is reported without one and is never scored.

    Raises `BalanceError` when no allowance carries a usable amount.
    """
    credits = contract.field(payload, "credits")
    window_limits = contract.field(payload, "windowLimits")
    if not contract.is_object(credits) or not contract.is_object(window_limits):
        raise contract.BalanceError("the response carries no credits and windowLimits objects")

    limits = []
    monthly = contract.js_number(contract.raw(credits, "monthlyCredits"))
    if contract.is_finite(monthly):
        limits.append({"name": "monthly", "left": max(0, monthly)})
    for label, key in WINDOWS:
        window = contract.field(window_limits, key)
        if not contract.is_object(window):
            continue
        cap = contract.js_number(contract.raw(window, "cap"))
        used = contract.js_number(contract.raw(window, "used"))
        if not contract.is_finite(cap) or not contract.is_finite(used):
            continue
        reset_at = contract.js_number(contract.raw(window, "resetAt"))
        entry = {"name": label, "left": max(0, cap - used), "cap": cap,
                 "exceeded": contract.field(window, "exceeded") is True}
        if contract.is_finite(reset_at) and reset_at > 0:
            entry["reset_at"] = reset_at
        limits.append(entry)
    if not limits:
        raise contract.BalanceError("no credit or window allowance carries a usable amount")

    windows = [w for w in (
        contract.window_of(limit["name"], limit["left"], limit.get("cap")) for limit in limits
    ) if w is not None]
    tightest = limits[0]
    for limit in limits[1:]:
        if limit["left"] < tightest["left"]:
            tightest = limit
    exhausted = next((limit for limit in limits if limit.get("exceeded")), None)
    flagged = contract.field(window_limits, "exceeded") is not None

    reading = {"remaining": 0 if (exhausted is not None or flagged) else tightest["left"]}
    reading["windows"] = windows
    if tightest.get("reset_at") is not None:
        reading["resetsAt"] = contract.iso_from_ms(tightest["reset_at"])
    if exhausted is not None:
        reading["error"] = f"the {exhausted['name']} window is exhausted"
    elif flagged:
        reading["error"] = ("the provider reports an exceeded window limit "
                            f"({contract.json_text(contract.field(window_limits, 'exceeded'))})")
    elif contract.field(credits, "belowThreshold") is True:
        reading["error"] = "the account is below its credit threshold"
    reading["detail"] = ", ".join(
        f"{limit['name']} {contract.num_text(cents(limit['left']))} left" for limit in limits)
    return reading


def read_balance(options, fetch=None):
    """Query the credits endpoint and return the reading."""
    try:
        payload = contract.get_json(f"{options.base_url}/credits", options.key, options.timeout_ms, fetch=fetch)
        return {"reading": parse(payload), "payload": payload}
    except contract.BalanceError as error:
        refused = contract.refused_reading(error)
        if refused is None:
            raise
        return {"reading": refused}


def main():
    options = contract.read_options(
        sys.argv[1:], os.environ, ENV_NAME, BASE_URL, PROVIDER, script_name=os.path.basename(__file__))
    if options is None:
        return
    result = read_balance(options)
    contract.emit_reading(result["reading"], raw=options.raw, payload=result.get("payload"))


if __name__ == "__main__":
    sys.exit(contract.run(main))
