#!/usr/bin/env python3
"""`minimax-cn` balance: the China-region Token Plan's per-resource quota.

`GET https://www.minimax.cn/v1/token_plan/remains` answers with one entry per
quota resource (`model_remains[]`), each carrying a current-interval and a weekly
window whose `*_remaining_percent` is the literal percentage left. Only the
endpoint is documented by MiniMax; the response fields follow the community
client, and this file's field handling is a port of `minimax_quota.py`, which
recorded the same knowledge for a console report.

A route pool needs *one* number, so this reads the language-model resources
(`minimax-m*`, `general`) -- the ones a model request actually spends -- and takes
the smaller of its two windows. `--model <name>` narrows that to one model's own
resource instead. Every selected window is also reported on its own, under that
window's label.

Port of `dsh-balance/minimax.mjs`.
"""

import os
import sys

import contract

PROVIDER = "minimax-cn"
BASE_URL = "https://www.minimax.cn"
ENV_NAME = "MINIMAX_CN_API_KEY"

# Another name this deployment's console tooling already publishes the key under.
FALLBACK_ENV_NAME = "MINIMAX_TOKEN_PLAN_KEY"

# Status codes that mean "this is not a Token Plan subscription key".
AUTH_STATUS_CODES = (1004, 2049)

# `model_name` keywords that mark the resources a model request spends.
LANGUAGE_KEYWORDS = ("minimax-m", "general")

# (label, total key, remaining-percent key, remaining-time key) per window.
WINDOWS = (
    ("current", "current_interval_total_count", "current_interval_remaining_percent", "remains_time"),
    ("weekly", "current_weekly_total_count", "current_weekly_remaining_percent", "weekly_remains_time"),
)


def is_language(model_name):
    """Whether one resource is a language-model resource."""
    lowered = contract.js_string(model_name).lower()
    return any(keyword in lowered for keyword in LANGUAGE_KEYWORDS)


def reported_windows_of(entry):
    """One entry's windows, as measurements carrying their own reset estimate."""
    out = []
    for label, _total_key, percent_key, remains_key in WINDOWS:
        percent = contract.js_number(contract.raw(entry, percent_key))
        if not contract.is_finite(percent):
            continue
        remains = contract.js_number(contract.raw(entry, remains_key))
        # Only the endpoint's own millisecond countdown is trusted for a reset
        # instant; a window with no countdown simply reports none.
        resets_at = (
            contract.iso_from_ms(contract.now_ms() + remains)
            if contract.is_finite(remains) and remains >= 0 else None
        )
        out.append({"label": label, "percent": percent, "resets_at": resets_at})
    return out


def parse(payload, model=None):
    """Read the least remaining quota out of the Token Plan response.

    Raises `BalanceError` when the endpoint refused, or no selected resource
    carries a percentage.
    """
    base = contract.field(payload, "base_resp")
    stated_code = contract.field(base, "status_code")
    status = contract.js_number(0 if stated_code is None else stated_code)
    if status != 0:
        hint = ("; check that this is the China-region Token Plan subscription key, "
                "not a pay-as-you-go API key") if status in AUTH_STATUS_CODES else ""
        message = contract.field(base, "status_msg")
        raise contract.BalanceError(
            f"Token Plan endpoint error {contract.num_text(status)}: "
            f"{message if message is not None else 'unknown'}{hint}"
        )
    entries = contract.as_list(payload, "model_remains")
    if not entries:
        raise contract.BalanceError("the response carries no model_remains")
    if model is None:
        selected = [entry for entry in entries if is_language(contract.raw(entry, "model_name"))]
    else:
        needle = model.lower()
        selected = [
            entry for entry in entries
            if needle in contract.js_string(contract.field(entry, "model_name") or "").lower()
        ]
    if not selected:
        raise contract.BalanceError(
            "no language-model quota resource is reported" if model is None
            else f'no quota resource matches model "{model}"'
        )
    reported = [
        {**window, "model": contract.js_string(contract.field(entry, "model_name") or "")}
        for entry in selected for window in reported_windows_of(entry)
    ]
    if not reported:
        raise contract.BalanceError("the selected resources carry no remaining-percent window")
    worst = reported[0]
    for window in reported[1:]:
        if window["percent"] < worst["percent"]:
            worst = window
    windows = [w for w in (
        contract.window_of(window["label"], max(0, window["percent"]), 100) for window in reported
    ) if w is not None]
    reading = {"remaining": max(0, worst["percent"])}
    reading["windows"] = windows
    if worst["resets_at"] is not None:
        reading["resetsAt"] = worst["resets_at"]
    reading["detail"] = ", ".join(
        f"{window['model']} {window['label']} {contract.num_text(window['percent'])}%" for window in reported
    )
    return reading


def read_balance(options, fetch=None):
    """Query the Token Plan endpoint and return the reading."""
    try:
        payload = contract.get_json(
            f"{options.base_url}/v1/token_plan/remains", options.key, options.timeout_ms, fetch=fetch)
        return {"reading": parse(payload, options.model), "payload": payload}
    except contract.BalanceError as error:
        refused = contract.refused_reading(error)
        if refused is None:
            raise
        return {"reading": refused}


def main():
    options = contract.read_options(
        sys.argv[1:], os.environ, ENV_NAME, BASE_URL, PROVIDER,
        fallback_env_name=FALLBACK_ENV_NAME, model=True, script_name=os.path.basename(__file__))
    if options is None:
        return
    result = read_balance(options)
    contract.emit_reading(result["reading"], raw=options.raw, payload=result.get("payload"))


if __name__ == "__main__":
    sys.exit(contract.run(main))
