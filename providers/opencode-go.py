#!/usr/bin/env python3
"""`opencode-go` balance: the OpenCode Go plan's rolling usage windows.

`GET https://opencode.ai/zen/go/v1/usage` answers with `rolling`, `weekly`, and
`monthly` windows, each carrying **percent used** (the console's own "used
percentage") plus a reset instant. The reading is what is left of the window that
is furthest spent: `100 - max(percent used)`, so a route is skipped exactly when
the provider would refuse it. Every window is also reported on its own, so a pool
can rank the route by its worst one.

The endpoint is not documented by OpenCode -- it is the call the installed
`dsh-opencode-go` plugin itself makes -- so `--base-url` is the escape hatch if an
account is served elsewhere.

Port of `dsh-balance/opencode-go.mjs`.
"""

import os
import sys

import contract

PROVIDER = "dsh-opencode-go"
BASE_URL = "https://opencode.ai/zen/go/v1"
ENV_NAME = "OPENCODE_API_KEY"

# The usage windows the endpoint reports, most binding first for a human reading it.
WINDOWS = ("rolling", "weekly", "monthly")


def windows_of(reported):
    """Each reported window's percent used, as percent left out of the full 100."""
    out = []
    for window in reported:
        remaining = 0 if window["status"] == "rate-limited" else max(0, 100 - window["percent"])
        entry = contract.window_of(window["name"], remaining, 100)
        if entry is not None:
            out.append(entry)
    return out


def parse(payload):
    """Read the least remaining allowance out of the usage response.

    A window the provider itself marks `rate-limited` decides alone: its percent
    may lag the refusal, and the provider does not serve a request just because a
    stale percentage looks low.

    Raises `BalanceError` when no window carries a numeric percent.
    """
    usage = contract.field(payload, "usage")
    if not contract.is_object(usage):
        raise contract.BalanceError("the response carries no usage object")
    reported = []
    for name in WINDOWS:
        window = contract.field(usage, name)
        if not contract.is_object(window):
            continue
        percent = contract.js_number(contract.raw(window, "percent"))
        if not contract.is_finite(percent):
            continue
        reported.append({"name": name, "percent": percent,
                         "status": contract.field(window, "status"),
                         "resets_at": contract.field(window, "resetsAt")})
    if not reported:
        raise contract.BalanceError("no usage window carries a numeric percent")
    windows = windows_of(reported)
    detail = ", ".join(f"{window['name']} {contract.num_text(window['percent'])}% used" for window in reported)
    limited = next((window for window in reported if window["status"] == "rate-limited"), None)
    if limited is not None:
        reading = {"remaining": 0, "windows": windows}
        if isinstance(limited["resets_at"], str) and limited["resets_at"] != "":
            reading["resetsAt"] = limited["resets_at"]
        reading["error"] = f"the {limited['name']} window is rate limited"
        reading["detail"] = detail
        return reading
    worst = reported[0]
    for window in reported[1:]:
        if window["percent"] > worst["percent"]:
            worst = window
    reading = {"remaining": max(0, 100 - worst["percent"]), "windows": windows}
    if isinstance(worst["resets_at"], str) and worst["resets_at"] != "":
        reading["resetsAt"] = worst["resets_at"]
    reading["detail"] = detail
    return reading


def read_balance(options, fetch=None):
    """Query the usage endpoint and return the reading."""
    try:
        payload = contract.get_json(f"{options.base_url}/usage", options.key, options.timeout_ms, fetch=fetch)
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
