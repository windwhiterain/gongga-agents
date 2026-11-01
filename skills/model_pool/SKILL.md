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

1. **Only enabled models are candidates.** The multiplier table in
   `preference.toml` is a whitelist: a model it does not name has a multiplier of
   zero and is not drawn. An explicit `--tier` is the one exception.
2. **The tier** — `flash` / `pro` / `sota` / `test` — by the weights in
   `[tier]` (default `0.60 / 0.35 / 0.05 / 0.00`). A tier with nothing runnable
   drops out, and its weight is spread over the tiers that are left: the draw is
   always *conditional on what exists*, so a machine that only serves flash and
   pro still splits `0.63 / 0.37`.
3. **The model inside that tier**, by `allowance × multiplier`: the worst
   `remaining / capacity` window a provider reports, times how much the
   preference file encourages that model. A provider that reports a bare amount
   and no ceiling (a wallet) carries a nominal share, so it competes instead of
   being shut out; a tier nobody scores falls back to roster order.

The tier of a model is a line in the roster (`tier = "..."`), never a rule in
this script: the judgement lives where a person can edit it.

A model is **not** drawable when any of these holds — each one is a decision the
provider or you already made, not a guess:

| Out because | Where it comes from |
|---|---|
| The roster retired the id | another entry's `retired_ids` |
| The provider says the allowance is spent, or at the floor | the quota script's `remaining` / `error` |
| You marked the provider or the route | `model_pool mark` |
| This machine holds no credential for it | no entry in `~/.agents/keys.toml`, and `key_env` in no environment variable |
| The provider denies this model for this account | one cheap probe: 401 / 402 / 403 / 404, or a body that says access was denied |
| `fail_open` is off and nothing could be reported | `~/.agents/model_pool.toml` |
| Its multiplier is zero | `preference.toml`, next to this skill |

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

Two files, two jobs.

**`preference.toml`** (beside this script) is the judgement: tier weights and
the whitelist of models worth drawing. Edit it and the next `pick` reads it —
`--preference` or `MODEL_POOL_PREFERENCE` points at another one. See
"倍率：这台机器鼓励用哪些" below for the shape.

**`~/.agents/model_pool.toml`** is the plumbing — how the allowance is read and
what counts as usable (`--config`, `MODEL_POOL_CONFIG` to point elsewhere). Its
defaults live in `pool.py`:

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
```

Nothing here decides which tier a model is in: that is `tier = "..."` in the
roster, below.

## 档位是 roster 里写着的，不是这里算的

每个模型在自己的 `[[provider.model]]` 里带一行 `tier`：

```toml
  [[provider.model]]
  id = "deepseek-flash"
  name = "DeepSeek V4.1-Flash-1017"
  tier = "flash"
```

| 档 | 意思 | 派什么活 |
|---|---|---|
| `flash` | 便宜：够用、快、按量花不了几个钱 | 读文件、跑测试、搬代码 |
| `pro` | 中间：贵一截，明显更能想 | 设计、疑难定位、跨文件的改 |
| `sota` | 前沿：最贵的那批 | 一次性的硬活，别拿它跑循环 |
| `test` | 基本不可用于编程，只用来测 harness 本身 | 验证接口通不通、看服务端起不起来 |

四个档**只在这里读，不在这里猜**。改档 = 改 roster 里那一行，不用重新编译、不用改脚本。
没有 `tier` 的模型不会被抽，`tiers` 会把它们列成"roster 没写档"——那是缺口，不是默认值。

这是**价格档，不是能力档**：这台机器上 V4.1-Flash 的智能指数（45）比 V4-Pro（36）还高，
所以 `--tier flash` 也可能抽到最聪明的那个；`--tier sota` 买的是贵的前沿，不是"最划算的答案"。
`[provider.model.params]` 里的规模（总/激活参数，十亿）只是参考：1.6T/49B 不比 552B/16B
更省钱地想事情——激活参数、价格、厂商的后训练是三个不同轴。

## 倍率：这台机器鼓励用哪些

`preference.toml` 就在这个 skill 目录里，两个表：

```toml
[tier]
flash = 0.60     # 先抽档
pro   = 0.35
sota  = 0.05
test  = 0.00     # 默认 0：只在你点名 --tier test 时才抽得到

[model]
"deepseek-official/deepseek-flash" = 1.0    # 正常候选
"minimax-cn/MiniMax-M3.1-Flash"    = 1.0
"kimi-coding/k3.1-pro"             = 1.0
"qwen-token-plan/qwen3.8-max"      = 1.0
"qwen-dashscope/qwen3.8-max"       = 1.0
"qwen-token-plan/qwen3.8-flash"    = 1.0
"qwen-dashscope/cmaas-qwen3.8-flash" = 1.0
"tencent-tokenhub/hy4-preview"     = 1.0
```

**倍率里没有的模型默认为 0，也就是不抽**——这张表就是白名单。`2.0` 是"能用就用它"，
`0.5` 是"没得选才用"。抽取时：先按 `[tier]` 的权重抽档，再在档内按
**剩余额度 × 倍率** 抽模型。

唯一例外：显式 `--tier test`（或任何 `--tier`）会绕过"倍率为 0 就不抽"这条，因为你已经
点名要那一档了。`tiers` 会把被关掉的模型标成 `off`，"为什么它从来不出现"一眼可答。

键可以写 `<provider>/<model>`，也可以只写 model id（对所有提供它的 provider 生效）。
改这个文件不用重启任何东西：下一次 `pick` 就读到；`--preference` 或
`MODEL_POOL_PREFERENCE` 可以指到别处。

## What it reads

- **The roster** — `~/.agents/gongga-agents/providers.toml`, the one file the
  server reads too (`GONGGA_PROVIDERS_CATALOG` names another). On this machine that
  checkout is on its `local` branch, where this machine's own providers live;
  upstream is `main`, and a `rebase` brings an upstream change down (`LOCAL.md` in
  that repository). Which models exist, **which tier each is in**, which ids are
  retired, which provider serves each, the price, the parameter count.
- **The key** — `~/.agents/keys.toml`, `[keys]` mapping a provider id to its key.
  Git does not track it, and it is read the way the server reads it: the keystore
  first, then the variable `key_env` names, and a `key` written in the roster is
  ignored (a roster is a checkout, so a key in one is a key in git). A provider
  with neither is not drawn — the gate is the server's own.
- **The credential a quota script takes** — the same one, which is what the
  server hands the script; a variable the script reads itself (`script_env`, a
  console cookie) comes from the environment when this machine has it.
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
