"""Whether a provider will actually serve a model, which the roster cannot say.

The roster states what exists and what a request costs; it does not state what
this account bought. A provider that answers `403 AccessDenied.Unpurchased` has
a key the server accepts and a model the server will happily point a session at,
and every request on it still fails -- so a draw that only asked about the
allowance would keep handing that route to spawns.

One almost-free request answers it. The check is deliberately cheap -- one word
in, one token out -- and its verdict is cached for `probe_refresh`, because
entitlement changes when somebody buys something, not between two draws.

What counts as a refusal is the provider *saying* the model is denied: a 401, a
403, or a body that says access was denied. Every other answer proves the
opposite -- a 400 about an unknown parameter, a 429, a 200 -- because the
provider had to admit the request before it could complain about it.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# The body of the probe: one user word, one token back.
PROBE_BODY = {
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 1,
    "stream": False,
}

# Statuses that mean this account cannot run the model: refused credentials, a
# wallet that owes, a model the provider does not serve under that name. A rate
# limit (429) and a bad parameter (400) are not here: those prove the provider
# admitted the request.
REFUSAL_STATUS = {
    401: "the provider refused the credential",
    402: "the provider wants payment: the account owes, or its balance is spent",
    403: "the provider refuses this model for this account",
    404: "the provider serves no model under that name",
}
REFUSAL_WORDS = ("accessdenied", "denied", "unpurchased", "not authorized", "no permission")

ALLOWED = "allowed"
REFUSED = "refused"
UNKNOWN = "unknown"


def openai_endpoint(provider):
    """The endpoint a chat request goes to, or `None` when the provider speaks something else."""
    for endpoint in provider.get("endpoint") or []:
        if endpoint.get("api") == "openai-completions" and endpoint.get("base_url"):
            return endpoint["base_url"].rstrip("/")
    return None


def classify(status, body):
    """`(verdict, detail)` from one answer: the body decides, then the status."""
    text = (body or "").lower()
    message = said(body)
    for word in REFUSAL_WORDS:
        if word in text:
            return REFUSED, message or f"the provider denies this model (HTTP {status})"
    if status in REFUSAL_STATUS:
        return REFUSED, message or f"{REFUSAL_STATUS[status]} (HTTP {status})"
    if status >= 500:
        return UNKNOWN, f"HTTP {status}: the provider failed, which says nothing about the model"
    return ALLOWED, f"answered HTTP {status}, so the model is served"


def said(body, limit=160):
    """The provider's own sentence, when the body is the JSON an error arrives in."""
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return (body or "").strip()[:limit]
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict):
            for key in ("message", "code", "type"):
                if isinstance(error.get(key), str):
                    return f"{error.get(key)}"[:limit]
        for key in ("message", "detail", "error"):
            if isinstance(value.get(key), str):
                return value[key][:limit]
    return json.dumps(value, ensure_ascii=False)[:limit]


def probe(provider, model_id, credential, timeout):
    """`(verdict, detail)` for one provider/model pair, without spending a model turn."""
    base = openai_endpoint(provider)
    if base is None:
        return UNKNOWN, "the roster gives this provider no OpenAI-compatible endpoint to probe"
    body = json.dumps({**PROBE_BODY, "model": model_id}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
        headers={"authorization": f"Bearer {credential}", "content-type": "application/json",
                 "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return classify(answer.status, answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        try:
            text = error.read().decode("utf-8", "replace")
        except OSError:
            text = ""
        return classify(error.code, text)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return UNKNOWN, f"the probe did not reach the provider: {error}"


def host_of(provider):
    base = openai_endpoint(provider)
    return urllib.parse.urlsplit(base).netloc if base else ""
