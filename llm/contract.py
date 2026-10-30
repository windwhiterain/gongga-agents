#!/usr/bin/env python3
"""The shared half of every balance script: the argument and credential
conventions, one bounded HTTP request, and the one-line JSON reading
`dsh-llm-quota-retry` parses.

A balance script is a *plain command*: the plugin runs it through the harness's
subprocess service with the credential its `apiKeyEnv` names in the environment,
and reads one JSON object from stdout:

  {
    "remaining": <number>,
    "windows"?: [{ "name": "...", "remaining": <number>, "capacity"?: <number> }, ...],
    "resetsAt"?: "<iso>", "error"?: "...", "detail"?: "..."
  }

`remaining` is what the provider has left, in the unit the row's `unit` field
names. `windows` reports the usage windows behind that one number, each in the
script's own unit; the plugin ranks a route by its worst window --
`min(remaining / capacity)` -- so a window whose provider states no `capacity`
is context for a person and is never scored.

Anything else on stdout, or a non-zero exit, is a *missing reading* for the
plugin -- it never fails the request -- so a script is free to be strict: exit 2
with one line on stderr and the plugin will log exactly that line.

This is the Python port of `dsh-balance/lib/contract.mjs`; the reading shape is
the wire contract and is byte-compatible with it, including the `Number()` and
`toISOString()` behaviours a provider payload is read through.
"""

import json
import math
import re
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_TIMEOUT_MS = 10_000

# One line of usage help for every script, so --help never needs its own copy.
USAGE = (
    "--key <credential>        use this credential instead of the environment variable",
    "--base-url <url>          query another origin (a region, a gateway, a proxy)",
    "--timeout <seconds>       request timeout, default 10",
    "--raw                     include the provider payload in the output",
    "--help                    print this help",
)


class BalanceError(Exception):
    """A diagnostic a script reports as a failed reading rather than a crash."""


class _Absent:
    """A key the provider did not send, which is not the same as a key sent as null."""

    def __repr__(self):
        return "undefined"


ABSENT = _Absent()


def raw(obj, name):
    """`obj?.[name]` where the difference between absent and null changes the reading.

    `Number(undefined)` is `NaN` and `Number(null)` is `0`, so a parser that
    treats a missing field as an unusable one has to see the absence.
    """
    if isinstance(obj, dict) and name in obj:
        return obj[name]
    return ABSENT


class Options:
    """One script's resolved command line."""

    def __init__(self, key, base_url, timeout_ms, raw, model, script_name):
        self.key = key
        self.base_url = base_url
        self.timeout_ms = timeout_ms
        self.raw = raw
        self.model = model
        self.script_name = script_name


def js_number(value):
    """`Number(value)` as JavaScript applies it to a provider's field.

    The distinction matters for a reading: a JSON `null` is `0` and a missing
    key is `NaN`, so `Number.isFinite` separates them exactly the way the .mjs
    parsers do.
    """
    if value is ABSENT or value is None:
        return math.nan if value is ABSENT else 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def is_finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def num_text(value):
    """How JavaScript's `${number}` prints a number: `12`, not `12.0`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value == int(value) and abs(value) < 1e21:
        return str(int(value))
    return str(value)


def js_string(value):
    """`String(value)` as JavaScript applies it to a provider's field.

    `undefined` becomes its own name and `null` becomes `"null"`, so a missing key
    lowercased into a match test reads `"undefined"` exactly the way the .mjs
    parsers do.
    """
    if value is ABSENT:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return num_text(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Array#toString joins with commas, and joins a hole as the empty string.
        return ",".join("" if item is None else js_string(item) for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def json_text(value):
    """`JSON.stringify` as a diagnostic prints it: no spaces, nulls kept."""
    if value is ABSENT:
        return "undefined"
    try:
        return json.dumps(reading_output(value), ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def iso_from_ms(ms):
    """`new Date(ms).toISOString()`: UTC, three fractional digits, `Z`."""
    milliseconds = int(ms)
    whole = math.floor(milliseconds / 1000)
    stamp = time.gmtime(whole)
    return "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ" % (
        stamp.tm_year, stamp.tm_mon, stamp.tm_mday, stamp.tm_hour, stamp.tm_min, stamp.tm_sec,
        milliseconds - whole * 1000,
    )


def now_ms():
    return int(time.time() * 1000)


def read_options(argv, env, env_name, base_url, provider, fallback_env_name=None, model=False, script_name=None):
    """Read one script's command line.

    Returns an `Options`, or `None` when `--help` was asked for. Raises
    `BalanceError` when an argument is unknown or unusable.
    """
    options = Options("", base_url, DEFAULT_TIMEOUT_MS, False, None, script_name or f"{provider}.py")
    index = 0
    while index < len(argv):
        argument = argv[index]

        def value_of(flag, index=index):
            if index + 1 >= len(argv) or str(argv[index + 1]).startswith("--"):
                raise BalanceError(f"{flag} needs a value")
            return argv[index + 1]

        if argument == "--key":
            options.key = value_of("--key")
            index += 2
            continue
        if argument == "--base-url":
            options.base_url = re.sub(r"/+$", "", value_of("--base-url"))
            index += 2
            continue
        if argument == "--timeout":
            seconds = js_number(value_of("--timeout"))
            if not is_finite(seconds) or seconds <= 0:
                raise BalanceError("--timeout must be a positive number of seconds")
            options.timeout_ms = int(round(seconds * 1000))
            index += 2
            continue
        if argument == "--model":
            if not model:
                raise BalanceError(f"{provider} does not take --model")
            options.model = value_of("--model")
            index += 2
            continue
        if argument == "--raw":
            options.raw = True
            index += 1
            continue
        if argument == "--help":
            print_help(provider, env_name, base_url, model, options.script_name)
            return None
        raise BalanceError(f'unknown argument "{argument}"; try --help')

    if options.key == "":
        # `??`, not `||`: a credential set to the empty string is still set, and
        # the script reports no credential rather than reaching for the fallback.
        stated = env.get(env_name)
        if stated is None and fallback_env_name is not None:
            stated = env.get(fallback_env_name)
        options.key = stated if stated is not None else ""
    if options.key == "":
        hint = "" if fallback_env_name is None else f" (or {fallback_env_name})"
        raise BalanceError(f"no credential: set {env_name}{hint}, or pass --key")
    return options


def print_help(provider, env_name, base_url, model, script_name):
    """Print one script's usage."""
    lines = [
        f"usage: python {script_name} [options]",
        "",
        f"Reads {provider}'s remaining allowance and prints one JSON reading, for",
        "dsh-llm-quota-retry route pools.",
        "",
        f"credential: {env_name}",
        f"origin:     {base_url}",
        "",
    ]
    lines += [f"  {text}" for text in USAGE]
    if model:
        lines.append("  --model <name>            read one model's quota instead of the provider-wide one")
    write_line("\n".join(lines))


def get_json(url, key, timeout_ms, headers=None, fetch=None):
    """Request one JSON document, as the contract's `getJson`."""
    request_headers = {"accept": "application/json", "authorization": f"Bearer {key}"}
    request_headers.update(headers or {})
    status, ok, body = send(
        url, method="GET", headers=request_headers, body=None, timeout_ms=timeout_ms,
        allow_redirect=True, fetch=fetch,
    )
    if not ok:
        raise BalanceError(f"HTTP {status} from {url}: {compact(body)}")
    return parse_json(url, body)


def post_form_json(url, form, timeout_ms, headers=None, fetch=None):
    """Request one JSON document with a form-encoded POST.

    A console gateway answers only to the verb, content type, and headers its
    own front end sends, so the body arrives as already-ordered pairs and the
    caller owns the headers that identify the session. A redirect is refused:
    the gateway's own front end never issues one.
    """
    status, ok, body = send(
        url, method="POST", headers=dict(headers or {}), body=form_encode(form),
        timeout_ms=timeout_ms, allow_redirect=False, fetch=fetch,
    )
    if not ok:
        raise BalanceError(f"HTTP {status} from {url}: {compact(body)}")
    return parse_json(url, body)


def send(url, method, headers, body, timeout_ms, allow_redirect, fetch=None):
    """One bounded request, or a `BalanceError` naming why it could not reach."""
    transport = fetch or default_transport
    try:
        return transport(url, method=method, headers=headers, body=body,
                         timeout_ms=timeout_ms, allow_redirect=allow_redirect)
    except BalanceError:
        raise
    except Exception as error:  # a transport failure is one message, whatever raised it
        reason = str(error) or type(error).__name__
        raise BalanceError(f"cannot reach {url}: {reason}") from error


def parse_json(url, body):
    try:
        return json.loads(body)
    except ValueError:
        raise BalanceError(f"non-JSON response from {url}: {compact(body)}")


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def default_transport(url, method="GET", headers=None, body=None, timeout_ms=10_000, allow_redirect=True):
    """The stdlib transport: one request, `(status, ok, body)` out.

    Handlers are built per call on purpose -- a redirect policy belongs to the
    request that asked for it, not to a process-wide opener.
    """
    opener = build_opener() if allow_redirect else build_opener(_RefuseRedirect())
    request = Request(url, data=body.encode("utf-8") if body is not None else None,
                      headers=headers or {}, method=method)
    try:
        with opener.open(request, timeout=timeout_ms / 1000.0) as response:
            return response.status, response.getcode() < 400, decode(response.read())
    except HTTPError as error:
        return error.code, False, decode(error.read())
    except URLError as error:
        raise RuntimeError(str(error.reason)) from error
    except ssl.SSLError as error:
        raise RuntimeError(str(error)) from error


def decode(raw):
    return raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)


def form_encode(pairs):
    """`URLSearchParams.toString()`, so a gateway sees the body its own client sends."""
    out = []
    for name, value in pairs:
        encoded = quote(str(value), safe="*-._").replace("%20", "+").replace(" ", "+")
        out.append(f"{quote(str(name), safe='*-._').replace('%20', '+')}={encoded}")
    return "&".join(out)


def form_decode(text):
    """The pairs of one form body, for a check that reads a request back."""
    from urllib.parse import parse_qsl

    return dict(parse_qsl(text or "", keep_blank_values=True))


def refused_reading(error):
    """The reading for a provider whose credential the endpoint refused.

    A refused credential is an answer about the allowance rather than a missing
    one: the endpoint will serve nothing on that key, so the route leaves its
    pool instead of being drawn as if it still had headroom. Every other failure
    -- an unreachable host, a timeout, a body that is not the payload -- stays a
    failure, because those say nothing about the allowance.
    """
    if not isinstance(error, BalanceError):
        return None
    status = re.match(r"^HTTP (401|403)\b", str(error))
    if status is None:
        return None
    return {"remaining": 0, "error": f"the endpoint refused this credential (HTTP {status.group(1)}); it can answer nothing"}


def as_output(value):
    """A number the way JSON.stringify prints a JavaScript number.

    Every number a provider sends is a float64 in Python and a `Number` in
    JavaScript, and `88` is what the reading has to say, not `88.0`.
    """
    if isinstance(value, float) and math.isfinite(value) and value == int(value) and abs(value) < 1e21:
        return int(value)
    return value


def reading_output(value):
    """One reading, with every number written the JavaScript way."""
    if isinstance(value, dict):
        return {key: reading_output(item) for key, item in value.items()}
    if isinstance(value, list):
        return [reading_output(item) for item in value]
    return as_output(value)


def emit_reading(reading, raw=False, payload=None):
    """Print one reading and end the script successfully."""
    value = reading_output(reading)
    if raw and payload is not None:
        value["payload"] = reading_output(payload)
    write_line(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def window_of(name, remaining, capacity=None):
    """Build one usage window of a reading.

    `None` when the name is not a non-empty string or `remaining` is not a
    finite number. A capacity that is not a finite positive number is dropped,
    leaving the window unscored.
    """
    if not isinstance(name, str) or name == "" or not is_finite(remaining):
        return None
    entry = {"name": name, "remaining": as_output(remaining)}
    if is_finite(capacity) and capacity > 0:
        entry["capacity"] = as_output(capacity)
    return entry


def report_failure(error):
    """Report one failed reading the way the plugin expects: one line, exit 2."""
    message = str(error) if isinstance(error, Exception) else str(error)
    write_line(message or type(error).__name__, stream=sys.stderr)
    return 2


def run(main):
    """Run one script's main, reporting any failure as a failed reading."""
    try:
        main()
        return 0
    except Exception as error:  # noqa: BLE001 - a crash would hide the reason from the plugin
        return report_failure(error)


def write_line(text, stream=None):
    """One UTF-8 line, whatever console code page the host runs under."""
    stream = stream or sys.stdout
    buffer = getattr(stream, "buffer", None)
    data = (text + "\n").encode("utf-8")
    if buffer is None:
        stream.write(text + "\n")
    else:
        buffer.write(data)
        buffer.flush()


def compact(body):
    """Shorten one response body for a diagnostic line."""
    return re.sub(r"\s+", " ", body).strip()[:200]


def field(obj, name):
    """`obj?.name`: a missing key and a non-object both read as absent."""
    return obj.get(name) if isinstance(obj, dict) else None


def as_list(obj, name):
    """The array one key holds, or an empty list when it is not an array."""
    value = field(obj, name)
    return value if isinstance(value, list) else []


def is_object(value):
    """The `x !== null && typeof x === 'object'` test every parser starts from."""
    return isinstance(value, dict)


def entries_of(value):
    """`Object.entries(x ?? {})`, in the order the provider sent them."""
    return list(value.items()) if is_object(value) else []
