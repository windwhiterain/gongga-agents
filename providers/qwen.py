#!/usr/bin/env python3
"""`qwen` balance: the Qwen Token Plan's monthly Credits.

The plan has no key-based balance endpoint, and its own FAQ says so the only way
round it is the console. This script reads the same data gateway the subscription
page reads:

  1. `GET {console}/tool/user/info.json`   -> `data.secToken`, the CSRF token
  2. `POST {gateway}?action=BroadScopeAspnGateway` with `api=<inner>` for
     `.../tokenplan/personal/api/v2/usage`        -> the current period's used ratio
     `.../tokenplan/personal/api/v2/quota-config` -> each tier's Credit totals
     `.../tokenplan/personal/api/v2/subscription`-> the subscribed tier

The credential is therefore a **console session cookie**, not an API key: open the
subscription page, copy the whole `Cookie:` header of any request, and store it
under `QWEN_CONSOLE_COOKIE` (or pass it as `--key`), or -- the way the server's own
reader finds it -- under `qwen-console` in `~/.agents/keys.toml`, because the
provider's entry there is the model key. The gateway rejects a custom
`User-Agent` with `PostonlyOrTokenError`, so the request is sent with the
browser's.

The reading is the **percentage left** of the period the account meters: the
console reports the ratio used (`per1MonthPercentage` since the 2026-09 billing
change, `per1WeekPercentage` for a subscription that still meters weekly), and
this script converts it. Credits are reported in `detail` only, because the tier's
total comes from a second call that times out on its own.

This is an interface the provider does not publish and may change or refuse at any
time. Whichever way it is read, the traffic is the user's own console session;
nothing here is printed or forwarded anywhere but stdout.

Port of `dsh-balance/qwen.mjs`.
"""

import datetime
import math
import os
import re
import sys
import tomllib
import uuid

import contract

PROVIDER = "qwen"
GATEWAY_URL = "https://cs-data.qianwenai.com/data/api.json"
CONSOLE_ORIGIN = "https://platform-home.qianwenai.com"
DASHBOARD_URL = f"{CONSOLE_ORIGIN}/analytics/token-plan/individual"
INFO_URL = f"{CONSOLE_ORIGIN}/tool/user/info.json"
ENV_NAME = "QWEN_CONSOLE_COOKIE"
FALLBACK_ENV_NAME = "BAILIAN_CONSOLE_COOKIE"

# Where a cookie may live besides the environment: the keystore beside the roster,
# under a name of its own, because the provider's entry there is the API key and the
# console wants the session, not the key. The same shape `minimax_credit.py` uses.
KEYSTORE = os.path.join(os.environ.get("GONGGA_AGENTS_HOME")
                        or os.path.join(os.path.expanduser("~"), ".agents"), "keys.toml")
KEYSTORE_NAME = "qwen-console"

# The console's own gateway vocabulary, which its front end hardcodes.
GATEWAY_ACTION = "BroadScopeAspnGateway"
GATEWAY_PRODUCT = "sfm_bailian"
API_PREFIX = "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2"
CONSOLE_HOST = "platform-home.qianwenai.com"

# A browser identity: the gateway's human check refuses anything else.
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# The metered periods, most binding first: (window name, used-ratio field, reset
# field, `quota-config` total field). A period is reported only when the console
# returns a ratio for it.
WINDOWS = (
    ("monthly", "per1MonthPercentage", "per1MonthResetTime", "monthly"),
    ("weekly", "per1WeekPercentage", "per1WeekResetTime", "weekly"),
    ("five-hour", "per5HourPercentage", "per5HourResetTime", "fiveHour"),
)

# The nested name the same five-hour total may appear under.
FIVE_HOUR_TOTAL_KEY = "five_hour"


def to_number(value):
    """Read one number out of a console value, which may be a string with separators."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[,\s]", "", value)
    if cleaned == "":
        return None
    parsed = contract.js_number(cleaned)
    return parsed if math.isfinite(parsed) else None


def to_epoch_ms(value):
    """Read one instant out of a console value: epoch seconds, millis, or a date string."""
    if value is None:
        return None
    text = contract.js_string(value).strip()
    if text == "":
        return None
    if re.fullmatch(r"\d{10}", text):
        return int(text) * 1000
    if re.fullmatch(r"\d{13}", text):
        return int(text)
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A date the console prints without an offset is in the console's zone,
        # which is the machine's own -- the same reading JavaScript's Date gives.
        parsed = parsed.astimezone()
    return int(parsed.timestamp() * 1000)


def cookie_value(cookie, name):
    """Read one cookie's value out of a `Cookie:` header line."""
    match = re.search(rf"(?:^|;\s*){name}=([^;]*)", contract.js_string(cookie))
    return None if match is None else match.group(1).strip()


def read_sec_token(cookie, timeout_ms, info_url=INFO_URL, fetch=None):
    """Read the CSRF token the gateway's form body has to carry.

    A cookie that already carries `sec_token`, or a token the caller passes, is
    used when this call cannot answer -- a token expired by a page refresh is the
    far more likely failure than a changed URL.
    """
    try:
        status, ok, body = contract.send(
            info_url, method="GET",
            headers={"accept": "application/json", "cookie": contract.js_string(cookie),
                     "user-agent": BROWSER_USER_AGENT},
            body=None, timeout_ms=timeout_ms, allow_redirect=False, fetch=fetch)
        if not ok:
            return None
        payload = contract.parse_json(info_url, body)
    except Exception:  # noqa: BLE001 - any failure here only costs the token, never the reading
        return None
    data = contract.field(payload, "data")
    token = contract.field(data, "secToken")
    if token is None:
        token = contract.field(payload, "secToken")
    return token if isinstance(token, str) and token != "" else None


def call_gateway(inner, cookie, sec_token, timeout_ms, base_url=GATEWAY_URL, fetch=None):
    """Call one inner API through the console data gateway.

    Raises `BalanceError` on a transport failure, a refusal, or a body the
    gateway's contract does not describe.
    """
    api = f"{API_PREFIX}/{inner}"
    query = contract.form_encode([
        ("action", GATEWAY_ACTION), ("product", GATEWAY_PRODUCT), ("api", api), ("_v", "undefined"),
    ])
    params = {
        "Api": api,
        "V": "1.0",
        "Data": {
            "cornerstoneParam": {
                "feTraceId": str(uuid.uuid4()),
                "feURL": DASHBOARD_URL,
                "protocol": "V2",
                "console": "ONE_CONSOLE",
                "productCode": "p_efm",
                "switchUserType": 3,
                "domain": CONSOLE_HOST,
                "consoleSite": "QIANWENAI",
                "userNickName": "",
                "userPrincipalName": "",
                "xsp_lang": "zh-CN",
            },
        },
    }
    anonymous = cookie_value(cookie, "cna")
    if anonymous:
        params["Data"]["cornerstoneParam"]["X-Anonymous-Id"] = anonymous
    form = [("product", GATEWAY_PRODUCT), ("action", GATEWAY_ACTION), ("region", "cn-beijing"),
            ("language", "zh-CN"), ("params", contract.json_text(params))]
    if sec_token is not None and sec_token != "":
        form.append(("sec_token", sec_token))
    payload = contract.post_form_json(f"{base_url}?{query}", form, timeout_ms, headers={
        "content-type": "application/x-www-form-urlencoded",
        "cookie": contract.js_string(cookie),
        "origin": CONSOLE_ORIGIN,
        "referer": DASHBOARD_URL,
        "user-agent": BROWSER_USER_AGENT,
    }, fetch=fetch)

    refused = re.search(r"NotAuthorised|NotLogined", contract.json_text(payload)[:500], re.IGNORECASE)
    if refused is not None:
        data = contract.field(payload, "data")
        code = contract.field(data, "errorCode")
        if code is None:
            code = contract.field(payload, "errorCode")
        if code is None:
            code = refused.group(0)
        raise contract.BalanceError(
            f"{code}: the console session was refused; copy a fresh `Cookie:` line from the "
            f'subscription page into "{KEYSTORE_NAME}" in {KEYSTORE}')
    inner_data = contract.field(contract.field(payload, "data"), "DataV2")
    envelope = contract.field(inner_data, "data")
    if not contract.is_object(envelope):
        raise contract.BalanceError(
            f'the gateway answered no data body for "{inner}": {contract.json_text(payload)[:200]}')
    if contract.field(envelope, "success") is False:
        raise contract.BalanceError(
            f'the gateway refused "{inner}": {contract.json_text(envelope)[:200]}')
    body = contract.field(envelope, "data")
    return body if contract.is_object(body) else envelope


def optional_gateway(inner, cookie, sec_token, timeout_ms, base_url, fetch=None):
    """Call one inner API whose failure costs context rather than the reading."""
    try:
        return call_gateway(inner, cookie, sec_token, timeout_ms, base_url=base_url, fetch=fetch)
    except contract.BalanceError:
        # The ratio in `usage` is the reading; a tier total or an expiry date that
        # does not come back is context this script reports without.
        return None


def keystore_cookie():
    """The cookie kept beside the roster under a name of its own, if any.

    # Invariant

    It is a separate entry from the provider's own, because the provider's entry is
    the API key and the console wants a session. The keystore is the one place a
    secret lives on this machine, so a cookie goes there rather than into a file of
    its own.
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
    session: sent as a cookie it comes back `BailianGateway.Login.NotLogined`, which
    would read as a refused session when it means "no cookie was ever sent". So a
    value that does not look like a `Cookie:` line is refused by name instead.
    """
    if "=" not in contract.js_string(options.key):
        raise contract.BalanceError(
            f'no console cookie: add "{KEYSTORE_NAME}" to the [keys] table of {KEYSTORE}, '
            f"or set {ENV_NAME} -- copy the `Cookie:` line of a signed-in console session "
            "(the model key is not a console session)")
    return options.key


def read_balance(options, fetch=None):
    """Read the Qwen Token Plan's remaining allowance.

    The two side calls are each allowed to fail: a tier total this script cannot
    read costs the Credits in `detail`, not the ratio the plan is scored by.
    """
    cookie = cookie_of(options)
    try:
        # The console publishes the CSRF token to a signed-in session; a cookie
        # that already carries one is used before the extra call is made.
        token = cookie_value(cookie, "sec_token")
        if token is None:
            token = read_sec_token(cookie, options.timeout_ms, fetch=fetch)
        usage = call_gateway("usage", cookie, token, options.timeout_ms, options.base_url, fetch=fetch)
        quota = optional_gateway("quota-config", cookie, token, options.timeout_ms, options.base_url, fetch=fetch)
        subscription = optional_gateway("subscription", cookie, token, options.timeout_ms,
                                        options.base_url, fetch=fetch)
        return {"reading": parse(usage, quota, subscription), "payload": {"usage": usage, "quota": quota,
                                                                         "subscription": subscription}}
    except contract.BalanceError as error:
        refused = contract.refused_reading(error)
        if refused is None:
            raise
        return {"reading": refused}


def parse(usage, quota, subscription):
    """Read the plan's remaining allowance out of the console's three bodies.

    Raises `BalanceError` when no period carries a ratio.
    """
    spec_code = contract.field(subscription, "specCode")
    if not (isinstance(spec_code, str) and spec_code != ""):
        spec_code = contract.field(subscription, "SpecCode")
        spec_code = spec_code if isinstance(spec_code, str) and spec_code != "" else ""
    if contract.is_object(quota) and spec_code != "" and contract.is_object(contract.field(quota, spec_code)):
        scoped = contract.field(quota, spec_code)
    else:
        scoped = quota

    windows = []
    totals = {}
    for name, ratio_key, reset_key, total_key in WINDOWS:
        ratio = to_number(contract.field(usage, ratio_key))
        if ratio is None:
            continue
        window = contract.window_of(name, math.floor(max(0, 1 - min(1, ratio)) * 1000 + 0.5) / 10, 100)
        if window is None:
            continue
        window.update(reset_of(usage, reset_key))
        windows.append(window)
        total = to_number(contract.field(scoped, total_key))
        if total is None:
            total = to_number(contract.field(scoped, FIVE_HOUR_TOTAL_KEY))
        if total is not None:
            totals[name] = total
    if not windows:
        raise contract.BalanceError(
            "the console meters no per1Month/per1Week/per5Hour period for this account "
            "(a non-Token-Plan key, or a field set that changed)")

    primary = windows[0]
    total = totals.get(primary["name"])
    ends_at = to_epoch_ms(contract.field(subscription, "endTime"))
    pieces = []
    if spec_code != "":
        pieces.append(f"{spec_code} plan")
    pieces.append(f"{contract.num_text(primary['remaining'])}% left")
    if total is not None:
        pieces.append(f"of {contract.num_text(total)} Credits")
    if primary.get("resetsAt") is not None:
        pieces.append(f"resets {primary['resetsAt']}")
    if ends_at is not None:
        pieces.append(f"subscription ends {contract.iso_from_ms(ends_at)}")

    reading = {
        "remaining": primary["remaining"],
        "windows": [{"name": window["name"], "remaining": window["remaining"], "capacity": window["capacity"]}
                    for window in windows],
    }
    if primary.get("resetsAt") is not None:
        reading["resetsAt"] = primary["resetsAt"]
    if primary["remaining"] <= 0:
        reading["error"] = f"the {primary['name']} allowance is spent"
    reading["detail"] = ", ".join(pieces)
    return reading


def reset_of(usage, key):
    """The reset instant one period's field names, when it names one."""
    at = to_epoch_ms(contract.field(usage, key))
    return {} if at is None else {"resetsAt": contract.iso_from_ms(at)}


def main():
    # A hand run has no credential in the environment, and this script's credential
    # is a console session that lives in the keystore -- so the keystore fills the
    # variable the option reader looks for, and the harness-supplied model key is
    # replaced further down.
    environment = dict(os.environ)
    if not environment.get(ENV_NAME) and not environment.get(FALLBACK_ENV_NAME):
        kept = keystore_cookie()
        if kept:
            environment[ENV_NAME] = kept
    options = contract.read_options(
        sys.argv[1:], environment, ENV_NAME, GATEWAY_URL, PROVIDER,
        fallback_env_name=FALLBACK_ENV_NAME, script_name=os.path.basename(__file__))
    if options is None:
        return
    # The harness hands a quota script the provider's API key, which is not this
    # script's credential -- so anything that is not a `Cookie:` line is replaced by
    # the console session, wherever it was kept. A hand-run that passes a real one
    # keeps it.
    if "=" not in contract.js_string(options.key):
        options.key = keystore_cookie() or options.key
    result = read_balance(options)
    contract.emit_reading(result["reading"], raw=options.raw, payload=result.get("payload"))


if __name__ == "__main__":
    sys.exit(contract.run(main))
