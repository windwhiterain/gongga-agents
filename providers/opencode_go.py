#!/usr/bin/env python3
"""OpenCode Go dynamic model discovery.

GETs the Go `/models` endpoint, which carries every model the subscription can
reach plus its relay metadata, and prints the list as gongga `FileModel` JSON --
one array on stdout, each element named the way the server deserialises it.

Every model is reached on the Responses face: the gateway answers the Responses
shape on `/zen/go/v1/responses` for every model id, including the ones the
catalogue tags `chat-completions`, `messages` or `generate-content`, so no
per-model face is sent. The prices are USD per 1M tokens, as the endpoint states.
"""

import json
import os
import sys
import urllib.request

BASE_URL = "https://opencode.ai"
MODELS_PATH = "/zen/go/v1/models"
ENV_NAME = "OPENCODE_API_KEY"
USER_AGENT = "gongga-harness/discovery"
TIMEOUT_SECS = 20


def modality_kind(mime):
    """Map a wire MIME/kind to gongga's coarse modality word."""
    if mime == "text":
        return "text"
    if mime.startswith("image/") or mime == "image":
        return "image"
    if mime.startswith("video/") or mime == "video":
        return "video"
    if mime.startswith("audio/") or mime == "audio":
        return "audio"
    return None


def convert(entry):
    """Read one /models item into one gongga FileModel dict."""
    limit = entry.get("limit") or {}
    cost = ((entry.get("cost") or {}).get("text")) or {}
    input_cost = cost.get("input") or {}
    cache = input_cost.get("cache") or {}
    reasoning = (entry.get("capabilities") or {}).get("reasoning") or {}

    modalities = []
    for mime in (entry.get("modalities") or {}).get("input", []):
        kind = modality_kind(mime)
        if kind is not None and kind not in modalities:
            modalities.append(kind)

    model = {
        "id": entry.get("id"),
        "name": entry.get("name") or entry.get("id"),
        "context": limit.get("context"),
        "max_output_tokens": limit.get("output"),
        "modalities": modalities,
        "reasoning_levels": list(reasoning.get("effort") or []),
    }
    price = {}
    if input_cost.get("price") is not None:
        price["input"] = input_cost["price"]
    if cache.get("read") is not None:
        price["cache_read"] = cache["read"]
    if (cost.get("output") or {}).get("price") is not None:
        price["output"] = cost["output"]["price"]
    if price:
        model["price"] = price
    return model


def fetch_models(key):
    """GET the model list, returning the `data` array."""
    url = BASE_URL + MODELS_PATH
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + key)
    # The gateway's Cloudflare blocks the default urllib identity (error 1010).
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("data") or []


def main():
    key = os.environ.get(ENV_NAME)
    if not key:
        print("no key in " + ENV_NAME, file=sys.stderr)
        return 1
    try:
        entries = fetch_models(key)
    except Exception as error:  # network or decode: the server records the failure
        print("cannot read models: " + str(error), file=sys.stderr)
        return 1
    models = [convert(entry) for entry in entries if entry.get("id")]
    print(json.dumps(models, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
