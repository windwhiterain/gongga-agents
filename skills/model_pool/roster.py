"""The machine's model roster: which provider serves which model, and which tier
each model belongs to.

Read the way the server reads it: one community checkout, its `local` branch
carrying this machine's own entries, and `GONGGA_PROVIDERS_CATALOG` naming some
other file instead. A credential is not in the roster -- it lives in the keystore
beside it, `~/.agents/keys.toml`, which git does not track.
"""

import os
import tomllib

# The two roots the server resolves, by the same names: `GONGGA_AGENTS_HOME` for
# `~/.agents`, `GONGGA_AGENTS_DIR` for the community checkout under it.
AGENTS_HOME_ENV = "GONGGA_AGENTS_HOME"
AGENTS_DIR_ENV = "GONGGA_AGENTS_DIR"
AGENTS = os.environ.get(AGENTS_HOME_ENV) or os.path.join(os.path.expanduser("~"), ".agents")
# One roster, from the community checkout. A machine's own entries are a branch of
# that checkout (`local`), not a second file -- so nothing here merges two lists.
CATALOG = os.path.join(
    os.environ.get(AGENTS_DIR_ENV) or os.path.join(AGENTS, "gongga-agents"),
    "providers.toml",
)
CATALOG_ENV = "GONGGA_PROVIDERS_CATALOG"
# The keystore: `[keys]` maps a provider id to its key. Never in git, edited in
# place, re-read whenever its mtime moves -- the same stamp the server watches.
KEYS = os.path.join(AGENTS, "keys.toml")


class RosterError(Exception):
    """A catalog that cannot be read at all."""


# The keystore's last read, keyed by path: `{path: (mtime, [keys])}`. Re-reading
# is what makes a rotation take effect on the next draw, so the stamp is the
# mtime, not "have I read this file yet".
_KEYSTORE = {}


def keystore(path=None):
    """`[keys]` from `~/.agents/keys.toml`: the provider id to its key.

    A file that is missing, unreadable or not TOML reads as no keys at all --
    never as an error: a machine with only environment variables is a machine
    that still draws.
    """
    path = path or KEYS
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        _KEYSTORE[path] = (None, {})
        return {}
    seen = _KEYSTORE.get(path)
    if seen and seen[0] == stamp:
        return seen[1]
    try:
        with open(path, "rb") as handle:
            table = tomllib.load(handle).get("keys") or {}
    except (OSError, tomllib.TOMLDecodeError):
        table = {}
    keys = {name: value for name, value in table.items() if isinstance(value, str) and value}
    _KEYSTORE[path] = (stamp, keys)
    return keys


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
        """The provider's API credential, from the one place a key may live.

        # Invariant

        Only two sources, and the keystore wins: `~/.agents/keys.toml`, keyed by
        provider id, then the variable the roster's `key_env` names. The keystore
        must beat the environment, not the other way round -- a variable is copied
        into a process once, at creation, so a stale one would shadow every edit to
        the file. The server orders them this way, and a draw that disagreed would
        hand a spawn a model the server then refuses, or hide one it would run.

        A `key` written in the roster is **ignored**, exactly as the server ignores
        it: the roster is a git checkout, and a key in one is a key in git.
        """
        kept = keystore().get(provider.get("id"))
        if kept:
            return kept
        name = provider.get("key_env")
        from_environment = os.environ.get(name) if name else None
        return from_environment or None

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
        """The credential the quota script takes.

        A variable the script reads itself (`script_env`) comes from the
        environment when it is there -- a console cookie or a second account is
        not an API key. Otherwise the script gets the same credential a request
        would use, which is what the server hands it.
        """
        name = query.get("script_env")
        if name:
            from_environment = os.environ.get(name)
            if from_environment:
                return from_environment
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
