---
name: proxy
description: 让某台机器的某个 host 走指定的上游（HTTP/SOCKS5/直连），并证明它真的走了；同时把一个订阅装成能跑的 mihomo/clash core，并驱动它——列策略组、换节点、测延迟和吞吐、切模式、热重载。任何"这个 API 连不上/到底有没有走代理/怎么只让某个域名走代理/换哪个节点/哪个节点快"的问题用它。
---

# 按 host 路由，并证明它真的走了

这个 skill 的 `proxy.py` 是一个**自己实现的路由代理**：纯 Python 标准库，按规则把
每个连接送到指定上游，并把每条决定记下来。没有第三方包，不需要装任何东西。

```python
# 下文的 fn(...) 都是 run_skill(id="proxy", cmd={"name": fn, "args": {...}}) 的简写。
doctor()      # 先看这条链每一环
```

## 为什么不是「配个代理」

两件事会让通常的做法失效，而且都会让人查错方向：

1. **按规则路由的代理对未列出的 host 是直连的。** 所以「请求失败」证明不了代理坏了，
   「请求成功」也证明不了它走了代理。
2. **`HTTP_PROXY` 环境变量不是证据。** curl 和 Node 认，Go 和不少 Python 库要别的名字，
   而按规则路由的代理仍可能把某个 host 送直连。

所以这里的判定单位是**每条连接的真实去向**：路由是代理自己决定的，它把决定写进
`~/.proxy/decisions.jsonl`，`check` 读的是这个记录，不是猜。

## 接一个上游（用户提供任何 API）

```python
# HTTP 代理（最常见）
upstream("add", "corp", "127.0.0.1:8080", "http")
upstream("add", "corp", "127.0.0.1:8080", "http", auth="user:pass")

# SOCKS5
upstream("add", "tun", "127.0.0.1:1080", "socks5")

# 另一个按流量计费的服务，一个上游一个名字即可，规则里按名字选
upstream("add", "premium", "api.example.com:3128", "http")
```

然后决定谁走它：

```python
add("github.com", "corp")                    # 后缀匹配（含子域）
add("api.openai.com", "premium", "exact")
add("10.0.0.0/8", "direct", "cidr")
```

## 三条命令的主线

```python
# 1. 改配置后重启，规则在启动时读入
serve()                                 # 前台；要常驻用 detached bash

# 2. 让客户端指向它
env()                                   # 打印 export，含 git 那行
# export HTTPS_PROXY=http://127.0.0.1:7899
# export NO_PROXY=localhost,127.0.0.1,::1

# 3. 证明这个 host 真的走了你指定的上游
check("github.com")
```

`check` 的输出形如：

```
host              github.com
rule              suffix github.com -> relay
upstream          relay (http)
proxy listening   yes
request           HTTP 200
proxy decision    github.com via relay

verdict           ROUTED VIA relay
```

`proxy decision` 那一行来自代理自己：它记录了这条连接选了哪个上游、命中哪条规则。
`verdict` 只有这几种：`ROUTED VIA <name>` / `DIRECT` / `ROUTE FAILED` / `REQUEST FAILED` /
`NOT RUNNING`。

## 两层：分流是一个东西，出口是另一个

`proxy` 自己管**这台机器上按 host 分流**。真正把流量送出去的上游，通常是一个 mihomo
（Clash.Meta）core：它带一份节点目录、一堆策略组（GitHub 走哪、YouTube 走哪），还有一个
运行时可读写的 HTTP API。

分开看这两层，否则会问错问题：

| 层 | 谁决定 | 改它用什么 |
|---|---|---|
| 分流：这个 host 走不走代理 | `proxy.py` 的规则，或 core 自己的 rules | `add(...)` / `mode()` |
| 出口：走哪个节点 | core 的策略组 | `use_node(group, node)` |

**换节点、bench 节点，全在 core 的 API 上。** 任何第三方客户端都只是这个 API 的一层壳，
所以本 skill 不点名任何客户端：它要的是一个订阅 URL（或一份 config.yaml）和一颗核。

## 找到控制器（"怎么配 API"）

core 的 API 叫 external-controller：一个 HTTP 端点 + 一个 secret。它们写在 core 自己的配置里：

```yaml
external-controller: 127.0.0.1:9090
secret: "…"
mixed-port: 7897          # 客户端要指向的端口——不是 API 端口
```

```python
core_status()
```

`doctor` 按这个顺序找，并且**打印它用的是哪一条**：

1. `--controller` / `--secret`
2. `MIHOMO_CONTROLLER` / `MIHOMO_SECRET`
3. `~/.proxy/mihomo.json`（`core connect … --save` 或 `core start` 写下的）
4. core 自己的 config.yaml：`$PROXY_HOME/core/config.yaml`（本 skill 自己起的那个）、
   `~/.config/mihomo/`、`~/mihomo/`、`/etc/mihomo/`
5. 探 9090 / 9091 / 9097 / 6170

**这里没有「读某个客户端的私有文件」这一步。** 客户端的 store 是别人实现里的一个细节，换一个
客户端就换一条路径；它生成的 config.yaml 落在第 4 步，它知道地址与 secret 就写进第 3 步那个
文件。本 skill 只认这两样东西，不认任何一对具体的目录名。

换一个端点：

```python
connect("10.0.0.2:6170", secret="xxx", save=True)   # 记住它
# 或者只用一次：先把 MIHOMO_CONTROLLER / MIHOMO_SECRET 设进环境，再 groups()
```

## 换节点

```python
groups()                                # 有哪些策略组，各自现在走谁
nodes("漏网之鱼")                        # 这个组的候选，标出当前
use_node("漏网之鱼", "日本 I1")            # 切换，然后读回来确认
use_node("漏网之鱼", "印尼")               # 唯一匹配才允许省全名
```

三条纪律：

- **歧义拒绝，不猜。** `日本` 命中 10 个会直接报错并全部列出来；"取第一个匹配"是错路由的
  常见来源，而且在界面上看起来完全正常。
- **notice 不是节点。** 供应商会把「未读消息」塞进候选列表。选它不报错，但所有继承它的组
  会一起坏掉。`use` 会拒绝。
- **切换要读回。** API 返回成功只说明请求收下了。`use` 之后重新 GET 那个组，`now` 不等于
  目标就报错。

真正的证据是出口 IP，不是 API 回显：

```bash
curl -s -x http://127.0.0.1:7897 https://api.ipify.org      # 103.196.116.251
```
```python
use_node("漏网之鱼", "日本 I1")
```
```bash
curl -s -x http://127.0.0.1:7897 https://api.ipify.org      # 45.146.163.49
```
```python
use_node("漏网之鱼", "印尼 B46")
```
```bash
curl -s -x http://127.0.0.1:7897 https://api.ipify.org      # 103.196.116.251
```

**前提是这个 echo 站点和目标 host 命中同一组规则**——上面成立是因为 `api.ipify.org` 在
白名单里，走的就是 `漏网之鱼`。不在白名单的 echo 站点走直连，地址不变，什么也证明不了。

## bench：延迟和带宽是两个轴

```python
bench("漏网之鱼")                                   # 延迟，快的在前，超时在最后
bench("漏网之鱼", throughput=True, seconds=8, limit=5)
```

不写组名时它会挑"真实节点最多的那个组"并在输出里说明——那常常不是你关心的组，所以**写清楚
组名**。

只按延迟选节点会选错：一条便宜的共享线路可以答得很快、传得很慢。

**吞吐必须证明字节真的过了那个节点**，这里有个会静默说谎的坑：在 `rule` 或白名单模式下，
测试 URL 很可能命中一条直连规则，于是测出来的是**本机自己那条线**的速度，而标签上写着节点名。
这正是"换节点救不了下载慢"这类结论的来源之一——那个数字可能压根没经过节点。

所以 `--throughput` 先把 core 切到 `global` 模式（规则不再参与决定），再逐节点跑固定窗口的
传输，最后在 `finally` 里把模式和原来的选择一起还原。bench 把整台机器留在 global 模式是
不能接受的，被中断也一样要还原。

`--limit` 不是贴心功能：81 个节点 × 8 秒是 11 分钟。

## 模式与热重载

```python
mode()                # 现在是 rule / global / direct
mode("direct")        # 全部直连——排查“是不是代理的问题”最干脆的一刀
reload()              # 改完 config.yaml 立刻生效，不重启 core；reload(path) 指定文件
```

`reload` 走 `PUT /configs?force=true`，是唯一"改规则不用重启"的路径。白名单就是这样生效的。

## 一条命令：cli + key

本 skill 要的输入只有一个：**订阅链接**。其余全是它自己算出来的——核、端口、secret、geo 库。

```python
up("<订阅链接>")
```

一条命令做完这些，每一步都不问你：

1. **核**：`$PROXY_HOME/core/` 里没有就从 release 取（zip 解出可执行文件），并取 `geoip.metadb`
   ——带 `GEOIP` 规则的配置缺了它，核会自己下载，而必须靠代理去下载的那台机器正好下不到；
2. **订阅**：HTTP GET（UA `clash-verge/v1`，走环境里的代理），校验顶层有 `proxies:` 与 `rules:`；
3. **端口**：`mixed-port` 7897、controller 9090 被占就顺着往下找（实测在一台 7897/9090 都被
   另一颗核占着的机器上，它自己落到 7898/9091 并说出来）；
4. **配置**：只改写四样东西——`external-controller`、生成的 `secret`（存档复用，重新渲染不失效）、
   `allow-lan: false`、一个 `mixed-port`，并把订阅自带的 `port`/`socks-port` 关成 `0`；
   节点、策略组、**规则列表原样透传**：路由是订阅自己的，本 skill 不另加一种说法；
5. **起核**：先 `mihomo -t` 验（配置错在启动前报出来），再 detached 起，pid 落 `core.pid`；
   就绪的判据是**控制器答话**，不是端口开着；
6. **记住**：controller + secret 写进 `$PROXY_HOME/mihomo.json`，之后 `core_status()` 不用问人。

```python
up()      # 不给链接：复用已经存下的订阅（换链接就是再给一个）
down()    # 只停自己起的那个 pid（先证明那是核，不是回收来的别人的 pid）
```

之后全是标准那套，本 skill 只是它的壳：

```python
core_status()                     # 控制器在哪、能不能答、模式与端口
groups(); nodes("组")              # 策略组与候选
use_node("组", "节点"); mode()   # 换节点、切模式（读回确认）
bench("组")                       # 延迟或吞吐（throughput=True）
reload()                          # 热重载，不重启不断连接
```

**证明一条连接的去向**，是核自己说的，不是猜的：

```
level=warning msg="[TCP] dial 🐟 漏网之鱼 (match Match/) 127.0.0.1:62732(curl.exe) --> cp.cloudflare.com:80 error: … i/o timeout"
```

规则名、策略组、目标、失败原因同一行——所以「502」是**那颗节点坏了**，而不是「代理坏了」。
实时视图是 controller 的 `/connections`。

手动分步（`up` 就是它们四个的组合，排障时才需要）：`core install`、`core subscribe URL`、
`core render`、`core start`、`core stop`、`core status`。`$PROXY_HOME`（默认 `~/.proxy`）
整棵可以删掉重来：分流规则在 `config.json`，核在 `core/`（`config.yaml`、`subscription.yaml`、
`secret`、`core.pid`、`core.log`）。

还有三件事**不在它手里**，也说清楚，免得以为起完核就完事：

| 能力 | 在哪 |
|---|---|
| 系统代理开关（Windows 注册表 / 环境变量） | 你的 shell 或系统设置；`env()` 打印的是本 skill 自己的分流代理 |
| 开机自启 | 系统的任务计划 / 服务 |
| 账号 → 订阅链接（供应商登录） | 供应商的客户端或网页；本 skill 从链接开始 |

## 出口 IP 只在规则相同时才说明问题

`check` 会比走代理和不走代理的出口 IP，但**那只在那个 echo 站点和目标 host 命中同一条
规则时才有意义**。如果不相同，它会明确说出来：

```
exit note         icanhazip.com follows a different rule, so that address says nothing
                  about github.com; the decision log below is what settles it
```

这是这个 skill 存在的原因之一：拿一个未路由的 echo 站点去推断「代理没生效」，会把人
带进完全错误的方向。`doctor` 也一样——它比较出口只在有用时才比，否则明说「不适用」。

## 配置

`~/.proxy/config.json`，也可以直接手改：

```json
{
  "listen": {"host": "127.0.0.1", "port": 7899},
  "upstreams": {
    "direct": {"kind": "direct"},
    "relay":  {"kind": "http", "address": "127.0.0.1:7897"}
  },
  "rules": [
    {"type": "suffix", "value": "github.com", "via": "relay"},
    {"type": "all", "via": "direct"}
  ]
}
```

规则按顺序匹配，**第一条命中就定**。规则类型：`suffix`（含子域）、`exact`、`prefix`、
`cidr`、`all`。

**最后一条必须是 `all`。** 少了它，一个不匹配任何规则的 host 没有定义好的去向，而
"大概直连吧"正是让代理看起来坏了的那种假设。`validate_config` 会直接拒绝这种配置。

## 命令

| 命令 | 作用 |
|---|---|
| `doctor()` | 逐环检查：curl、配置合法性、catch-all、监听、**每个上游能否真的建立隧道** |
| `serve()` | 前台跑代理 |
| `status()` | 配置、监听状态、最近的决策 |
| `rules()` | 规则表 |
| `check(host)` | 这个 host 到底走没走、走的哪条 |
| `add(value, via, rule_type)` | 加规则（插在 catch-all 之前） |
| `rm(value)` | 删规则 |
| `upstream(action, name, address, kind, auth)` | 管理上游 |
| `env()` | 打印客户端需要的 export |
| `up(key, replace, mixed_port, controller_port)` | cli + key：没有核就装、有链接就抓订阅、挑空闲端口、渲染、验、起、记住 |
| `down()` | 停掉 `up` 起的那个 pid |
| `core_install(version, source)` | 把 mihomo（和 geo 数据库）放进 `$PROXY_HOME/core/` |
| `core_subscribe(url, source)` | 抓订阅，落到 `$PROXY_HOME/core/subscription.yaml` |
| `core_render(mixed_port, controller_port)` | 渲染 `config.yaml`，显示 controller / secret / mixed port |
| `core_start()` / `core_stop()` / `core_state()` | 起核（detached，pid 落在 `core.pid`）/ 只停它 / 报告现状 |
| `core_status()` | 控制器在哪、能不能答、模式与端口 |
| `groups()` / `nodes(group)` | 策略组与候选节点 |
| `use_node(group, node)` | 换节点，并读回确认 |
| `bench(group, throughput)` | 延迟或吞吐，快的在前 |
| `mode(which)` | 报告或切换模式 |
| `reload(path)` | 热重载配置，不重启 |
| `connect(controller, secret, save)` | 记住一个控制器地址 |

## 实现上几个不显然的决定

- **所有上游走同一条路**：先建到 `host:port` 的隧道，再在隧道里说普通 HTTP。所以
  CONNECT 和明文请求是同一段代码，直连/HTTP/SOCKS5 也是同一段。分支越少，能错的地方越少。
- **不用 `SO_REUSEADDR`（Windows 上）**：它允许第二个实例绑同一端口，连接会分给任意一个。
  一个还活着、带着旧规则的实例，看起来就像"新加的规则没生效"——这个坑我踩过。现在端口
  被占用会直接拒绝启动。
- **`doctor` 检查上游能否建立隧道，不检查端口通不通**：一个接受连接但拒绝 CONNECT 的代理
  看起来是活的，却毫无用处。
- **只读标准库**：HTTP CONNECT、SOCKS5 握手、PNG、relay 都是手写的，`uv run` 瞬间启动。

## 限制

- 只处理 HTTP 和 HTTPS（通过 CONNECT）。其他协议的流量不会经过它。
- 不做 DNS 代理：域名在代理进程里解析，客户端看到的是自己解析的地址。需要 fake-ip 一类
  行为的话，这一套不够。
- 规则在启动时读入，改完要重启 `serve`。
- `check` 的判定依赖代理写日志；日志路径不可写时它会退化成"没有记录"，而不是假装成功。
- `core_start()` 起的是**前台进程的 detached 版**：它不装服务、不改开机项、不动系统代理
  （见上表）。想让核跟着机器起来，那是系统计划任务的事。
- `core subscribe` 只吃 Clash 格式的订阅（顶层有 `proxies:` 与 `rules:`）；其他格式的订阅是
  另一件事。
- 规则列表是订阅的，本 skill 不生成、不改写：要改路由就改配置里的 `rules:`（标准 Clash 语法），
  再 `core reload`。

## 自己验证它

```bash
python3 test_rules.py      # 16 项：后缀/子域/大小写/CIDR 边界/规则顺序/配置拒绝
python3 test_mihomo.py     # 48 项：配置抓取/名称解析/排序/bench 还原/发现顺序

核的生命周期不在单测里，它要真进程（这也正是它值得单独跑一遍的原因）。用临时
`PROXY_HOME` 和让开的端口，别碰机器上正在用的那颗核：

```bash
export PROXY_HOME=<一个空目录>            # ~/.proxy 的替身，删掉就干净
```python
core_subscribe(source="subscription.yaml")   # 路径也认，与链接等价
up()                                        # 装 + 渲染 + 验 + 起，然后记住控制器
core_status()                               # 控制器答话 + mixed port 能出去
groups()                                    # 策略组，第一屏就够了
down()                                      # pid 从 core.pid 读，不是 $!
```

一轮应当看到：`up` 从空目录到控制器答话（含下载）二十几秒，之后秒级；`doctor` 的 `found via` 是
`$PROXY_HOME/core/config.yaml`（删掉 `mihomo.json` 之后也是它，说明发现不依赖缓存）；
`stop` 之后同一颗核不再应答，而机器上别的核一个都没动。
```

这些用例有齿。规则那边：把 `suffix` 改成 `startswith`、让 `cidr` 忽略掩码、倒着匹配规则、
取消 catch-all，每一个都会红。core 这边做过同样的验证：把排序改成"失败也算 0 延迟"、
让 `node_choices` 保留组引用、把 `finally` 还原改成只在成功时还原、把客户端 state 排到
config 前面、去掉 notice 识别、让速率忽略时间窗口——六项全部变红。

其中一条值得记住：**"bench 失败也要还原"最初没被测到**，因为逐节点的错误在循环里就被
吞掉了，`finally` 和"只在成功时还原"在那些用例里表现一致。改成用 Ctrl-C 那种会逃出循环的
异常去测，它才真的红。测试没红，先怀疑测试。
