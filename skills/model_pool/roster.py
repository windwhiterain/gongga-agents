"""The machine's model roster: which provider serves which model, and which tier
each model belongs to.

Both roots are read the way the server reads them: the community catalog is the
base, the private one overrides field by field, and the private file is never
required. `GONGGA_PROVIDERS_CATALOG` names one catalog instead of the pair.
"""

import os
import re
import tomllib

AGENTS = os.path.join(os.path.expanduser("~"), ".agents")
COMMUNITY_CATALOG = os.path.join(AGENTS, "gongga-agents", "providers.toml")
PRIVATE_CATALOG = os.path.join(AGENTS, "providers.toml")
CATALOG_ENV = "GONGGA_PROVIDERS_CATALOG"

# The roster's own script lookup: the private scripts directory shadows the
# community one, exactly as the server orders them.
PRIVATE_SCRIPTS = AGENTS


class RosterError(Exception):
    """A catalog that cannot be read at all."""


def read_catalog(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def merge(base, overlay):
    """Community fields kept, private fields winning, rows carrying an `id` merged by it."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = merge(base[key], value) if key in base else value
        return merged
    if isinstance(base, list) and isinstance(overlay, list) and keyed_rows(base) and keyed_rows(overlay):
        return merge_rows(base, overlay)
    return overlay


def keyed_rows(items):
    return all(isinstance(item, dict) and "id" in item for item in items)


def merge_rows(base, overlay):
    """Overlay rows replace the base row of the same id, field by field; new rows append."""
    merged = [dict(item) for item in base]
    position = {item["id"]: index for index, item in enumerate(merged)}
    for item in overlay:
        index = position.get(item["id"])
        if index is None:
            position[item["id"]] = len(merged)
            merged.append(dict(item))
        else:
            merged[index] = merge(merged[index], item)
    return merged


def catalog_paths():
    explicit = os.environ.get(CATALOG_ENV)
    if explicit:
        return [explicit]
    return [COMMUNITY_CATALOG, PRIVATE_CATALOG]


class Roster:
    """The merged catalog, plus the lookups a draw needs."""

    def __init__(self, catalog, path):
        self.catalog = catalog
        self.path = path
        self.providers = [provider for provider in catalog.get("provider") or [] if provider.get("id")]
        self.by_id = {provider["id"]: provider for provider in self.providers}
        self.retired = {
            model_id
            for provider in self.providers
            for model in provider.get("model") or []
            for model_id in model.get("retired_ids") or []
        }

    def credential(self, provider):
        """The provider's API credential: the environment first, then the private catalog."""
        name = provider.get("key_env")
        from_environment = os.environ.get(name) if name else None
        return from_environment or provider.get("key") or None

    def script_path(self, script):
        """The quota script, the private root shadowing the community one."""
        if not script:
            return None
        roots = [os.path.dirname(self.path)]
        if not os.environ.get(CATALOG_ENV):
            roots = [PRIVATE_SCRIPTS, os.path.dirname(COMMUNITY_CATALOG)] + roots
        for root in roots:
            candidate = os.path.join(root, script)
            if os.path.isfile(candidate):
                return candidate
        return None

    def quota_target(self, provider):
        """The first quota query the roster gives a script for.

        A provider can carry several queries (a subscription plan and a
        pay-as-you-go wallet); only the script-bearing one says anything about
        the allowance a model request spends.
        """
        for query in provider.get("quota_query") or []:
            if query.get("script"):
                return query
        return None

    def quota_credential(self, provider, query):
        """The credential the quota script itself takes.

        `script_env` is the variable that script reads, which is not always the
        provider's `key_env` (a console cookie, a second account). A missing
        script-specific variable is not replaced by the API key: the script
        would only refuse it.
        """
        name = query.get("script_env")
        if name:
            from_environment = os.environ.get(name)
            if from_environment:
                return from_environment
            if name == provider.get("key_env"):
                return provider.get("key") or None
            return None
        return self.credential(provider)

    def tier_of(self, provider_id, model_id, tiers):
        """One model's tier: the explicit override, then the first rule that matches.

        A rule word matches a *token* of the id, not a substring: `mini` must not
        be read into the brand `MiniMax`. A word that carries a separator of its
        own is a phrase, and is matched against the whole id instead.
        """
        explicit = (tiers.get("model") or {}).get(f"{provider_id}/{model_id}")
        if explicit is None:
            explicit = (tiers.get("model") or {}).get(model_id)
        if explicit:
            return explicit
        tokens = set(tokenize(model_id))
        lowered = model_id.lower()
        for rule in tiers.get("rule") or []:
            for word in rule.get("any") or []:
                word = word.lower()
                if matches(word, tokens, lowered):
                    return rule["tier"]
        return tiers.get("default") or "sota"

    def routes(self, tiers, tier=None, provider_id=None):
        """Every live model, as a `(provider, model, tier)` row.

        An id another entry retired is not live, and a provider that serves no
        models (a proxy, an index) contributes nothing.
        """
        for provider in self.providers:
            if provider_id and provider["id"] != provider_id:
                continue
            for model in provider.get("model") or []:
                model_id = model.get("id")
                if not model_id or model_id in self.retired:
                    continue
                model_tier = self.tier_of(provider["id"], model_id, tiers)
                if tier and model_tier != tier:
                    continue
                yield provider, model, model_tier


def tokenize(model_id):
    """The words an id is spelled with: `MiniMax-M3.1-Flash` is minimax, m3, 1, flash."""
    return [token for token in re.split(r"[^0-9a-z]+", model_id.lower()) if token]


def matches(word, tokens, lowered):
    """Whether one rule word names this id: a token, or a phrase inside it."""
    if re.search(r"[^0-9a-z]", word):
        return word in lowered
    return word in tokens


def load():
    """The merged roster for this machine, or `None` when no catalog exists."""
    merged = {}
    used = None
    for path in catalog_paths():
        if not os.path.isfile(path):
            continue
        try:
            catalog = read_catalog(path)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise RosterError(f"{path}: {error}") from error
        merged = merge(merged, catalog) if merged else catalog
        used = used or path
    if not merged:
        return None
    return Roster(merged, used)
