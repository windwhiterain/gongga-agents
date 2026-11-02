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
  密钥不在这条分支上，见下面的「密钥政策」。
  这个 clone 的**检出永远是 `local`**：服务端读的是它的工作区，所以分支工作在
  `.worktrees/` 里做。提交该往哪条分支走、worktree 怎么用，见「提交往哪去」和 [`LOCAL.md`](LOCAL.md)。
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

不变量有两句，第二句讲的是**目录**，比第一句更容易踩坏：

> `local = main + 本机提交`，本机提交在最上面，`main` 是它的基。
> **主检出始终检出 `local`**；换分支、rebase、解冲突都在 `.worktrees/` 的 worktree 里做。

第二句不是洁癖。服务端读的是**主检出的工作区**，不是分支名：roster 就是
`~/.agents/gongga-agents/providers.toml` 这个路径上的那个文件，机器侧 skill 是
`~/.agents/skills/*` 指向 `gongga-agents/skills/` 的软链。所以主检出一旦切到 `main`、
detach 到某个中间提交、或者停在 rebase 的冲突态，这台机器当场就在用上游的 roster 跑：本机条目
（内网 `base_url`、`local-qwen3`）和本机的 skill 一起消失，**而且没有任何报错**，只是下几次请求
少几个模型。更糟的是整点同步也停了：服务端只在 `local` 是检出分支时才把它 rebase
（`crates/server/src/agents.rs` 的 `rebase_local`），检出在别处就整段跳过 —— 你以为它在跟进上游，
其实它一动不动。

提交信息跟着分支走：公共提交用区域前缀（`providers:` / `model_pool:` / `README:`），本机提交
一律 `local:` 开头。`git log --oneline main..local` 扫一眼就该看出哪几条不该出现在上游。

### worktree：建一次，之后都在里面干活

```bash
cd ~/.agents/gongga-agents
git worktree add .worktrees/work -B pub origin/main
cd .worktrees/work
```

`.worktrees/` 在 `.gitignore` 里 —— 它是工作区，不是仓库内容。worktree 里**不要**检出 `local`
（它已经在主检出上，git 也不允许），**也不要**检出 `main`：用 `pub` 这种一次性分支，`main`
才留得下"只快进"这条规矩，服务端每小时那次快进也不会撞上"branch is checked out"。

主检出里只剩两个会动文件的操作：一条 `git rebase main`（本机提交重新落到新 `main` 上），
和第三节末尾那条一次性的 `git reset --hard`。两者都还在 `local` 上，都不经过冲突态。

### 一、公共改动：在 worktree 的 `pub` 上提交，当天推走

```bash
cd ~/.agents/gongga-agents/.worktrees/work
git fetch origin && git checkout -B pub origin/main   # 每次开工先对齐上游
# 改、测、按「怎么贡献」自查
git commit -am "providers: ..."
git push origin pub:main                        # 推的是内容，不是 `pub` 这个名字

cd ~/.agents/gongga-agents                      # 回到主检出，它还该在 local 上
git fetch -q . refs/heads/pub:refs/heads/main   # 非快进会被这里拒绝，`main` 于是只快进
git rebase main                                 # 本机提交跟着挪到新 main 上
```

最后一行不要省：`local` 是服务端读的那个检出，落在后面就是这台机器在旧基上跑。它也是唯一
可能在主检出里撞上冲突的操作 —— 一旦撞了，立刻 `git rebase --abort`（工作区立刻回到干净的
`local`），然后按第三节在 worktree 里把结果算好，再用一条 `reset --hard` 落下来。

`main` 上不要留没推的提交。服务端的快进是 `--ff-only`：上游再动一次，你那条只在本地、还没推的
`main` 就再也快进不了（服务端只记一条 warn），`local` 于是被 rebase 到一个既旧、又和上游分叉的
基上。**不要合并 `main` 和 `origin/main`** —— `main` 只快进，合并会让"我的提交在哪条分支上"
重新变成一个要查的问题。

两句提醒都在这三行上：

- `git push origin pub:main` 被拒（`non-fast-forward`）只有一个原因：你开工之后上游又动了。
  在 worktree 里 `git fetch origin && git rebase origin/main`，再推一次；`pub` 是你在
  worktree 里的分支，重来不碰主检出。
- `git fetch . refs/heads/pub:refs/heads/main` 被拒，说明本地 `main` 上有一条别人的克隆里
  没有的提交 —— 那是这套流程不允许的状态（`main` 只快进）。先 `git log --oneline origin/main..main`
  看清它是什么，搬到 `pub` 上重新走一遍，别用 `--force`。

### 二、本机改动：在 `local` 上提交，不推

本机的东西就该落在主检出的工作区里（那正是服务端读的东西），所以这一条在主检出里直接改，
不需要 worktree：

```bash
cd ~/.agents/gongga-agents
git rebase main                # 先确认基是新的，再动手
# 改
git commit -am "local: ..."
```

`local` 没有上游分支，`git push` 在这里本来就会失败 —— 这是有意的，别给它加 upstream。
本机的东西很少（一个内网 `base_url`、一个本机 provider），一条分支就够；再分出一条本机分支，
等于把"这台机器在读什么"重新变成要查的问题。

### 三、公共提交已经压在 `local` 上了：在 worktree 里搬去 `main`

这是补救路径，也是这套流程里唯一重写历史的地方 —— 重写的只是 `local`，`main` 永远只快进，
而手术全程在主检出之外的 worktree 里做，主检出从头到尾没有离开 `local`。

先把哪几条是本机提交看清楚：

```bash
git log --oneline main..local
```

本机提交必须是**连续的一段**，而且在栈底。若不是（公共的和本机的交错），先把它们理顺：

```bash
cd ~/.agents/gongga-agents/.worktrees/work
git checkout -B work local && git rebase -i pub    # 把 `local:` 那几行拖到最上面
```

不能写成 `git rebase -i pub local` —— `local` 在主检出里检出了，git 会拒绝。理顺之后，下面
每一步里的 `local` 都换成 `work`，算出来的结果照样是新 `local` 该有的样子。

```bash
cd ~/.agents/gongga-agents/.worktrees/work
git fetch origin && git checkout -B pub origin/main
git log --oneline pub..local                   # 找出本机提交那一段的最后一条

git branch -f machine <那条本机提交的 sha>
git checkout -B public local                   # 拿 local 的整摞来切
git rebase --onto pub machine                  # public = main + 公共提交

git checkout pub && git merge --ff-only public
git push origin pub:main                       # 公共的出去了，本机的还在 machine 上

git checkout -B newlocal pub
git cherry-pick machine                        # newlocal = main + 本机提交（冲突在这里解）
```

然后回主检出，一条命令落地：

```bash
cd ~/.agents/gongga-agents
git status --short                            # 必须是干净的：reset --hard 不问你
git fetch -q . refs/heads/pub:refs/heads/main
git reset --hard newlocal                     # local 落到新基上，全程还在 local 上
```

最后回 worktree 收掉临时分支（`local` 已经不指着它们了）：

```bash
cd .worktrees/work
git checkout -B pub origin/main && git branch -D public newlocal machine
```

- 手术放在 worktree 里，是因为服务端的自动 rebase 只在 `local` 是检出分支时才动手：检出在别处，
  它就跳过，不会在你 rebase 到一半时插进来（它失败后会 `git rebase --abort`，那会把你做到一半的
  进度一起撤掉）。
- `git cherry-pick machine` 大概率撞在 `providers.toml` 的末尾：本机条目是文件最后一块，上游也
  在改同一个文件。冲突时**两边都留** —— 上游的新字段照抄，本机那一块仍然放在最后。冲突在这个
  worktree 里解掉，主检出那边只看得见结果。
- `git reset --hard` 是这套流程里唯一直接改主检出工作区的命令，所以要它落地之前工作区是干净的，
  而且落下来的是一条**没有冲突**的提交。它比"在主检出里 rebase 到一半"安全的地方就在这里：
  文件一次性从旧 `local` 变成新 `local`，中间没有冲突态可被服务端读到。
- 推之前再跑一遍密钥自查，`grep -nE '"(sk|ak|flm_ak|user)_' providers.toml` 必须无输出：
  搬出去的提交立刻就是公开的。

### 校验

```bash
git -C ~/.agents/gongga-agents branch --show-current       # 永远是 local
git -C ~/.agents/gongga-agents worktree list               # 主检出 local，worktree 在 pub
git -C ~/.agents/gongga-agents log --oneline local..main    # 应为空：local 永远包含 main
git -C ~/.agents/gongga-agents log --oneline main..local    # 只应出现 local: 开头的提交
git -C ~/.agents/gongga-agents rev-list --count main..local # 本机提交数，不随公共改动增长
git -C ~/.agents/gongga-agents rev-parse main origin/main   # 推过之后两个 sha 相同
```

`git reflog` 能回到上面任何一步，所以这不是丢历史的操作；但也别对它用 `push --force` ——
`main` 只快进，`--all` / `--mirror` 不用。
