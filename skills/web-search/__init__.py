"""Live web search and page fetch against DeepSeek's own tools endpoint.

Two functions, one HTTP call each. This is a REST endpoint, not a model turn:
it costs no model credit and needs no model that supports server-side search.

    search(query, count=6, content=False)   # ranked sources
    fetch(url)                              # one page as markdown
"""

import json
import os
import textwrap
import tomllib
import urllib.error
import urllib.request

#: The tools endpoint. Stated as a variable so a caller can point it elsewhere.
BASE = os.environ.get("DEEPSEEK_TOOLS_URL", "https://api.deepseek.com/v1/tools")

#: The private keystore. The community roster carries no key at all: it is a git
#: checkout, so a key written there would be a commit.
KEYS = os.environ.get(
    "DEEPSEEK_KEYS",
    os.path.join(
        os.environ.get("GONGGA_AGENTS_HOME", os.path.join(os.path.expanduser("~"), ".agents")),
        "keys.toml",
    ),
)


def _key():
    """The key to send: the keystore first, then the environment.

    # Invariant

    The file wins over the variable. A variable is copied into a process once, at
    creation, so one left over from before a rotation would shadow the file; the
    file is what an edit reaches without a restart.
    """
    try:
        with open(KEYS, "rb") as handle:
            key = tomllib.load(handle).get("keys", {}).get("deepseek-official", "")
    except OSError:
        key = ""
    if key:
        return key
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError(f"no deepseek-official in {KEYS}, and no DEEPSEEK_API_KEY")
    return key


def _post(path, body):
    """One JSON body in, one decoded JSON body out; a refusal in one line."""
    request = urllib.request.Request(
        f"{BASE}/{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {_key()}", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as answer:
            raw = answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as refused:
        detail = refused.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"HTTP {refused.code} from {BASE}/{path}\n{detail}") from None
    except urllib.error.URLError as failed:
        raise RuntimeError(f"request to {BASE}/{path} failed: {failed.reason}") from None
    try:
        decoded = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"the endpoint did not return JSON:\n{raw[:800]}") from None
    if not isinstance(decoded, dict):
        raise RuntimeError(f"the endpoint answered a {type(decoded).__name__}, not an object")
    return decoded


def search(query, count=6, content=False):
    """The ranked sources for a query, one block each.

    `count` is how many sources to ask for; `content` also asks for each page's
    body, which is paid for in tokens, so it is off unless a caller needs the
    words and not the links.
    """
    if not query.strip():
        raise ValueError("search needs a query")
    answer = _post(
        "search",
        {"query": query, "count": int(count), "need_content": bool(content)},
    )
    hits = answer.get("results") or []
    if not hits:
        return "no results"
    lines = []
    for number, hit in enumerate(hits, 1):
        title = (hit.get("title") or "").strip() or "(untitled)"
        lines.append(f"{number}. {title}")
        lines.append(f"   {(hit.get('url') or '').strip()}")
        meta = " | ".join(
            part for part in ((hit.get("site_name") or "").strip(), (hit.get("date") or "").strip()) if part
        )
        if meta:
            lines.append(f"   {meta}")
        summary = (hit.get("summary") or hit.get("snippet") or "").strip()
        for line in textwrap.wrap(summary, 110)[:6]:
            lines.append(f"   {line}")
        body = (hit.get("content") or "").strip()
        if body:
            lines.append("   --- content ---")
            lines.extend(f"   {line}" for line in body.splitlines()[:40])
        lines.append("")
    return "\n".join(lines)


def fetch(url):
    """One page as markdown, titled when the endpoint knows the title."""
    if not url.strip():
        raise ValueError("fetch needs a url")
    answer = _post("fetch", {"url": url})
    title = (answer.get("title") or "").strip()
    body = answer.get("content") or ""
    if not body.strip():
        raise RuntimeError("the endpoint returned no content for this URL")
    return f"{title}\n\n{body}" if title else body
