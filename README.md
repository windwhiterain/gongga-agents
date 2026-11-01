# gongga-agents

社区共建的「机器无关」内容仓库：一份 provider 目录，加上查额度用的脚本。
gongga 启动时把它 clone 到 `~/.agents/gongga-agents/`，以后按层读。

## 布局

与 `~/.agents/` 同构，所以同一条相对路径在两处含义相同：

```
gongga-agents/
  providers.toml   # provider 目录：端点、模型、上下文、模态、思考等级、价格、额度查询
  providers/       # 额度查询脚本，被 providers.toml 的 quota_query.script 引用
```

`quota_query.script` 里的路径**相对 `providers.toml` 自己所在的目录** —— 社区条目的
`providers/deepseek.py` 指向这个仓库里的脚本，私有条目的同名路径指向
`~/.agents/providers/deepseek.py`。

## gongga 怎么找

两个根，同一个形状：

| 根 | 谁的 | 说明 |
| --- | --- | --- |
| `~/.agents/gongga-agents/` | 社区 | 本仓库的本地 clone，gongga 维护 |
| `~/.agents/` | 用户私有 | 可以整个不存在；只有要覆盖某个字段时才写 |

- **目录不存在就 clone**：启动时异步 clone 一次；之后启动时和每小时各异步 `fetch` + 快进一次。
  断网只 warn，用本地已有副本继续，不影响 gongga 启动。
- **roster 合并**：两份 `providers.toml` 按 provider `id` 合并，社区打底、私有覆盖同名字段；
  `endpoint` / `model` 也按 `id` 合并，社区的顺序（= 默认模型的偏好顺序）保留，私有的新增排后。
- **脚本查找**：同名脚本私有优先（`~/.agents/providers/` 先于 `~/.agents/gongga-agents/providers/`）。
- 显式指定单文件（`GONGGA_PROVIDERS_CATALOG=/path/to/providers.toml`）时不做合并，只用那一个。

## 密钥政策

**这里不写密钥。** 每个 provider 只用 `key_env` 指明环境变量名。解析顺序是
**环境变量 > 私有文件里的 `key`**，所以一台机器可以没有私有 `providers.toml`：

```bash
setx DEEPSEEK_API_KEY "sk-..."   # Windows：写进用户环境变量
export DEEPSEEK_API_KEY=sk-...   # macOS / Linux
```

要覆盖别的东西（内网 `base_url`、自建 provider）才写 `~/.agents/providers.toml`，
同 id 的 provider 只补差异字段即可；那个文件不进任何仓库。

## 怎么贡献

1. 改 `providers.toml`：新字段能用就带上 `source` 与 `retrieved`（查证来源与日期）；官方没写的标 `unverified`。
   规模写进 `[provider.model.params]`：`total` / `active`（单位十亿参数），MOE 的激活参数按 token 计；
   厂商没公布就整块不写，别猜一个数。
2. 每个模型必须有 `tier`：`flash` / `pro` / `sota` / `test`。它是**人对这个模型值不值得
   派活的判断**（不是算出来的），`model_pool` 只读不猜；改档就是改这一行，不用重新编译。
   哪个模型被鼓励用（倍率、白名单）在 `skills/model_pool/preference.toml`。
3. 新的额度查询脚本放 `providers/`，并在对应 provider 的 `quota_query.script` 里用相对路径引用；
   `providers/probe.py` 可以离线跑全部解析器。
4. 提交前确认：`grep -nE '"(sk|ak|flm_ak|user)_' providers.toml` 无输出。
