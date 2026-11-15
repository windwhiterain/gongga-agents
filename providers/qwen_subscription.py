#!/usr/bin/env python3
"""`qwen-token-plan` allowance, read from Alibaba Cloud's own OpenAPI.

The earlier route read the console's own XHR, which needs a browser session: it lived a
few days, could not be renewed by a machine, and its expiry was silent -- the reading
stopped arriving and the plan looked unreadable (2026-11-15). This reader asks the same
question through `GetSubscriptionStats`, an ordinary signed OpenAPI: a long-lived
AccessKey instead of a cookie. The plan meters a month and nothing shorter, so this
covers every window the console did.

  GET https://modelstudio.cn-beijing.aliyuncs.com/tokenplan/subscription/stats
  x-acs-action: GetSubscriptionStats, x-acs-version: 2026-02-10
  source: https://help.aliyun.com/zh/model-studio/get-subscription-stats

The credential is an AccessKey pair, never the model key: the harness hands a quota
script the provider's API key, and that key signs nothing here, so a value that is not an
id/secret pair is refused by name. Where the pair may live, in this order:

  ALIBABA_CLOUD_ACCESS_KEY_ID + ALIBABA_CLOUD_ACCESS_KEY_SECRET   (environment)
  qwen-accesskey = "<id>:<secret>"                                (preceding whitespace)

in `~/.agents/keys.toml` beside the roster -- never in a file that is committed.

The reading is `percent-left`, the worst seat type's remaining Credits over its total,
as a percentage, the same unit the provider console prints.
"""

import hashlib
import hmac
import os
import sys
import tomllib
import uuid
from urllib.parse import quote

import contract

PROVIDER = "qwen-token-plan"
BASE_URL = "https://modelstudio.cn-beijing.aliyuncs.com"
PATH = "/tokenplan/subscription/stats"
ACTION = "GetSubscriptionStats"
VERSION = "2026-02-10"
ENV_ID = "ALIBABA_CLOUD_ACCESS_KEY_ID"
ENV_SECRET = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
# The variable the harness is told to put the provider's model key in. Nothing reads it:
# it exists so that key never lands in a variable this script does read.
KEY_ENV = "QWEN_TOKEN_PLAN_KEY"
KEYSTORE = os.path.join(os.environ.get("GONGGA_AGENTS_HOME")
                        or os.path.join(os.path.expanduser("~"), ".agents"), "keys.toml")
KEYSTORE_NAME = "qwen-accesskey"


def encode(part):
    """RFC 3986 encoding, which is what the signature is computed over."""
    return quote(str(part), safe="-_.~")


def canonical_request(method, uri, query, headers, payload_hash):
    """The canonical request: method, URI, query, headers, signed names, payload hash."""
    names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in names)
    signed_headers = ";".join(names)
    return "\n".join([method, uri, query, canonical_headers, signed_headers, payload_hash]), signed_headers


def sign(key_id, key_secret, method, uri, query, headers):
    """`Authorization` for one request, by ACS3-HMAC-SHA256.

    # Invariant

    The signed headers are exactly the ones sent: the gateway recomputes this from the
    request, so a header added after signing (or named here but not sent) is a refusal.
    """
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = dict(headers)
    headers["x-acs-content-sha256"] = payload_hash
    canonical, signed_headers = canonical_request(method, uri, query, headers, payload_hash)
    to_sign = "ACS3-HMAC-SHA256\n" + hashlib.sha256(canonical.encode()).hexdigest()
    signature = hmac.new(key_secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (f"ACS3-HMAC-SHA256 Credential={key_id},"
                                f"SignedHeaders={signed_headers},Signature={signature}")
    return headers, payload_hash


def keystore_pair():
    """The AccessKey pair this machine keeps beside the roster, when it keeps one."""
    try:
        with open(KEYSTORE, "rb") as handle:
            kept = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = (kept.get("keys") or {}).get(KEYSTORE_NAME)
    if not isinstance(value, str) or ":" not in value:
        return None
    key_id, _, key_secret = value.partition(":")
    if not key_id.strip() or not key_secret.strip():
        return None
    return key_id.strip(), key_secret.strip()


def credentials(options, environment):
    """The pair to sign with: `--key`, then the environment, then the keystore."""
    passed = contract.js_string(options.key)
    if ":" in passed:
        key_id, _, key_secret = passed.partition(":")
        return key_id.strip(), key_secret.strip()
    key_id = (environment.get(ENV_ID) or "").strip()
    key_secret = (environment.get(ENV_SECRET) or "").strip()
    if key_id and key_secret:
        return key_id, key_secret
    pair = keystore_pair()
    if pair is not None:
        return pair
    raise contract.BalanceError(
        f"no AccessKey for {ACTION}: set {ENV_ID} and {ENV_SECRET}, "
        f"or keep 'qwen-accesskey = \"<id>:<secret>\"' in {KEYSTORE} "
        f"(the model key signs nothing here, so it is not used)")


def percent_of(item):
    """One seat type's remaining share of its Credits, or None when it cannot be read."""
    total = contract.js_number(contract.field(item, "SeatCredits"))
    left = contract.js_number(contract.field(item, "SeatRemainingCredits"))
    if not contract.is_finite(total) or not contract.is_finite(left) or total <= 0:
        return None
    return max(0.0, min(100.0, left / total * 100.0))


def read_stats(options, key_id, key_secret):
    """Ask the OpenAPI once and turn its seats into one reading."""
    host = BASE_URL.split("//", 1)[1].split("/", 1)[0]
    headers = {
        "host": host,
        "x-acs-action": ACTION,
        "x-acs-version": VERSION,
        "x-acs-date": contract.iso_from_ms(contract.now_ms())[:19] + "Z",
        "x-acs-signature-nonce": uuid.uuid4().hex,
    }
    signed, _ = sign(key_id, key_secret, "GET", quote(PATH), "", headers)
    url = f"{BASE_URL}{PATH}"
    payload = contract.get_json(url, None, options.timeout_ms, headers=signed)
    if not contract.is_object(payload):
        raise contract.BalanceError(f"{ACTION} answered no object")
    if contract.field(payload, "Success") is not True:
        code = contract.js_string(contract.field(payload, "Code"))
        message = contract.js_string(contract.field(payload, "Message"))
        raise contract.BalanceError(f"{ACTION} refused: {code or 'no code'} {message}".strip())
    data = contract.field(payload, "Data")
    items = contract.as_list(contract.field(data, "Items"))
    windows = []
    pieces = []
    resets = []
    for item in items:
        seat = contract.js_string(contract.field(item, "SeatType")) or "unspecified"
        percent = percent_of(item)
        if percent is None:
            continue
        window = contract.window_of(seat, percent, 100)
        if window is not None:
            windows.append(window)
        total = contract.js_number(contract.field(item, "SeatCredits"))
        pieces.append(f"{seat} seat {percent:.1f}% left of {contract.num_text(total)} Credits")
        refresh = contract.js_number(contract.field(item, "SeatRefreshTime"))
        if contract.is_finite(refresh) and refresh > 0:
            resets.append(refresh)
    if not windows:
        raise contract.BalanceError(f"{ACTION} answered no seat with a readable share")
    primary = min(windows, key=lambda window: window["remaining"])
    reading = {"remaining": primary["remaining"], "windows": windows}
    if resets:
        reading["resetsAt"] = contract.iso_from_ms(min(resets))
        pieces.append(f"resets {reading['resetsAt']}")
    ends = contract.js_number(contract.field(data, "SubscriptionEndTime"))
    if contract.is_finite(ends) and ends > 0:
        pieces.append(f"subscription ends {contract.iso_from_ms(ends)}")
    if primary["remaining"] <= 0:
        reading["error"] = f"the {primary['name']} allowance is spent"
    reading["detail"] = ", ".join(pieces)
    return {"reading": reading, "payload": payload}

def main():
    # The keystore stands in for the environment, because a hand run has neither: the
    # option reader refuses a missing credential before any script code runs, so the
    # pair it should have found is filled in here.
    environment = dict(os.environ)
    if not (environment.get(ENV_ID) and environment.get(ENV_SECRET)):
        pair = keystore_pair()
        if pair is not None:
            environment[ENV_ID], environment[ENV_SECRET] = pair
    options = contract.read_options(
        sys.argv[1:], environment, ENV_ID, BASE_URL, PROVIDER,
        script_name=os.path.basename(__file__))
    if options is None:
        return
    key_id, key_secret = credentials(options, environment)
    result = read_stats(options, key_id, key_secret)
    contract.emit_reading(result["reading"], raw=options.raw, payload=result.get("payload"))


if __name__ == "__main__":
    sys.exit(contract.run(main))
