#!/usr/bin/env python3
"""`kimi-coding` balance: the Kimi coding plan's usage rows.

`GET https://api.kimi.com/coding/v1/usages` (falling back to `/v1/usage` on a 404)
answers with exactly four top-level keys -- `booster_wallet`, `limits`, `usage`,
and `usages` -- and no others. `limits` carries one entry per rate-limit window
with the window's own `window.duration`/`window.timeUnit` beside the amounts;
`usage` is the current window's summary; `usages` carries one share already spent
per window as `used_ratio` in 0-1, under a key of the provider's choosing. Every
one of those is an allowance that can refuse a request, so a reading that leaves
any of them out reports more headroom than the account has, and a reading that
takes a *used* share for what is left reports the opposite. A window one source
already reported is not reported again, which is what keeps the overlapping
sources from counting the same window twice.

A row may report `remaining` outright, or `limit` and `used` for the reading to
subtract, or a remaining percentage. Percentages win when any row carries one,
because they are the only unit that compares across limits; otherwise the reading
is the least remaining count.

`booster_wallet` reports the extra-usage wallet's monthly spend in CNY, a
different unit from the percentages above; mixing the two in one reading would
make its least-value meaningless, so it is left out rather than converted.

The endpoint is not documented -- the community client
https://github.com/Golden0Voyager/kimi-code-usage calls it -- and it needs the
**coding plan** key (`sk-kimi-...`), not an open-platform key, which is why a
`User-Agent` is sent as well.

Port of `dsh-balance/kimi-coding.mjs`.
"""

import math
import os
import re
import sys

import contract

PROVIDER = "kimi-coding"
BASE_URL = "https://api.kimi.com/coding/v1"
ENV_NAME = "KIMI_CODING_API_KEY"

# The client this endpoint's reverse-engineered call is known to answer.
HEADERS = {"user-agent": "KimiCLI/1.6"}

# The rate-limit window each length of minutes names, so one window is not reported twice.
WINDOW_NAMES = {300: "five-hour", 10080: "weekly"}

# The window a known `usages` key names; any other key is its own window name.
USAGE_WINDOW_NAMES = {"limit_5h": "five-hour", "limit_7d": "weekly"}


def minutes_of(window):
    """The length of one rate-limit window in minutes."""
    duration = contract.js_number(contract.raw(window, "duration"))
    if not contract.is_finite(duration):
        return None
    unit = contract.js_string(contract.field(window, "timeUnit") or "")
    if "MINUTE" in unit:
        return duration
    if "HOUR" in unit:
        return duration * 60
    if "DAY" in unit:
        return duration * 1440
    if "MONTH" in unit:
        return duration * 43200
    return duration / 60


def rows_of(payload):
    """Collect every usage row a response shape may carry, with its window length.

    `limits` is the shape the endpoint answers with today; `data` is the older
    array form. The window sits beside the amounts rather than inside them, so it
    is carried along here.
    """
    rows = []
    for limit in contract.as_list(payload, "limits"):
        detail = contract.field(limit, "detail")
        if contract.is_object(detail):
            rows.append({"detail": detail, "minutes": minutes_of(contract.field(limit, "window"))})
    data = contract.field(payload, "data")
    if isinstance(data, list):
        rows += [{"detail": row, "minutes": None} for row in data]
    return rows


def parse(payload):
    """Read the least remaining allowance out of the usage response.

    Each row is also reported as its own window, named by the length it states
    where it states one and by its position where it does not. A percentage row is
    out of 100; a count row is out of its own `limit`, when the row states one.

    Raises `BalanceError` when no row carries a usable amount.
    """
    percentages = []
    counts = []
    windows = []
    reported = set()
    state = {"considered": 0}

    def take(row, name):
        """Take one row's amounts as a window of the reading."""
        stated_percent = contract.raw(row, "remaining_percent")
        if stated_percent is contract.ABSENT or stated_percent is None:
            stated_percent = contract.raw(row, "remainingPercent")
        percent = contract.js_number(stated_percent)
        if contract.is_finite(percent):
            percentages.append(percent)
            entry = contract.window_of(name, percent, 100)
            if entry is not None:
                windows.append(entry)
            return True
        limit = contract.js_number(contract.raw(row, "limit"))
        used = contract.js_number(contract.raw(row, "used"))
        stated = contract.js_number(contract.raw(row, "remaining"))
        if contract.is_finite(stated):
            remaining = stated
        elif contract.is_finite(limit) and contract.is_finite(used):
            remaining = limit - used
        else:
            return False
        counts.append(remaining)
        entry = contract.window_of(name, remaining, limit)
        if entry is not None:
            windows.append(entry)
        return True

    for row in rows_of(payload):
        name = None if row["minutes"] is None else WINDOW_NAMES.get(row["minutes"])
        if name is not None:
            reported.add(name)
        state["considered"] += 1
        take(row["detail"], name if name is not None else f"limit-{state['considered']}")

    # `usage` is the current window's own summary and `usages` states, per key, the
    # share already spent in that window. A window one source already reported is
    # left alone rather than counted twice.
    usage = contract.field(payload, "usage")
    if contract.is_object(usage) and "weekly" not in reported:
        state["considered"] += 1
        if take(usage, "weekly"):
            reported.add("weekly")

    for key, entry in contract.entries_of(contract.field(payload, "usages")):
        ratio = contract.js_number(contract.raw(entry, "used_ratio"))
        if not contract.is_finite(ratio):
            continue
        name = USAGE_WINDOW_NAMES.get(key) or key
        if name in reported:
            continue
        # The share is already spent, so what is left is its complement; a share
        # past the whole window clamps rather than reporting more than full.
        remaining = math.floor((1 - min(1, max(0, ratio))) * 10000 + 0.5) / 100
        state["considered"] += 1
        reported.add(name)
        percentages.append(remaining)
        window = contract.window_of(name, remaining, 100)
        if window is not None:
            windows.append(window)

    considered = state["considered"]
    if percentages:
        remaining = min(percentages)
        return {"remaining": remaining, "windows": windows,
                "detail": f"{considered} limit(s), least remaining {contract.num_text(remaining)}%"}
    if counts:
        remaining = min(counts)
        return {"remaining": remaining, "windows": windows,
                "detail": f"{considered} limit(s), least remaining {contract.num_text(remaining)}"}
    raise contract.BalanceError(
        "no usage row carries a remaining percentage, a remaining count, or limit and used")


def read_balance(options, fetch=None):
    """Query the usage endpoint and return the reading.

    A refused credential is the shared contract's answer; the 404 that sends this
    script to the older endpoint is the only shape here that is not.
    """
    try:
        payload = contract.get_json(f"{options.base_url}/usages", options.key, options.timeout_ms,
                                    headers=HEADERS, fetch=fetch)
        return {"reading": parse(payload), "payload": payload}
    except contract.BalanceError as error:
        refused = contract.refused_reading(error)
        if refused is not None:
            return {"reading": refused}
        if re.search(r"HTTP 404", str(error)) is None:
            raise
        payload = contract.get_json(f"{options.base_url}/usage", options.key, options.timeout_ms,
                                    headers=HEADERS, fetch=fetch)
        return {"reading": parse(payload), "payload": payload}


def main():
    options = contract.read_options(
        sys.argv[1:], os.environ, ENV_NAME, BASE_URL, PROVIDER, script_name=os.path.basename(__file__))
    if options is None:
        return
    result = read_balance(options)
    contract.emit_reading(result["reading"], raw=options.raw, payload=result.get("payload"))


if __name__ == "__main__":
    sys.exit(contract.run(main))
