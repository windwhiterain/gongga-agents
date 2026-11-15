"""The roster's three layers, read as the one shape its readers know.

`[[model]]` states what a model is, `[[route]]` states a way of reaching a
provider, and `[[serving]]` states that one route sells one model -- under whose
wire name, at whose price. The server folds the same three tables into
`[[provider]]` rows (`crates/server/src/providers/layers.rs`); this is that
expansion for the two readers here, so a price or a draw sees the roster the
server sees.

A serving naming a route or a model nobody declared is dropped here rather than
raised: the server reports such a file as broken, and a reader that refused to
answer would hide the rest of a roster that still works.
"""


def providers(catalog):
    """`catalog`'s provider rows, with the three-layer tables folded into them."""
    rows = [row for row in catalog.get("provider") or [] if isinstance(row, dict)]
    facts = {
        row["id"]: row
        for row in catalog.get("model") or []
        if isinstance(row, dict) and row.get("id")
    }
    routes = []
    for route in catalog.get("route") or []:
        if not isinstance(route, dict) or not route.get("id"):
            continue
        row = dict(route)
        row["model"] = list(row.get("model") or [])
        routes.append(row)
    for serving in catalog.get("serving") or []:
        if not isinstance(serving, dict):
            continue
        route = next((r for r in routes if r["id"] == serving.get("route")), None)
        shared = facts.get(serving.get("model"))
        if route is None or shared is None:
            continue
        route["model"].append(served(shared, serving))
    return rows + routes


def served(facts, serving):
    """One served row: the shared facts, then whatever the serving states."""
    row = dict(facts)
    for key, value in serving.items():
        if key in ("route", "model"):
            continue
        row["id" if key in ("wire_id", "id") else key] = value
    if "max_output_tokens" not in row and "max_output" in row:
        row["max_output_tokens"] = row["max_output"]
    return row
