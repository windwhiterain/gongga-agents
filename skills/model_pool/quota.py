"""Runs one provider's quota script, caches the reading, and turns it into a score.

The reading shape is the one the roster's scripts print for
`dsh-llm-quota-retry`: `remaining`, optional `windows`, optional `error`. Only
the last non-empty line is the reading, so a script may warn before it answers.

Nothing here decides which model to draw; `share` and `state_of` are the two
facts a draw needs, kept apart from the tiers that weigh them.
"""

import json
import os
import subprocess
import sys
import time

STATE_DIR = os.path.join(os.path.expanduser("~"), ".model_pool")
STATE_PATH = os.path.join(STATE_DIR, "state.json")


def state_path():
    return os.environ.get("MODEL_POOL_STATE") or STATE_PATH


def load_state():
    """The marks and cached readings, or an empty state when there is no file yet."""
    path = state_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {"readings": {}, "marks": {}}
    state.setdefault("readings", {})
    state.setdefault("marks", {})
    return state


def save_state(state):
    """Write the state in one move, so a draw never reads half a file."""
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def parse_reading(text):
    """The reading a script printed, or `None` when it printed no JSON.

    Invariant: the last line is the answer. A script that warns first still
    answers last, and a pretty-printed reading is still one reading.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    last = stripped.splitlines()[-1]
    for candidate in (stripped, last):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        return value if isinstance(value, dict) else None
    return None


def run_script(path, credential, timeout):
    """One quota script, bounded: `(reading, error)` with exactly one of them set."""
    command = [sys.executable, path]
    if credential:
        command += ["--key", credential]
    command += ["--timeout", str(max(1, int(timeout)))]
    try:
        finished = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout + 2,
        )
    except subprocess.TimeoutExpired:
        return None, f"the script did not answer within {timeout}s"
    except OSError as error:
        return None, f"the script could not start: {error}"
    reading = parse_reading(finished.stdout)
    if reading is None:
        said = (finished.stderr or "").strip().splitlines()
        return None, said[-1] if said else f"the script printed no reading (exit {finished.returncode})"
    return reading, None


def window_share(reading):
    """The worst stated window as a fraction of its own ceiling, or `None`.

    A window the provider reports without stating its capacity is context and
    is never scored; a reading with no scored window leaves the route unscored.
    """
    shares = []
    for window in reading.get("windows") or []:
        remaining = window.get("remaining")
        capacity = window.get("capacity")
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            continue
        if not isinstance(capacity, (int, float)) or isinstance(capacity, bool) or capacity <= 0:
            continue
        shares.append(max(0.0, min(1.0, remaining / capacity)))
    return min(shares) if shares else None


def share_of(reading):
    """How much of its own allowance a route has left, in `0..1`, or `None` when unscored."""
    if not reading or reading.get("error"):
        return None
    return window_share(reading)


def minimum_for(unit, minimums):
    """The floor a reading must clear, in the unit the row reports.

    A percentage and a wallet balance are not comparable numbers, so the floor
    is stated per unit family: `percent-left` in points, a money unit in its own
    currency, anything else at zero.
    """
    unit = unit or ""
    if unit.startswith("percent"):
        return float(minimums.get("percent", 0))
    head = unit.split("-")[0]
    if head in ("cny", "rmb", "usd"):
        return float(minimums.get("money", 0))
    return float(minimums.get("other", 0))


def state_of(reading, unit, minimums):
    """`(usable, why)`: false only when the provider itself says the allowance is spent."""
    if reading is None:
        return None, "no reading"
    if reading.get("error"):
        return False, str(reading["error"])
    remaining = reading.get("remaining")
    if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
        return None, "the reading states no remaining amount"
    floor = minimum_for(unit, minimums)
    if remaining <= floor:
        return False, f"{remaining:g} left, at or below the {floor:g} floor for {unit}"
    return True, f"{remaining:g} left"


def fresh(state, provider_id, refresh):
    """The cached entry for one provider, when it is younger than `refresh` seconds.

    The entry, not just the reading: a failure is cached the same way, so a
    provider that cannot answer is asked once per interval instead of once per
    draw.
    """
    cached = (state.get("readings") or {}).get(provider_id)
    if not isinstance(cached, dict) or "at" not in cached:
        return None
    if refresh <= 0 or time.time() - float(cached["at"]) > refresh:
        return None
    return cached


def remember(state, provider_id, reading, failure):
    """Cache one provider's reading, or the failure that stood in for it.

    A failed query keeps the last reading it had: a provider that answered a
    minute ago still has that allowance, and a timeout says nothing about it.
    """
    previous = (state.get("readings") or {}).get(provider_id) or {}
    state.setdefault("readings", {})[provider_id] = {
        "at": time.time(),
        "reading": reading if reading is not None else previous.get("reading"),
        "failure": failure,
    }


def marked(state, key):
    """The mark on a provider or a route, when it is still running."""
    mark = (state.get("marks") or {}).get(key)
    if not isinstance(mark, dict):
        return None
    if float(mark.get("until") or 0) <= time.time():
        return None
    return mark


def active_marks(state):
    """Every mark still running, so `status` can say why a route is out."""
    return {key: mark for key, mark in (state.get("marks") or {}).items() if marked(state, key)}
