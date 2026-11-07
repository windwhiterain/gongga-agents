#!/usr/bin/env python3
"""`minimax-cn` credits: what a lapsed plan still leaves to spend.

A MiniMax subscription key spends two products, in this order: the M Plan
quarters (5-hour and weekly windows) and then the purchased credits. When the
plan itself ends, `/v1/token_plan/remains` answers `2062 no active token plan
subscription` and the credits are what the same key still runs on -- officially:
"没有 Token Plan 订阅也可以使用积分".

# The one endpoint, and why it takes a cookie

There is no API-key endpoint that reports credits. The console reads them from
`/backend/account/token_plan_credit`, and that endpoint authenticates a **web
session cookie** rather than a key: the key returns `401 not login`. So the
credential here is a `Cookie:` header copied from a signed-in console session,
which is the same shape `qwen.py` uses for its console.

# Invariant

The response carries the account's own API key back in an `api_key` field. It is
never printed, never put in a reading, and never passed on: a balance is not worth
a leaked key. Only the numbers this file names leave it.
"""

import datetime
import os
import sys
import tomllib

import contract

PROVIDER = "minimax-cn"
BASE_URL = "https://www.minimax.cn"
ENV_NAME = "MINIMAX_CONSOLE_COOKIE"
# Where a cookie may live besides the environment: the keystore beside the roster,
# under a name of its own, because the provider's entry there is the API key and
# the console wants the session, not the key.
KEYSTORE = os.path.join(os.environ.get("GONGGA_AGENTS_HOME")
                        or os.path.join(os.path.expanduser("~"), ".agents"), "keys.toml")
KEYSTORE_NAME = "minimax-console"

# The console route the usage page itself calls for its "积分余额" panel.
CREDIT_PATH = "/backend/account/token_plan_credit"

# Status codes meaning the session, not the allowance, is the problem.
SESSION_CODES = (1004, 2049)


def reading_of(payload):
    """The credits left, as the reading the pool reads."""
    body = contract.field(payload, "base_resp")
    status = contract.js_number(contract.field(body, "status_code"))
    if status != 0:
        said = contract.field(body, "status_msg")
        hint = ("; the console cookie has expired -- copy a fresh `Cookie:` line from a "
                "signed-in session") if status in SESSION_CODES else ""
        raise contract.BalanceError(
            f"console error {contract.num_text(status)}: {said if said is not None else 'unknown'}{hint}")

    remaining = contract.js_number(contract.field(payload, "remaining_credits"))
    if not contract.is_finite(remaining):
        raise contract.BalanceError("the console states no remaining_credits")
    reading = {"remaining": max(0, remaining)}

    # When the credits run out, and how much of the purchase is left, are what a
    # plan-less route is judged on; both come from the same response.
    parts = []
    total = contract.js_number(contract.field(payload, "total_credits"))
    used = contract.js_number(contract.field(payload, "used_credits"))
    if contract.is_finite(total) and contract.is_finite(used):
        parts.append(f"{contract.num_text(remaining)} of {contract.num_text(total)} credits left "
                     f"({contract.num_text(used)} spent)")
    else:
        parts.append(f"{contract.num_text(remaining)} credits left")
    expiry = expiry_of(payload)
    if expiry is not None:
        parts.append(f"expires {expiry}")
    reading["detail"] = ", ".join(parts)
    return reading


def expiry_of(payload):
    """The soonest expiry across the credit buckets, as a date, or `None`."""
    buckets = contract.as_list(contract.field(payload, "balance_breakdown") or {}, "buckets")
    soonest = None
    for bucket in buckets:
        stamp = contract.js_number(contract.field(bucket, "expire_time_ms"))
        if contract.is_finite(stamp) and stamp > 0 and (soonest is None or stamp < soonest):
            soonest = stamp
    if soonest is None:
        return None
    return datetime.datetime.fromtimestamp(soonest / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def keystore_cookie():
    """The cookie kept beside the roster under a name of its own, if any.

    # Invariant

    It is a separate entry from the provider's own, because the provider's entry
    is the API key and the console wants a session. The keystore is also the one
    place a secret lives on this machine -- `~/.agents` is not a repository -- so
    a cookie goes there rather than into a file of its own.
    """
    try:
        with open(KEYSTORE, "rb") as handle:
            kept = (tomllib.load(handle).get("keys") or {}).get(KEYSTORE_NAME)
        return kept if isinstance(kept, str) and kept else None
    except (OSError, tomllib.TOMLDecodeError):
        return None


def cookie_of(options):
    """The console session, refusing a credential that is not one.

    A quota script is handed the provider's API key by default, and a key is not a
    session: sending it as a cookie gets `401 not login`, which would read as "no
    allowance" when it means "no cookie". So a value that does not look like a
    `Cookie:` line is refused by name.
    """
    if "=" not in contract.js_string(options.key):
        raise contract.BalanceError(
            f'no console cookie: add "{KEYSTORE_NAME}" to the [keys] table of {KEYSTORE}, '
            f"or set {ENV_NAME} -- copy the `Cookie:` line of a signed-in console session")
    return options.key


def read_balance(options, fetch=None):
    """Query the console and return the reading."""
    status, ok, body = contract.send(
        f"{options.base_url}{CREDIT_PATH}", method="GET",
        headers={"accept": "application/json", "cookie": contract.js_string(cookie_of(options))},
        body=None, timeout_ms=options.timeout_ms, allow_redirect=True, fetch=fetch,
    )
    if not ok:
        raise contract.BalanceError(
            f"HTTP {status} from {CREDIT_PATH}: the console session was refused -- copy a fresh "
            f"`Cookie:` line from a signed-in session")
    payload = contract.parse_json(f"{options.base_url}{CREDIT_PATH}", body)
    return {"reading": reading_of(payload)}


def main():
    options = contract.read_options(
        sys.argv[1:], os.environ, ENV_NAME, BASE_URL, PROVIDER,
        script_name=os.path.basename(__file__))
    if options is None:
        return
    # The harness hands a quota script the provider's API key, which is not this
    # script's credential -- so anything that is not a `Cookie:` line is replaced by
    # the console session, wherever it was kept. A hand-run that passes a real one
    # keeps it.
    if "=" not in contract.js_string(options.key):
        options.key = keystore_cookie() or os.environ.get(ENV_NAME) or options.key
    result = read_balance(options)
    # No `--raw`: the payload carries the account's API key back.
    contract.emit_reading(result["reading"])


if __name__ == "__main__":
    sys.exit(contract.run(main))
