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
  密钥不在这条分支上，见下面的「密钥政策」。见 [`LOCAL.md`](LOCAL.md)。
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
5. 法定节假日日期写在 `providers/statutory.toml`（国务院每年一次通知），由 `price.py` 读；
   放假调休表从 chinese-days 按年缓存到 `~/.agents/cache/`，不在仓库里。
6. 提交前确认：`grep -nE '"(sk|ak|flm_ak|user)_' providers.toml` 无输出。
