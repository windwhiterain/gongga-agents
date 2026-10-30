# gongga-agents

社区共建的「机器无关」内容仓库：一份 LLM / provider 目录，加上查额度用的脚本。
gongga 启动时把它 clone 到 `~/.agents/gongga-agents/`，以后按层读。

## 布局

与 `~/.agents/` 同构，所以同一条相对路径在两处含义相同：

```
gongga-agents/
  llm.toml      # provider 目录：端点、模型、上下文、模态、思考等级、价格、额度查询
  llm/          # 额度查询脚本，被 llm.toml 的 quota_query.script 引用
```

`quota_query.script` 里的路径**相对 `llm.toml` 自己所在的目录** —— 社区条目的
`llm/deepseek.py` 指向这个仓库里的脚本，私有条目的同名路径指向 `~/.agents/llm/deepseek.py`。

## gongga 怎么找

两个根，同一个形状：

| 根 | 谁的 | 说明 |
| --- | --- | --- |
| `~/.agents/gongga-agents/` | 社区 | 本仓库的本地 clone，gongga 维护 |
| `~/.agents/` | 用户私有 | 手写，只有这台机器知道的东西 |

- **目录不存在就 clone**：启动时异步 clone 一次；之后启动时和每小时各异步 `fetch` + 快进一次。
  断网只 warn，用本地已有副本继续，不影响 gongga 启动。
- **roster 合并**：两份 `llm.toml` 按 provider `id` 合并，社区打底、私有覆盖同名字段；
  `endpoint` / `model` 也按 `id` 合并，社区的顺序（= 默认模型的偏好顺序）保留，私有的新增排后。
- **脚本查找**：同名脚本私有优先（`~/.agents/llm/` 先于 `~/.agents/gongga-agents/llm/`）。
- 显式指定单文件（`GONGGA_LLM_CATALOG=/path/to/llm.toml`）时不做合并，只用那一个。

## 密钥政策

**这里不写密钥。** 每个 provider 只用 `key_env` 指明环境变量名，凭据放环境变量；
写进文件的那种私有密钥属于 `~/.agents/llm.toml`，不进这个仓库。同 id 的 provider
在私有文件里只补 `key`（或本地 `base_url`）就够，不必把整块抄一遍。

## 怎么贡献

1. 改 `llm.toml`：新字段能用就带上 `source` 与 `retrieved`（查证来源与日期）；官方没写的标 `unverified`。
2. 新的额度查询脚本放 `llm/`，并在对应 provider 的 `quota_query.script` 里用相对路径引用；
   `probe.py` 可以离线跑全部解析器。
3. 提交前确认：`grep -nE '"(sk|ak|flm_ak|user)_' llm.toml` 无输出。
