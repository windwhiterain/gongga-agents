"""The machine's model roster: which provider serves which model, and which tier
each model belongs to.

Both roots are read the way the server reads them: the community catalog is the
base, the private one overrides field by field, and the private file is never
required. `GONGGA_PROVIDERS_CATALOG` names one catalog instead of the pair.
"""

import os
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

    def models(self, provider_id=None):
        """Every live model as a `(provider, model)` row.

        An id another entry retired is not live, and a provider that serves no
        models (a proxy, an index) contributes nothing.
        """
        for provider in self.providers:
            if provider_id and provider["id"] != provider_id:
                continue
            for model in provider.get("model") or []:
                model_id = model.get("id")
                if model_id and model_id not in self.retired:
                    yield provider, model

    def tier_of(self, model):
        """The tier the roster states for one model, or `None` when it states none.

        A model with no `tier` is not placed by this tool at all: the roster is
        the one place the judgement is written down (`providers.toml`, `model.tier`),
        so an unstated tier is a gap to report, never a guess to make.
        """
        tier = model.get("tier")
        return tier.strip().lower() if isinstance(tier, str) and tier.strip() else None


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
