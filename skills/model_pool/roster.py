"""The machine's model roster: which provider serves which model, and which tier
each model belongs to.

Both roots are read the way the server reads them: the community catalog is the
base, the private one overrides field by field, and the private file is never
required. `GONGGA_PROVIDERS_CATALOG` names one catalog instead of the pair.
"""

import os
import tomllib

AGENTS = os.path.join(os.path.expanduser("~"), ".agents")
# One roster, from the community checkout. A machine's own entries are a branch of
# that checkout (`local`), not a second file -- so nothing here merges two lists.
CATALOG = os.path.join(AGENTS, "gongga-agents", "providers.toml")
CATALOG_ENV = "GONGGA_PROVIDERS_CATALOG"


class RosterError(Exception):
    """A catalog that cannot be read at all."""


def read_catalog(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def catalog_path():
    """The one roster file: an override, else the community checkout."""
    return os.environ.get(CATALOG_ENV) or CATALOG


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
        """The quota script, relative to the roster that named it."""
        if not script:
            return None
        candidate = os.path.join(os.path.dirname(self.path), script)
        return candidate if os.path.isfile(candidate) else None

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
    """The roster this machine reads, or `None` when there is no file."""
    path = catalog_path()
    if not os.path.isfile(path):
        return None
    try:
        catalog = read_catalog(path)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RosterError(f"{path}: {error}") from error
    return Roster(catalog, path)
