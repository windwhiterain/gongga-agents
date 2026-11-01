#!/usr/bin/env python3
"""What a model costs right now, from the roster's own price bands.

The roster already says what each band costs (`[[provider.model.price.band]]`).
What it cannot say is which band applies at a given minute, because that needs
the year's holiday calendar, which changes yearly and does not belong in a file
people hand-edit. So this script answers the second half:

    python providers/price.py --provider deepseek-official --model deepseek-flash
    python providers/price.py --provider deepseek-official --model deepseek-flash --at 2026-02-16T10:00
    python providers/price.py --provider deepseek-official --model deepseek-flash --offline
    python providers/price.py --list

## The day vocabulary

`price.band.days` names days with the five words the roster defines: `workday`,
`weekend`, `holiday`, `rest_day`, `makeup_workday`. Telling them apart needs two
facts, and they are different:

- **is today a statutory holiday** (`holiday`), and
- **is today off as part of a longer break, or worked as a makeup day**
  (`rest_day`, `makeup_workday`).

A dataset that only says "is today a day off" cannot answer the first: 2026-02-15
is the first day of the Spring Festival break and is *not* a statutory holiday,
while DeepSeek's peak rule excludes only statutory holidays. So the source is
`chinese-days`, which publishes both.

## Offline

`--offline` skips the network and judges by weekday alone: Monday to Friday is
`workday`, the weekend is `weekend`. That is wrong on holidays and on makeup days,
and it says so in `approximate` rather than pretending.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.request

# The roster's own words, and the only ones `price.band.days` may use.
DAYS = ("workday", "weekend", "holiday", "rest_day", "makeup_workday")

# Where the calendar comes from. `chinese-days` publishes one JSON object per year
# with `holidays` (放假调休全集), `workdays` (调休上班日) and `inLieuDays`; the
# authority for what counts as a *statutory* holiday is the State Council list, so
# that is what `STATUTORY` records need to come from.
SOURCES = {
    "calendar": "https://cdn.jsdelivr.net/npm/chinese-days/dist/years/{year}.json",
    "statutory": "https://cdn.jsdelivr.net/npm/chinese-days/dist/years/{year}.json",
}

CACHE = os.path.join(os.path.expanduser("~"), ".agents", "cache")
CACHE_TTL_DAYS = 30

# The statutory dates, written down: a small, published, yearly fact. The remote
# calendar says which days are off, never *why* -- and the difference between a
# statutory holiday and a day borrowed into a longer break is exactly what
# DeepSeek's peak rule turns on.
HERE = os.path.dirname(os.path.abspath(__file__))
STATUTORY = os.path.join(HERE, "statutory.toml")


def statutory_of(year):
    """`(dates, makeup_workdays)` for one year, or `(set(), set())` when not written."""
    try:
        import tomllib

        with open(STATUTORY, "rb") as handle:
            table = tomllib.load(handle)
    except (OSError, ValueError):
        return set(), set()
    for entry in table.get("year") or []:
        if entry.get("year") == year:
            return set(entry.get("dates") or []), set(entry.get("makeup_workdays") or [])
    return set(), set()


def cache_path(year):
    return os.path.join(CACHE, f"chinese-days-{year}.json")


def fetch_year(year, timeout=10):
    """The year's calendar, from the cache when it is fresh enough."""
    path = cache_path(year)
    if os.path.isfile(path):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(path)
        if age < CACHE_TTL_DAYS * 86400:
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, ValueError):
                pass
    url = SOURCES["calendar"].format(year=year)
    with urllib.request.urlopen(url, timeout=timeout) as answer:
        payload = json.load(answer)
    os.makedirs(CACHE, exist_ok=True)
    temporary = f"{path}.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temporary, path)
    return payload


def day_of(moment, calendar):
    """Which of the five words names this date, or `None` when the year is unknown.

    # Invariant

    `holiday` is the statutory list and nothing else. A day off as part of a longer
    break is `rest_day`, even though the remote calendar files both under
    "holidays": the peak rule that uses these words excludes statutory holidays
    only, so collapsing the two would charge a peak price on a break that is not a
    statutory holiday, and an off-peak price on one that is.
    """
    text = moment.strftime("%Y-%m-%d")
    statutory, makeup = statutory_of(moment.year)
    if text in statutory:
        return "holiday"
    if calendar is None:
        # No remote table: the written list still knows the statutory days and the
        # makeup workdays, so use those and fall back to the weekday.
        if text in makeup:
            return "makeup_workday"
        return weekday_day_of(moment)
    holidays = set(calendar.get("holidays") or [])
    workdays = set(calendar.get("workdays") or {})
    if text in workdays or text in makeup:
        return "makeup_workday"
    if text in holidays:
        return "rest_day"
    if moment.weekday() >= 5:
        return "weekend"
    return "workday"


def weekday_day_of(moment):
    """The offline answer: weekday alone, which is wrong on holidays and makeups."""
    return "weekend" if moment.weekday() >= 5 else "workday"


def in_window(moment, window):
    """Whether a time falls inside one `{start, end}` window; `end` may be 24:00."""
    start = window.get("start")
    end = window.get("end")
    if not start or not end:
        return False
    now = moment.strftime("%H:%M")
    if end == "24:00" or end == "00:00":
        return now >= start or (end == "24:00" and now >= start)
    if start <= end:
        return start <= now < end
    # A window that wraps past midnight, e.g. 22:00-08:00.
    return now >= start or now < end


def applies(days, moment, calendar, offline):
    """Whether a band whose `days` is this list applies at this moment."""
    if not days:
        return True
    if days == "all" or days == "全部":
        return True
    # Even offline, the written statutory table answers: it is a local file, so a
    # holiday is not guessed at just because the network is not there.
    named = day_of(moment, None if offline else calendar)
    if named is None:
        return False
    return named in days


def band_price(band):
    """The numbers a band states, in its own currency, skipping its metadata."""
    skip = {"id", "days", "hours", "note", "source", "retrieved", "input_over", "unit"}
    return {k: v for k, v in band.items() if k not in skip}


def choose(model, moment, calendar, offline):
    """`(band, why)` for one model at one moment: the first band whose window fits."""
    price = model.get("price") or {}
    bands = price.get("band") or []
    for band in bands:
        days = band.get("days")
        hours = band.get("hours")
        if days is not None and not applies(days, moment, calendar, offline):
            continue
        if hours and not any(in_window(moment, window) for window in hours):
            continue
        return band, band.get("note") or ""
    # No band fits: the model's base price is what applies.
    return None, "the base price (no band covers this moment)"


def load_roster(path=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default = os.path.join(root, "providers.toml")
    explicit = path or os.environ.get("GONGGA_PROVIDERS_CATALOG") or default
    with open(explicit, "rb") as handle:
        import tomllib

        return tomllib.load(handle), explicit


def find_model(roster, provider_id, model_id):
    for provider in roster.get("provider") or []:
        if provider_id and provider.get("id") != provider_id:
            continue
        for model in provider.get("model") or []:
            if model.get("id") == model_id:
                return provider, model
    return None, None


def report(roster, provider_id, model_id, moment, offline, path):
    provider, model = find_model(roster, provider_id, model_id)
    if model is None:
        return {"error": f"no model {model_id!r}" + (f" under {provider_id}" if provider_id else "")}
    calendar = None
    if not offline:
        try:
            calendar = fetch_year(moment.year)
        except Exception as error:  # noqa: BLE001 - the reading is still useful offline
            offline = True
            note = f"the calendar could not be read ({error}); weekday only"
    else:
        note = ("offline: statutory holidays and makeup workdays come from "
                "statutory.toml; a day off as part of a longer break is read as "
                "weekend, because only the remote table knows it")
    band, why = choose(model, moment, calendar, offline)
    price = model.get("price") or {}
    # A band that states no price of its own is priced at the base -- that is how
    # "peak is the default rate" is written without repeating every number.
    merged = {k: v for k, v in price.items()
              if k in ("input", "output", "cache_read", "currency", "unit", "price_usd")}
    if band:
        merged.update(band_price(band))
    result = {
        "provider": provider["id"],
        "model": model["id"],
        "at": moment.isoformat(timespec="minutes"),
        "day": day_of(moment, None if offline else calendar),
        "band": band.get("id") if band else None,
        "price": merged,
        "currency": price.get("currency"),
        "why": why,
        "roster": path,
    }
    if band and band.get("input_over"):
        result["applies_when"] = f"input over {band['input_over']} tokens"
    if offline:
        result["approximate"] = note
    return result


def main():
    parser = argparse.ArgumentParser(description="What a model costs right now.")
    parser.add_argument("--provider", help="provider id")
    parser.add_argument("--model", help="model id")
    parser.add_argument("--at", help="an ISO moment, e.g. 2026-02-16T10:00 (default: now)")
    parser.add_argument("--offline", action="store_true", help="no calendar: weekday only")
    parser.add_argument("--catalog", help="a roster file other than this repository's")
    parser.add_argument("--list", action="store_true", help="every model with price bands")
    args = parser.parse_args()

    roster, path = load_roster(args.catalog)
    if args.at:
        moment = datetime.datetime.fromisoformat(args.at)
    else:
        moment = datetime.datetime.now()

    if args.list:
        for provider in roster.get("provider") or []:
            for model in provider.get("model") or []:
                bands = (model.get("price") or {}).get("band") or []
                if bands:
                    ids = ", ".join(b.get("id", "?") for b in bands)
                    print(f"{provider['id']}/{model['id']}: {ids}")
        return 0

    if not args.model:
        parser.error("--model is required unless --list is given")
    result = report(roster, args.provider, args.model, moment, args.offline, path)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
