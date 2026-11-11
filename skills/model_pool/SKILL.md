---
name: model_pool
description: Pick the model a spawned agent should run on — one `<provider>/<model id>` string that `gongga-cli set_session --model` takes verbatim, or, when the spawn takes a whole config, SPEC's `ModelConfig` for that model; drawn inside the tier the work calls for (flash / pro / sota, named on every call), and only from models this machine can actually run right now: still in the roster, credentialed, with allowance above its floor, entitled on this account, and not marked out. Use it when a subagent has no model to name, when a spawn hit a quota wall and needs another, or when you are placing the work itself: flash for search / explore / investigate / implement, pro for hard implement and review, sota for plan and core review. Triggers - 挑模型 / 派子 agent 用哪个模型 / 有额度的模型 / model pool / flash pro sota 概率.
metadata:
  entry: model_pool
---

# Draw a model, hand it to a spawn

One command, one line, and that line is already the argument:

```bash
# This skill's own directory: the parent of the `SKILL.md: <path>` that `@skill:`
# expands to (the `path` column of `skill`'s table names the same file).
# In gongga, `gongga-cli skill <name> ...` runs the entry the frontmatter declares.
SKILL_DIR=<the directory this SKILL.md is in>
S="$SKILL_DIR/model_pool"
$S pick --tier flash --provider deepseek-official   # -> deepseek-official/deepseek-flash
$S pick_config --tier flash --provider deepseek-official   # -> the ModelConfig below
```

```bash
gongga-cli set_session --name scout --model "$($S pick --provider deepseek-official --tier flash)"
gongga-cli send_message --name scout --message "read the failing test and report"
```

The server resolves `<provider>/<model id>` exactly as this prints it, so the
value needs no trimming, quoting or mapping. Everything that is *not* the model
goes to stderr: warnings, `--explain`, nothing to strip on the way in.

## The same draw, as the config a session is created with

A spawn that takes one model string wants `pick`. A spawn that takes a whole
session config -- SPEC's `SessionConfig.model_config` -- wants `pick_config`: the
same draw, the same tier, the same gates, printed as SPEC's `ModelConfig` instead
of one line.

```bash
$S pick_config --tier flash
# {"model":{"provider":"deepseek-official","model":"deepseek-flash"},
#  "thinking":null,"ctx_limit":null,"cache_tier":null}
```

`pick` and `pick_config` are one lottery told two ways, so picking a string here
and a config there cannot hand out two different models. What differs is only
what stdout carries: one `<provider>/<model id>` for `pick`, one JSON document
for `pick_config` -- still exactly one thing to parse, with warnings and
`--explain` on stderr as before. `--json` is `pick --json`'s decision, with the
config under `model_config`.

The three fields SPEC defaults are `null`, which is SPEC's `provider_default()`:
this skill decides *which model*, and how that model is asked -- thinking effort,
context budget, cache tier -- is the provider's own answer for the model it
serves. SPEC re-defaults all three on a model change, so a caller that wants one
named sets it on the object it hands on; a value invented here would be this tool
answering a question it cannot.

## What a draw decides

Three decisions, and only the last two are the engine's.

1. **Only enabled models are candidates.** The multiplier table in
   `preference.toml` is a whitelist: a model it does not name has a multiplier of
   zero and is not drawn. The tier you name narrows the candidates to that tier; it
   does not lift the whitelist. The one exception is a tier the file itself gives no
   weight — `test`, whose models are deliberately unencouraged.
2. **The tier is yours: `--tier flash|pro|sota|test`, on every call.** Which tier a
   piece of work belongs to is a judgement about the work, and the caller is the only
   one holding it — the roster says what a model *is*, never what you are *doing*.
   So no call of this skill leaves it out. A `pick` without `--tier` is not a way to
   choose work: it is a lottery over `[tier]` in `preference.toml`
   (`0.60 / 0.35 / 0.05 / 0.00` on a stock checkout), which is exactly how a
   read-three-files errand ends up buying `pro`. Those weights survive for a hand-run
   `pick` used to feel a machine out; nothing that spawns work may lean on them.
3. **The model inside the tier you named**, by `allowance × multiplier`: the worst
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
| Nothing could be reported on | no reading, a timeout, or a provider the roster publishes no quota for — **no allowance known counts as none** |
| Its multiplier is zero | `preference.toml`, next to this skill |

The credential row is why a draw never hands a spawn a model the server would
refuse: the server resolves `--model` against the same roster, and a provider
with no key there fails the very `set_session` this tool feeds.

**A quota nobody could read is zero, not unknown.** A timeout, a provider the
roster publishes no quota for, a script whose own variable is missing: all of
them leave a route with no allowance to draw on, so it is not drawn. Treating a
missing number as an open door is how an unpurchased provider got handed out
with `-` in the share column beside it.

**A number is only kept across silence, never across an answer.** A reader that
timed out or never started says nothing about the allowance, so the reading that
was true a minute ago still stands. A reader that ran and said *no* — the plan is
gone, the key is not a plan key — has answered, and the old number is dropped:
keeping it is how a provider whose subscription lapsed went on drawing on the
percentage it had before it lapsed.

When *nothing* is usable the draw may lift **a mark** — this machine's own
temporary judgement — and says `model_pool: nothing is usable, drawn anyway` on
stderr. It never lifts a credential, an allowance or a refusal: with none of
those left it prints nothing on stdout and exits non-zero saying why.

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
$S pick --tier flash --probe   # refresh the verdicts on the way to a draw
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
| `model_pool pick --tier TIER [--provider ID] [--json] [--explain] [--seed N]` | one draw inside that tier; `--json` for the decision, `--explain` for every candidate on stderr |
| `model_pool pick_config --tier TIER [--provider ID] [--json] [--explain] [--seed N]` | the same draw as SPEC's `ModelConfig`, one JSON object on stdout; `--json` for the decision, with the config under `model_config` |
| `model_pool status [--provider ID]` | every route: tier, usable or not, share, and the provider's own detail line |
| `model_pool tiers` | the three tiers, their weights, and the models inside each |
| `model_pool probe [--force] [--provider ID]` | ask each provider whether this account may run the model |
| `model_pool mark <provider/model\|provider> [--minutes N] [--reason TEXT] [--clear]` | keep a route out of the draw, or release it |

Flags on `pick`, `pick_config` and `status`: `--refresh S` (how long a cached reading
answers, `0` asks again), `--cached` (never run a quota script), `--timeout S`,
`--probe` (also refresh the entitlement verdicts).

## When a spawn hits the wall

A draw is a prediction, and a prediction can be wrong: entitlement expires
between two probes, and a plan's window can empty mid-run. So the loop is draw,
spawn, and — if the child dies — mark what killed it and draw again. The failure
line in the child's transcript names the route.

```bash
$S mark kimi-coding/k3.1-pro --minutes 30 --reason "quota exhausted mid-run"
$S pick --tier sota --provider kimi-coding   # same tier, another route of it
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
unscored_share = 0.5     # how a route with a wallet reading competes in its tier
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

| 档 | 派什么活 | 为什么是这一档 |
|---|---|---|
| `flash` | **search、explore、investigate、implement** | 有工具、有文件、答案在眼前：查文档、读代码、跑测试、把改动落地。量大，所以必须便宜 |
| `pro` | **hard implement、review** | 要判断的活：实现本身不难但坑深，或者要挑别人代码的错。读得懂 ≠ 看得出问题 |
| `sota` | **plan、core review** | 一次错就全错的地方：动手前定方案，以及最核心那部分的复核。一次性，不跑循环 |
| `test` | 只用来测 harness 本身 | 验证接口通不通、看服务端起不起来；基本不可用于编程 |

派活先认活，再抽模型：**查资料、读代码、改代码都是 flash**——不是"便宜所以丢给它"，
而是那几种活的上限就在那儿，贵的模型换不来更多。只有"要判断"（review、疑难实现）才上 pro，"错了要重来"（plan、核心 review）才上 sota。反过来用是纯浪费：让 sota 去 fetch 几个网页、
让 flash 去定架构，两次都白花。

四个档**只在这里读，不在这里猜**。改档 = 改 roster 里那一行，不用重新编译、不用改脚本。
没有 `tier` 的模型不会被抽，`tiers` 会把它们列成"roster 没写档"——那是缺口，不是默认值。

这是**价格档，不是能力档**：这台机器上 V4.1-Flash 的智能指数（45）比 V4-Pro（36）还高，
所以 `--tier flash` 也可能抽到最聪明的那个；`--tier sota` 买的是贵的前沿，不是"最划算的答案"。
`[provider.model.params]` 里的规模（总/激活参数，十亿）只是参考：1.6T/49B 不比 552B/16B
更省钱地想事情——激活参数、价格、厂商的后训练是三个不同轴。

## 倍率：这台机器鼓励用哪些

`preference.toml` 就在这个 skill 目录里，两个表：

```toml
[tier]           # 引擎兜底：只有不点 --tier 的 pick 才读它，本 skill 的调用一律点档
flash = 0.60
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
"qwen-dashscope/qwen3.8-flash" = 1.0
"tencent-tokenhub/hy4-preview"     = 1.0
"zhipu/glm-5.3"                    = 1.0
"bytedance-ark/doubao-seed-2-1-pro-260915" = 1.0
```

**倍率里没有的模型默认为 0，也就是不抽**——这张表就是白名单。`2.0` 是"能用就用它"，
`0.5` 是"没得选才用"。抽取时：先把候选收窄到点的那一档，再在档内按
**剩余额度 × 倍率** 抽模型。

**档是呼叫方给的，`pool.py` 不替你猜。** 不点档时它按 `[tier]` 的权重自己抽一档
（`0.60 / 0.35 / 0.05 / 0.00`），抽到哪一档与活无关——"读三个文件的侦察买到 pro"就是
这条路径。`--tier` 也不会绕过白名单；例外只有权重为 0 的 `test`：点名 `--tier test` 才抽得到它。
`tiers` 会把被关掉的模型标成 `off`，"为什么它从来不出现"一眼可答。

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
  console cookie) comes from the environment when this machine has it. A script
  whose credential is *not* the API key — a console session, which is the only
  thing that can read MiniMax's credits — looks its own entry up in the same
  keystore (`minimax-console`) and says so by name when it is missing.
- **The allowance** — each provider's `quota_query` scripts, in file order, run
  once per `refresh`: the same scripts the roster publishes, read through the
  same contract. File order is preference order and the first reader that answers
  is the one on record — the server reads the same table by the same rule
  (`crates/server/src/quota.rs`), so one provider can carry several products: an
  M Plan's windows, then, when that plan is over, the credits the same key still
  spends (`unit = "credit-left"`), then a pay-as-you-go wallet. A reader that
  answers *no* falls through to the next; a reader that answers nothing only
  reports why. Readings are cached in `~/.model_pool/state.json` with their unit
  and their marks, which is also where `mark` writes (`MODEL_POOL_STATE` points
  elsewhere). A failed query is cached too, so a provider with no credential is
  asked once per `refresh` instead of once per draw.
- **Nothing else.** No network call of its own, no model turn, no API key of its
  own: a provider whose script needs a variable this machine does not have
  reports nothing, which counts as no allowance, so the route is out and
  `status` names the missing variable.

## Read the answer before trusting it

`status` is the check that the draw is drawing from what you think:

```bash
$S status --provider minimax-cn
flash minimax-cn/MiniMax-M3.1-Flash  usable  0.85  85 left (3s old)
      general current 100%, general weekly 85%
pro   minimax-cn/MiniMax-M3          usable  0.85  85 left (3s old)
```

`share` is the fraction of its own tightest window; `-` means the reading states
no window to score — a wallet balance, or nothing at all, and the two are told
apart by `usable`: a wallet is drawn on a nominal share, a provider that answered
nothing is `out`. `out` is a route the draw will not take — the `why` column
always names which of the reasons above it was. The second line is the script's
own `detail`, so a floor that looks wrong can be traced to the provider's number
rather than to this tool.
