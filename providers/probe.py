#!/usr/bin/env python3
"""Offline probe for the Python balance scripts.

Drives each provider's response parser over the payload shapes their endpoints
are known to answer with, and checks the reading `dsh-llm-quota-retry` would
decide on, down to the usage windows it reports alongside the one number.

    python probe.py          # parsers only: no network, no credentials
    python probe.py --live   # also query every provider whose credential is set

`--live` calls each script's own `read_balance` in-process -- no child process, no
pipes -- and prints the reading or the provider's refusal, never the credential.

Port of `dsh-balance/probe.mjs`; the fixtures are that file's, verbatim, so a
green run here is evidence about the port and not about a payload nobody measured.
"""

import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The scripts import `contract` from their own directory; running them by file
# path does not put that directory on the path for a spec-loaded import.
sys.path.insert(0, HERE)

import contract  # noqa: E402  (the path line above has to come first)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deepseek = load("deepseek")
kimi = load("kimi-coding")
minimax = load("minimax")
qwen = load("qwen")

checks = []
throws = re.compile(r".")


def check(name, body):
    """Register one probe check."""
    checks.append((name, body))


def response(status, ok, body):
    return (status, ok, body)


def fake_gateway(bodies):
    """A gateway that answers one JSON body per inner API and records the requests."""
    requests = []

    def transport(url, method="GET", headers=None, body=None, timeout_ms=10_000, allow_redirect=True):
        requests.append({"url": str(url), "headers": headers or {}, "body": body or ""})
        if "tool/user/info.json" in str(url):
            return response(200, True, json.dumps({"data": {"secToken": "csrf-from-console"}}))
        inner = inner_of(requests[-1])
        envelope = {"data": {"DataV2": {"data": {"success": True, "data": bodies.get(inner, {})}}}}
        return response(200, True, json.dumps(envelope))

    return transport, requests


def inner_of(request):
    """The inner API name of one recorded gateway request."""
    params = json.loads(contract.form_decode(request["body"]).get("params", "{}"))
    return str(params.get("Api", "")).split("/")[-1]


def options(key="k", base_url="https://example.test", timeout_ms=1000, model=None):
    return contract.Options(key, base_url, timeout_ms, False, model, "probe.py")


def assert_equal(actual, expected, message=""):
    if actual != expected:
        raise AssertionError(f"{actual!r} != {expected!r} {message}")


def assert_deep(actual, expected, message=""):
    if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise AssertionError(f"{actual!r} != {expected!r} {message}")


def assert_match(value, pattern, message=""):
    if value is None or re.search(pattern, str(value)) is None:
        raise AssertionError(f"{value!r} does not match {pattern!r} {message}")


def assert_raises(body, pattern=throws, message=""):
    try:
        body()
    except Exception as error:  # noqa: BLE001 - a check that expects any failure takes any
        assert_match(str(error), pattern, message)
        return
    raise AssertionError(f"expected a failure matching {pattern!r}, none raised {message}")


check("contract: a window needs a stable name and a finite amount", lambda: (
    assert_deep(contract.window_of("weekly", 2.94, 35), {"name": "weekly", "remaining": 2.94, "capacity": 35}),
    assert_deep(contract.window_of("wallet", 12), {"name": "wallet", "remaining": 12},
                "a provider stating no capacity leaves the window unscored"),
    assert_deep(contract.window_of("wallet", 12, 0), {"name": "wallet", "remaining": 12},
                "a non-positive capacity is not a capacity"),
    assert_deep(contract.window_of("wallet", 12, float("nan")), {"name": "wallet", "remaining": 12}),
    assert_equal(contract.window_of("", 1), None, "an empty name is not a window name"),
    assert_equal(contract.window_of(None, 1), None),
    assert_equal(contract.window_of("weekly", float("nan")), None),
    assert_equal(contract.window_of("weekly", float("inf")), None),
))

check("deepseek: the CNY wallet is the reading, and an unavailable account says so", lambda: (
    assert_equal(deepseek.parse(DEEP_WALLET)["remaining"], 123.45),
    assert_match(deepseek.parse(DEEP_WALLET)["detail"], r"CNY"),
    assert_equal(deepseek.parse({"is_available": True, "balance_infos": [{"currency": "EUR", "total_balance": "1"}]})["remaining"], 1),
    assert_equal("windows" in deepseek.parse(DEEP_WALLET), False, "a prepaid wallet has no usage window to report"),
    assert_equal(UNAVAILABLE["remaining"], 0),
    assert_match(UNAVAILABLE.get("error"), r"unavailable"),
    assert_raises(lambda: deepseek.parse({"balance_infos": []})),
    assert_raises(lambda: deepseek.parse({"balance_infos": [{"currency": "CNY", "total_balance": "not a number"}]})),
))


DEEP_WALLET = {
    "is_available": True,
    "balance_infos": [
        {"currency": "USD", "total_balance": "6.78", "granted_balance": "0.00", "topped_up_balance": "6.78"},
        {"currency": "CNY", "total_balance": "123.45", "granted_balance": "10.00", "topped_up_balance": "113.45"},
    ],
}
UNAVAILABLE = deepseek.parse({"is_available": False, "balance_infos": [{"currency": "CNY", "total_balance": "0.00"}]})


check("kimi-coding: percentages outrank counts, and limit/used is subtracted", lambda: (
    assert_equal(COUNTS["remaining"], 30, "the least remaining of the two rows"),
    assert_match(COUNTS["detail"], r"least remaining 30"),
    assert_deep(COUNTS["windows"], [
        {"name": "limit-1", "remaining": 70, "capacity": 100},
        {"name": "limit-2", "remaining": 30, "capacity": 50},
    ], "each row names its own window, and a count row carries its limit as the capacity"),
    assert_equal(PERCENTS["remaining"], 12.5, "a percentage is the only unit that compares across limits"),
    assert_match(PERCENTS["detail"], r"12\.5%"),
    assert_deep(PERCENTS["windows"], [
        {"name": "limit-1", "remaining": 12.5, "capacity": 100},
        {"name": "limit-2", "remaining": 9, "capacity": 10},
    ], "a percentage row is out of 100 even when the row states a limit"),
    assert_deep(BARE["windows"], [{"name": "limit-1", "remaining": 7}],
                "a row stating no limit leaves its window unscored"),
    assert_raises(lambda: kimi.parse({"limits": []})),
    assert_raises(lambda: kimi.parse({"limits": [{"detail": {}}]})),
))

COUNTS = kimi.parse({"limits": [{"detail": {"limit": 100, "used": 30, "remaining": 70}},
                                {"detail": {"limit": 50, "used": 20}}]})
PERCENTS = kimi.parse({"data": [{"remaining_percent": 12.5}, {"limit": 10, "used": 1}]})
BARE = kimi.parse({"data": [{"remaining": 7}]})


check("kimi-coding: the live payload reads every window it carries, once each", lambda: (
    assert_equal(LIVE_KIMI["remaining"], 0, "a spent monthly window decides even when the summary has room"),
    assert_deep(LIVE_KIMI["windows"], [
        {"name": "five-hour", "remaining": 100, "capacity": 100},
        {"name": "weekly", "remaining": 84, "capacity": 100},
        {"name": "limit_month_total", "remaining": 0, "capacity": 100},
        {"name": "limit_month_code", "remaining": 0.93, "capacity": 100},
    ], "a window limits already produced is not reported again from usages"),
))

LIVE_KIMI = kimi.parse({
    "booster_wallet": {"status": "STATUS_DISABLED", "monthlyUsed": {"currency": "CNY", "priceInCents": "1234"}},
    "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {"limit": "100", "remaining": "100"}}],
    "usage": {"limit": "100", "remaining": "84"},
    "usages": {"limit_5h": {"used_ratio": 0}, "limit_month_total": {"used_ratio": 1},
               "limit_month_code": {"used_ratio": 0.9907}},
})


check("kimi-coding: the usage shares stand in when the other two are absent", lambda: (
    assert_equal(SHARES["remaining"], 83.69, "the tighter of the two shares decides"),
    assert_deep(SHARES["windows"], [
        {"name": "five-hour", "remaining": 100, "capacity": 100},
        {"name": "weekly", "remaining": 83.69, "capacity": 100},
    ], "a used_ratio is the share already spent, so what is left is its complement"),
    assert_match(SHARES["detail"], r"least remaining 83\.69%"),
    assert_equal(kimi.parse({"usages": {"limit_7d": {"used_ratio": 3.5}}})["remaining"], 0,
                 "a share past the whole window clamps instead of reporting more than full"),
    assert_raises(lambda: kimi.parse({"usages": {"limit_7d": {}}}), throws,
                  "a share window with no ratio is not an amount"),
))

SHARES = kimi.parse({"usages": {"limit_5h": {"used_ratio": 0}, "limit_7d": {"used_ratio": 0.1631}}})


def refused_scripts():
    """Every script answers a refusal with zero, and only a refusal."""
    for name, reader, status in [("deepseek", deepseek, 401), ("minimax-cn", minimax, 401),
                                 ("qwen", qwen, 401), ("kimi-coding", kimi, 401)]:
        result = reader.read_balance(options(), fetch=lambda url, **kw: response(status, False, "denied"))
        assert_equal(result["reading"]["remaining"], 0, f"{name} reports the refusal as an exhausted allowance")
        assert_match(result["reading"].get("error"), rf"HTTP {status}", f"{name} says which status refused it")

    def offline(url, **kwargs):
        raise RuntimeError("offline")

    assert_raises(lambda: kimi.read_balance(options(), fetch=offline), r"cannot reach",
                  "a failure that says nothing about the allowance stays a failure")
    assert_raises(lambda: deepseek.read_balance(options(),
                                                fetch=lambda url, **kw: response(500, False, "boom")),
                  r"HTTP 500", "and so does a status that is not about the credential")


check("every script: a refused credential reads zero, and only a refusal does", refused_scripts)


def minimax_windows():
    before = contract.now_ms()
    payload = {
        "base_resp": {"status_code": 0, "status_msg": "success"},
        "model_remains": [
            {"model_name": "MiniMax-M3.1-Flash-Preview", "current_interval_total_count": 1000,
             "current_interval_remaining_percent": 64.5, "remains_time": 3_600_000,
             "current_weekly_total_count": 10_000, "current_weekly_remaining_percent": 81,
             "weekly_remains_time": 86_400_000},
            {"model_name": "hailuo-video-01", "current_interval_total_count": 10,
             "current_interval_remaining_percent": 0, "remains_time": 60_000},
        ],
    }
    reading = minimax.parse(payload)
    assert_equal(reading["remaining"], 64.5, "the video resource is not what a model request spends")
    reset = int(datetime_ms(reading["resetsAt"]))
    if reset < before + 3_500_000:
        raise AssertionError("the reset estimate is not the deciding window's countdown in the future")
    assert_match(reading["detail"], r"MiniMax-M3\.1-Flash-Preview current 64\.5%")
    assert_deep(reading["windows"], [
        {"name": "current", "remaining": 64.5, "capacity": 100},
        {"name": "weekly", "remaining": 81, "capacity": 100},
    ], "one window per selected resource window, under that window's own label")

    two_models = {
        "base_resp": {"status_code": 0},
        "model_remains": [
            {"model_name": "MiniMax-M3.1-Flash-Preview", "current_interval_remaining_percent": 90},
            {"model_name": "MiniMax-M2.7", "current_interval_remaining_percent": 20, "remains_time": 1000},
        ],
    }
    assert_equal(minimax.parse(two_models, "m2.7")["remaining"], 20)
    assert_deep(minimax.parse(two_models, "minimax")["windows"], [
        {"name": "current", "remaining": 90, "capacity": 100},
        {"name": "current", "remaining": 20, "capacity": 100},
    ], "two resources report two windows, and a repeated label is still a distinct window")

    assert_raises(lambda: minimax.parse({"base_resp": {"status_code": 0}, "model_remains": []}))
    assert_raises(lambda: minimax.parse({"base_resp": {"status_code": 0}, "model_remains": [
        {"model_name": "hailuo-video-01", "current_interval_remaining_percent": 5}]}), r"no language-model quota")
    assert_raises(lambda: minimax.parse({"base_resp": {"status_code": 1004, "status_msg": "invalid api key"},
                                         "model_remains": []}), r"Token Plan subscription key")


def datetime_ms(stamp):
    """`Date.parse` of one ISO instant, back to milliseconds."""
    text = str(stamp)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)


check("minimax: the language resources decide, per window, with a reset estimate", minimax_windows)


def qwen_parse():
    quota = {"lite": {"monthly": 11_500, "five_hour": 3_000},
             "standard": {"monthly": 45_000, "five_hour": 3_000},
             "addon_quota": {"extrabundle": 0}}
    reading = qwen.parse({"per1MonthPercentage": 0.349, "per1MonthResetTime": 1_792_000_000_000}, quota,
                         {"specCode": "standard", "remainingDays": 22, "endTime": 1_795_000_000_000})
    assert_equal(reading["remaining"], 65.1, "the plan meters a month, and reports what is left of it")
    assert_deep(reading["windows"], [{"name": "monthly", "remaining": 65.1, "capacity": 100}])
    assert_equal(reading["resetsAt"], contract.iso_from_ms(1_792_000_000_000))
    assert_equal(reading.get("error"), None)
    assert_match(reading["detail"], r"standard plan, 65\.1% left, of 45000 Credits")
    assert_match(reading["detail"], r"subscription ends 2026-11-18T")

    legacy = qwen.parse({"per1WeekPercentage": 1, "per1WeekResetTime": "2026-10-20T03:00:00+08:00"}, None, None)
    assert_equal(legacy["remaining"], 0, "a subscription still metering a week reports that window")
    assert_deep(legacy["windows"], [{"name": "weekly", "remaining": 0, "capacity": 100}])
    assert_match(legacy.get("error"), r"weekly allowance is spent")
    assert_equal(legacy["resetsAt"], "2026-10-19T19:00:00.000Z")

    ratio_only = qwen.parse({"per1MonthPercentage": 0.5}, None, None)
    assert_equal(ratio_only["remaining"], 50, "a tier total this script could not read costs the Credits, not the reading")
    if re.search(r"Credits", ratio_only["detail"]):
        raise AssertionError(f"the Credits should be absent: {ratio_only['detail']}")

    assert_raises(lambda: qwen.parse({}, quota, {"specCode": "standard"}), r"meters no per1Month")
    assert_raises(lambda: qwen.parse(None, quota, None))


check("qwen: the console's used ratio becomes percent left, with the tier total as context", qwen_parse)


def qwen_request():
    assert_equal(qwen.cookie_value("a=1; sec_token=abc; cna=xyz", "sec_token"), "abc")
    assert_equal(qwen.cookie_value("a=1", "sec_token"), None)

    transport, requests = fake_gateway({"usage": {"per1MonthPercentage": 0.25}})
    result = qwen.read_balance(options("a=1; cna=xyz", "https://cs-data.example/data/api.json", 5000),
                               fetch=transport)
    assert_equal(result["reading"]["remaining"], 75)

    info = requests[0]
    assert_match(info["url"], r"tool/user/info\.json$")
    assert_match(info["headers"].get("cookie", ""), r"cna=xyz")

    request = requests[1]
    query = contract.form_decode(request["url"].split("?", 1)[1])
    assert_equal(query.get("action"), "BroadScopeAspnGateway")
    assert_equal(query.get("product"), "sfm_bailian")
    assert_equal(query.get("api"), "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage")
    assert_equal(query.get("_v"), "undefined")
    form = contract.form_decode(request["body"])
    assert_equal(form.get("action"), "BroadScopeAspnGateway")
    assert_equal(form.get("region"), "cn-beijing")
    assert_equal(form.get("sec_token"), "csrf-from-console", "the token comes from the console, not from the user")
    params = json.loads(form.get("params"))
    assert_equal(params["Api"], "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage")
    assert_equal(params["V"], "1.0")
    assert_equal(params["Data"]["cornerstoneParam"]["consoleSite"], "QIANWENAI")
    assert_equal(params["Data"]["cornerstoneParam"]["X-Anonymous-Id"], "xyz",
                 "the console reads its own anonymous id out of the cookie")
    assert_match(request["headers"].get("user-agent", ""), r"Mozilla/5\.0",
                 "the gateway refuses a custom user agent")
    assert_equal(request["headers"].get("content-type"), "application/x-www-form-urlencoded")
    assert_match(request["headers"].get("referer", ""), r"token-plan/individual$")
    assert_deep([inner_of(entry) for entry in requests[1:]], ["usage", "quota-config", "subscription"])


check("qwen: the gateway request carries the session, the CSRF token, and a browser identity", qwen_request)


def qwen_failures():
    refused = lambda url, **kw: response(200, True, json.dumps({
        "data": {"errorCode": "BailianGateway.Login.NotLogined", "errorMsg": "nope"}}))
    assert_raises(lambda: qwen.call_gateway("usage", "a=1", "t", 1000, fetch=refused),
                  r"the console session was refused")

    empty = lambda url, **kw: response(200, True, json.dumps({"code": "200"}))
    assert_raises(lambda: qwen.call_gateway("usage", "a=1", "t", 1000, fetch=empty), r"answered no data body")

    failing = lambda url, **kw: response(500, False, "boom")
    assert_raises(lambda: qwen.call_gateway("usage", "a=1", "t", 1000, fetch=failing), r"HTTP 500")

    no_token = lambda url, **kw: response(401, False, "")

    def offline(url, **kwargs):
        raise RuntimeError("offline")

    assert_equal(qwen.read_sec_token("a=1", 1000, fetch=no_token), None)
    assert_equal(qwen.read_sec_token("a=1", 1000, fetch=offline), None,
                 "a token call that cannot answer leaves the cookie's own sec_token to be used")


check("qwen: a refused session, an unknown envelope, and a missing token each report themselves", qwen_failures)

PROVIDERS = [
    ("deepseek-official", deepseek, "DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    ("kimi-coding", kimi, "KIMI_CODING_API_KEY", "https://api.kimi.com/coding/v1"),
    ("minimax-cn", minimax, "MINIMAX_CN_API_KEY", "https://www.minimax.cn"),
    # `key` is the console session cookie here, not an API key.
    ("qwen", qwen, "QWEN_CONSOLE_COOKIE", "https://cs-data.qianwenai.com/data/api.json"),
]


def main():
    failed = 0
    for name, body in checks:
        try:
            body()
            contract.write_line(f"ok   {name}")
        except Exception as error:  # noqa: BLE001 - one check failing must not stop the rest
            failed += 1
            message = str(error).split("\n")
            contract.write_line(f"FAIL {name}\n     " + "\n     ".join(message))

    if "--live" in sys.argv:
        contract.write_line("\nlive reads (credentials come from this process's environment)")
        for provider, module, env_name, base_url in PROVIDERS:
            key = os.environ.get(env_name, "")
            if key == "":
                contract.write_line(f"skip {provider}: {env_name} is not set")
                continue
            try:
                result = module.read_balance(options(key, base_url, 10_000))
                contract.write_line(f"ok   {provider}: {contract.json_text(result['reading'])}")
            except Exception as error:  # noqa: BLE001 - a provider's refusal is one line, not a crash
                failed += 1
                contract.write_line(f"FAIL {provider}: {error}")

    contract.write_line(f"\n{len(checks) - failed}/{len(checks)} parser checks passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
