# gongga-agents

社区共建的「机器无关」内容仓库：一份 provider 目录，加上查额度用的脚本。
gongga 启动时把它 clone 到 `~/.agents/gongga-agents/`，以后按层读。

## 布局

与 `~/.agents/` 同构，所以同一条相对路径在两处含义相同：

```
gongga-agents/
  providers.toml   # provider 目录：端点、模型、上下文、模态、思考等级、价格、额度查询
  providers/       # 额度查询脚本（quota_query.script 引用）与分时价格计算（price.py）
```

`quota_query.script` 里的路径**相对 `providers.toml` 自己所在的目录** —— 社区条目的
`providers/deepseek.py` 指向这个仓库里的脚本，私有条目的同名路径指向
`~/.agents/providers/deepseek.py`。

## gongga 怎么找

一个 roster 文件：`~/.agents/gongga-agents/providers.toml`。服务端只读它。

- **目录不存在就 clone**：启动时异步 clone 一次；之后启动时和每小时各异步 `fetch` + 快进一次。
  断网只 warn，用本地已有副本继续，不影响 gongga 启动。
- **不再有第二层**：以前还有一份 `~/.agents/providers.toml` 作为私有覆盖、每次读按字段合并。
  那条路删掉了 —— 两份文件无法回答"现在这个价是哪个价"，而且本机的东西没有自己的历史。
- **本机的东西走分支**：这台机器自己的条目（内网 `base_url`、自建 provider）在
  clone 里的 `local` 分支上，`main` 是上游。上游更新是 `rebase`，不是合并。
  密钥不在这条分支上，见下面的「密钥政策」；提交该往哪条分支走，见「提交往哪去」。见 [`LOCAL.md`](LOCAL.md)。
- **脚本查找**：`quota_query.script` 是相对该文件的路径，所以只在自己的 clone 里找。
- 显式指定单文件（`GONGGA_PROVIDERS_CATALOG=/path/to/providers.toml`）时只用那一个。

## 密钥政策

**密钥不进任何 git 仓库** —— `main` 不写，`local` 也不写。服务端只从两个地方读，
**keystore 优先**：

1. `~/.agents/keys.toml` —— 一个 `[keys]` 表，provider id 到 key。`~/.agents` 不是仓库，
   所以这个文件永远不会被提交：

   ```toml
   [keys]
   deepseek-official = "sk-..."
   kimi-coding = "sk-..."
   ```

   改一行、保存即热更新：服务端把该文件和 roster 一起按 mtime 判断是否重读，下一次请求
   就用新 key，不用重启。
2. 环境变量，名字由 roster 里的 `key_env` 指明 —— 适合一次性的
   `DEEPSEEK_API_KEY=... gongga-cli ...`，或者还没写 keystore 的机器。

   ```bash
   setx DEEPSEEK_API_KEY "sk-..."   # Windows：写进用户环境变量
   export DEEPSEEK_API_KEY=sk-...   # macOS / Linux
   ```

顺序不能反：**keystore 赢过环境变量**。`setx` 写的是注册表，而 Windows 上进程的环境块只在
创建时从父进程拷贝一次。从 2026-10-26 就开着的 VS Code / pwsh 里启动 gongga，继承到的是写键
之前的旧环境；若环境变量优先，一次热更新就会被陈旧进程永久遮蔽，轮换密钥只能靠重启。文件是
服务端每次解析前都会重新看的东西，所以它说了算。

roster 里的 `key = "..."` 会被**忽略**：roster 是 git 检出，写进去的密钥就是 git 里的密钥，
留这条路径开着等于邀请那次泄露再来一次。

## 怎么贡献

1. 改 `providers.toml`：新字段能用就带上 `source` 与 `retrieved`（查证来源与日期）；官方没写的标 `unverified`。
   规模写进 `[provider.model.params]`：`total` / `active`（单位十亿参数），MOE 的激活参数按 token 计；
   厂商没公布就整块不写，别猜一个数。
2. 每个模型必须有 `tier`：`flash` / `pro` / `sota` / `test`。它是**人对这个模型值不值得
   派活的判断**（不是算出来的），`model_pool` 只读不猜；改档就是改这一行，不用重新编译。
   哪个模型被鼓励用（倍率、白名单）在 `skills/model_pool/preference.toml`。
3. 新的额度查询脚本放 `providers/`，并在对应 provider 的 `quota_query.script` 里用相对路径引用；
   `providers/probe.py` 可以离线跑全部解析器。
4. 价格分时段就写 `[[provider.model.price.band]]`：`days` 只能用 `workday` / `weekend` /
   `holiday` / `rest_day` / `makeup_workday` 这五个词（定义在 `providers.toml` 顶部），
   `hours` 写固定时段，价格写这个时段的数。**最宽的那个档放最后且不写 `days`**，它就是其余全部；
   写反了会让工作日夜间按高峰价计费。想知道某时刻实际多少钱：`python providers/price.py`。
5. 缓存写在两处：**保留时间**是 provider 级的事实（`cache_ttl` / `cache_ttl_note` /
   `cache_ttl_source`，写在 `[[provider]]` 下），**价格**照旧在 `[provider.model.price]`
   （`cache_read` / `cache_write` / `cache_write_5m` / `cache_storage`，按小时计的另配
   `cache_storage_unit`）。字段释义在 `providers.toml` 顶部。
   **保留时间要填两层**：`cache_ttl` 只放官方口径，官方没公布就写 `unverified —— 原因`；
   `cache_ttl_estimate` 放拿来规划用的那个数，宁可是猜的也要有，**并标出处**：
   `cache_ttl_estimate_basis` 用 `community`（第三方实测/横评）/ `inferred`（按官方机制推）/
   `guess`（纯猜），`cache_ttl_estimate_source` 写 URL，纯猜的写"来源是猜的"和猜法。
   价格同样：官方没给的写进 `note` 并标明"估计、未核实"，不要伪装成价目表里的一行。
6. 法定节假日日期写在 `providers/statutory.toml`（国务院每年一次通知），由 `price.py` 读；
   放假调休表从 chinese-days 按年缓存到 `~/.agents/cache/`，不在仓库里。
7. 提交前确认：`grep -nE '"(sk|ak|flm_ak|user)_' providers.toml` 无输出。

## 提交往哪去：公共的进 `main`，本机的留 `local`

一条判据：**换一台机器还成立吗？** 成立就是公共的，进 `main`；不成立（内网 host、本机脚本、
本机端口）就是本机的，只留在 `local`。

不变量只有一句：

> `local = main + 本机提交`，本机提交在最上面，`main` 是它的基。

服务端每小时那次同步（见「gongga 怎么找」）只维护这条不变量的后半截：`fetch`、快进 `main`、
在 `local` 是检出分支时把它 `rebase` 到 `main` 上。它**不搬提交，也不推**。所以"公共提交压在
`local` 上"这种状态它自己永远修不好 —— 只会每小时把那些提交原样重新应用到新的 `main` 上，
看起来一切正常，而它们始终没进上游。

提交信息跟着分支走：公共提交用区域前缀（`providers:` / `model_pool:` / `README:`），本机提交
一律 `local:` 开头。`git log --oneline main..local` 扫一眼就该看出哪几条不该出现在上游。

### 一、公共改动：在 `main` 上提交，当天推走

```bash
cd ~/.agents/gongga-agents
git fetch origin
git checkout main
git merge --ff-only origin/main
# 改、测、按「怎么贡献」自查
git commit -am "providers: ..."
git push origin main
git checkout local && git rebase main    # 本机提交跟着挪到新 main 上
```

最后一行不要省：`local` 是服务在读的那个检出，落在后面就是这台机器在旧基上跑。

`main` 上不要留没推的提交。服务端的快进是 `--ff-only`：只要上游再动一次，你那条只在本地、
还没推的 `main` 就再也快进不了（服务端只记一条 warn），`local` 于是被 rebase 到一个既旧、
又和上游分叉的基上。**不要合并 `main` 和 `origin/main`** —— `main` 只快进，合并会让"我的提交
在哪条分支上"重新变成一个要查的问题。

### 二、本机改动：在 `local` 上提交，不推

```bash
git checkout local
git rebase main                # 先确认基是新的，再动手
# 改
git commit -am "local: ..."
```

`local` 没有上游分支，`git push` 在这里本来就会失败 —— 这是有意的，别给它加 upstream。
本机的东西很少（一个内网 `base_url`、一个本机 provider），一条分支就够；再分出一条本机分支，
等于把"这台机器在读什么"重新变成要查的问题。

### 三、公共提交已经压在 `local` 上了：搬去 `main`

这是补救路径，也是这套流程里唯一重写历史的地方 —— 重写的只是 `local`，`main` 永远只快进。
先把哪几条是本机提交看清楚：

```bash
git log --oneline main..local
```

本机提交必须是**连续的一段**，而且在栈底。若不是（公共的和本机的交错），先用
`git rebase -i --onto main main local` 把 `local:` 那几行拖到最上面，再走下面：

```bash
cd ~/.agents/gongga-agents
git fetch origin
git checkout main && git merge --ff-only origin/main
git log --oneline main..local                  # 找出本机提交那一段的最后一条

git branch machine <最后一条本机提交的 sha>
git checkout -b pub local                      # 换一条分支做手术
git rebase --onto main machine                 # pub = main + 公共提交

git checkout main
git merge --ff-only pub
git push origin main

git checkout local
git reset --hard main
git cherry-pick machine                        # local = main + 本机提交
git branch -D pub machine
```

- 换到 `pub` 上做手术，是因为服务端的自动 rebase 只在 `local` 是检出分支时才动手：检出在别处，
  它就跳过，不会在你 `rebase` 到一半时插进来（它失败后会 `git rebase --abort`，那会把你做到
  一半的进度一起撤掉）。
- `git cherry-pick machine` 大概率撞在 `providers.toml` 的末尾：本机条目是文件最后一块，上游也
  在改同一个文件。冲突时**两边都留** —— 上游的新字段照抄，本机那一块仍然放在最后。
- 推之前再跑一遍密钥自查，`grep -nE '"(sk|ak|flm_ak|user)_' providers.toml` 必须无输出：
  搬出去的提交立刻就是公开的。

### 校验

```bash
git log --oneline local..main      # 应为空：local 永远包含 main
git log --oneline main..local      # 只应出现 local: 开头的提交
git rev-list --count main..local   # 本机提交数，不该随公共改动增长
git rev-parse main origin/main     # 推过之后两个 sha 相同
```

`git reflog` 能回到上面任何一步，所以这不是丢历史的操作；但也别对它用 `push --force` ——
`main` 只快进，`--all` / `--mirror` 不用。
