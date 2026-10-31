---
name: model_pool
description: Pick the model a spawned agent should run on — one `<provider>/<model id>` string that `gongga-cli set_session --model` takes verbatim, drawn across three tiers (flash / pro / sota) by the weights you set, and only from models this machine can actually run right now: still in the roster, credentialed, with allowance above its floor, entitled on this account, and not marked out. Use it when a subagent has no model to name, when a spawn hit a quota wall and needs another, or when you want cheap work on flash and the hard part on pro/sota. Triggers - 挑模型 / 派子 agent 用哪个模型 / 有额度的模型 / model pool / flash pro sota 概率.
---

# Draw a model, hand it to a spawn

One command, one line, and that line is already the argument:

```bash
S=~/.agents/gongga-agents/skills/model_pool/model_pool
$S pick --provider deepseek-official      # -> deepseek-official/deepseek-flash
```

```bash
gongga-cli set_session --session scout --model "$($S pick --provider deepseek-official --tier flash)"
gongga-cli send_message --session scout --message "read the failing test and report"
```

The server resolves `<provider>/<model id>` exactly as this prints it, so the
value needs no trimming, quoting or mapping. Everything that is *not* the model
goes to stderr: warnings, `--explain`, nothing to strip on the way in.

## What a draw decides

Two decisions, in this order.

1. **The tier** — `flash`, `pro`, `sota` — by weight (default `0.60 / 0.35 / 0.05`).
   A tier with nothing runnable drops out, and its weight is spread over the
   tiers that are left: the draw is always *conditional on what exists*, so a
   provider that only serves flash and pro still splits `0.63 / 0.37`.
2. **The model inside that tier**, in proportion to the allowance it has left:
   the worst `remaining / capacity` window a provider reports. A provider that
   reports a bare amount and no ceiling (a wallet) carries a nominal share, so it
   competes instead of being shut out; a tier nobody scores falls back to roster
   order.

A model is **not** drawable when any of these holds — each one is a decision the
provider or you already made, not a guess:

| Out because | Where it comes from |
|---|---|
| The roster retired the id | another entry's `retired_ids` |
| The provider says the allowance is spent, or at the floor | the quota script's `remaining` / `error` |
| You marked the provider or the route | `model_pool mark` |
| The roster names no credential this machine has | `key_env` in no environment variable and no private `key` |
| The provider denies this model for this account | one cheap probe: 401 / 402 / 403 / 404, or a body that says access was denied |
| `fail_open` is off and nothing could be reported | `~/.agents/model_pool.toml` |

The credential row is why a draw never hands a spawn a model the server would
refuse: the server resolves `--model` against the same roster, and a provider
with no key there fails the very `set_session` this tool feeds.

Anything else the scripts could not report on — a timeout, a provider the roster
publishes no quota for, a script whose own variable is missing — is still
drawable and simply unscored (`fail_open`, on by default). Nothing reported is
not a refusal, and the "no credential" case is already caught above.

When *nothing* is usable the draw answers anyway, because a spawn has to run
somewhere, and says `model_pool: nothing is usable, drawn anyway` on stderr. That
line is the signal to hand the child a small task or to wait, not to trust the
route.

## The check the roster cannot do

The roster knows what a provider *sells*. Only the provider knows what this
account *bought* — and the server does not check either: it resolves
`<provider>/<model>` against the roster and its key, so a route this account
cannot use is accepted, spawned, and then dies on `403 AccessDenied.Unpurchased`
or `402 Payment Required`. Measured on this machine: five Qwen routes and both
LongCat routes answered exactly that, with allowance to spare -- the reason the
draw asks before it draws instead of after a spawn dies.

`probe` asks, once, with the cheapest request a model answers — one word in, one
token out:

```bash
$S probe                 # every route, verdict cached for probe_refresh (a day)
$S probe --provider kimi-coding --force
$S pick --probe          # refresh the verdicts on the way to a draw
```

Three verdicts, and only one of them excludes:

| Verdict | Means | Effect |
|---|---|---|
| `allowed` | the provider answered — 200, a 400 about a parameter, a 429 | drawable |
| `refused` | 401, 402, 403, 404, or a body that says access was denied | **out** for `probe_refresh` |
| `unknown` | the probe did not reach the provider, or the provider failed with a 5xx | no effect: a network blip is not a refusal |

`pick` never probes on its own — it is on the spawn path and has to stay fast —
so a fresh machine draws from allowances it has not verified. Run `probe` once
after adding keys, and again with `--force` after buying something.

## The commands

| Command | What it answers |
|---|---|
| `model_pool` | `pick`, the default: print one model |
| `model_pool pick [--provider ID] [--tier TIER] [--json] [--explain] [--seed N]` | one draw; `--json` for the decision, `--explain` for every candidate on stderr |
| `model_pool status [--provider ID]` | every route: tier, usable or not, share, and the provider's own detail line |
| `model_pool tiers` | the three tiers, their weights, and the models inside each |
| `model_pool probe [--force] [--provider ID]` | ask each provider whether this account may run the model |
| `model_pool mark <provider/model\|provider> [--minutes N] [--reason TEXT] [--clear]` | keep a route out of the draw, or release it |

Flags on `pick` and `status`: `--refresh S` (how long a cached reading answers,
`0` asks again), `--cached` (never run a quota script), `--timeout S`,
`--probe` (also refresh the entitlement verdicts), `--weights
flash=0.5,pro=0.45,sota=0.05`.

## When a spawn hits the wall

A draw is a prediction, and a prediction can be wrong: entitlement expires
between two probes, and a plan's window can empty mid-run. So the loop is draw,
spawn, and — if the child dies — mark what killed it and draw again. The failure
line in the child's transcript names the route.

```bash
$S mark kimi-coding/k3.1-pro --minutes 30 --reason "quota exhausted mid-run"
$S pick --provider kimi-coding          # now draws another kimi route
```

A mark is per machine, not per session, and lapses on its own after
`minutes`. `mark <provider>` takes a whole provider out at once — what a wallet
running dry means.

## Make it this machine's

The defaults live in `pool.py`; a private file overrides them without touching
the repository, at `~/.agents/model_pool.toml` (`--config`, `MODEL_POOL_CONFIG`
to point elsewhere):

```toml
[pool]
refresh = 300            # seconds a cached reading answers for
timeout = 8              # seconds one quota script may take
fail_open = true         # a provider that reported nothing is still drawable
unscored_share = 0.5     # how an unscored route competes inside its tier
cooldown = 1800          # default mark length, seconds
exclude = []             # provider ids never drawn, e.g. ["local-qwen3"]
probe_refresh = 86400    # seconds an entitlement verdict answers for
probe_timeout = 20       # seconds one probe may take
minimum = { percent = 5, money = 1, other = 0 }

[[tier]]
id = "flash"
weight = 0.60
[[tier]]
id = "pro"
weight = 0.35
[[tier]]
id = "sota"
weight = 0.05

# Which id belongs in which tier: first rule that matches a token of the id.
[tiers]
default = "pro"
[[tiers.rule]]
tier = "flash"
any = ["flash", "mini", "lite", "highspeed", "ultraspeed", "turbo", "bonsai"]
[[tiers.rule]]
tier = "sota"
any = ["preview", "prev", "max", "sota", "ultra", "opus"]
[[tiers.rule]]
tier = "pro"
any = ["pro"]

# A model the rules place wrong, by `<provider>/<id>` or bare id.
[tiers.model]
"minimax-cn/MiniMax-M3" = "sota"
```

A rule word matches a **token**, never a substring, so `mini` is not read into
the brand `MiniMax`; a word that carries a hyphen or a dot is a phrase and is
matched against the whole id.

## What it reads

- **The roster** — `~/.agents/providers.toml` and
  `~/.agents/gongga-agents/providers.toml`, merged the way the server merges them
  (`GONGGA_PROVIDERS_CATALOG` names one file instead). Which models exist, which
  ids are retired, which provider serves each, and the credential a quota script
  takes.
- **The allowance** — each provider's `quota_query.script`, run once per
  `refresh`: the same scripts the roster publishes, read through the same
  contract. Readings are cached in `~/.model_pool/state.json` with their marks,
  which is also where `mark` writes (`MODEL_POOL_STATE` points elsewhere). A
  query that fails is cached too, so a provider with no credential is asked once
  per `refresh` instead of once per draw.
- **Nothing else.** No network call of its own, no model turn, no API key of its
  own: a provider whose script needs a variable this machine does not have is
  drawn unscored and says so in `status`.

## Read the answer before trusting it

`status` is the check that the draw is drawing from what you think:

```bash
$S status --provider minimax-cn
flash minimax-cn/MiniMax-M3.1-Flash  usable  0.85  85 left (3s old)
      general current 100%, general weekly 85%
pro   minimax-cn/MiniMax-M3          usable  0.85  85 left (3s old)
```

`share` is the fraction of its own tightest window; `-` means unscored, which is
a wallet or a provider that answered nothing, and `out` is a route the draw will
not take — the `why` column always names which of the reasons above it was. The second line is the script's
own `detail`, so a floor that looks wrong can be traced to the provider's number
rather than to this tool.
