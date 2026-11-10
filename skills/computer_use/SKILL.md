---
name: computer_use
description: 用鼠标和键盘真的操作桌面——截图看屏、按控件树定位、点击/打字/按键，然后截图确认；也能新建/切换虚拟桌面、把窗口搬过去，把自己的活和用户隔开（虚拟桌面仅 Windows）。任何只能通过图形界面完成的事（原生应用、装不了 CLI 的软件、拖拽、菜单）用它。Windows 已验证，macOS/Linux 后端已写但未实测。
metadata:
  entry: cu
---

# 操作桌面

`$SKILL_DIR/cu` 是一条命令，三个平台：`shot`、`tree`、`click`、
`type`、`key`、`focus`、`windows`、`doctor` 在哪儿都叫这个名字，只有后端换掉。
`desktop` 是唯一的例外：只有 Windows 有能用接口驱动的虚拟桌面，别的后端会直说
不支持，而不是去猜一套热键。

```bash
# 本 skill 的目录：`@skill:` 展开里 `SKILL.md: <path>` 的父目录（`index_skill` 的 path 列同源）。
# gongga 里更短：`gongga-cli skill <name> …`，它跑 frontmatter 里声明的入口。
SKILL_DIR=<本 SKILL.md 所在目录>
"$SKILL_DIR/cu" doctor      # 先确认这台机器能用
```

`cu` 优先跑 `cu.py`（uv 解析内联依赖，不需要你先装任何东西），没有 uv 就用
`python3` / `python` / `py` 里第一个 3.9+ 的。**跨平台的是 `cu.py` + 每个平台一个后端**，
没有第二份 PowerShell 实现：两套调同一件事的代码会各漂一半，这个仓库的 UIA 树遍历
就那么在两边各坏过一次。

## 先看它能承诺什么

| 平台 | 后端 | 状态 |
|---|---|---|
| Windows | `cuwin.py`：ctypes + UIAutomation + pyvda | **实测通过**（截图、控件树、点击、打字、组合键、焦点、虚拟桌面全套；build 22631） |
| macOS | `cumac.py`：Quartz + Accessibility + `screencapture` | 未实测，按文档写的 |
| Linux (X11) | `culinux.py`：`xdotool`/`wmctrl`/ImageMagick | **实测通过**（Ubuntu 24.04 + openbox + Xvfb：8 条命令全跑通，中文字符串完整落地） |

**macOS 仍未实测**——`doctor` 会明说 `UNVERIFIED`，别把它的“能跑”当成已知事实：第一次用它时，先跑 `doctor`，再拿一件小事验证。

Linux 的实测环境是 WSL2 里的 Ubuntu 24.04 + Xvfb + openbox。**它验证了 X11 协议和工具链，不是每一台 Linux 桌面**：WSLg、GNOME、Wayland 各自还有自己的脾气。

Linux 还有一层分裂：X11 下这些工具直接驱动会话；**Wayland 按设计不允许客户端注入
输入或读别的窗口**，`click`/`type` 要 `ydotool`，控件树退化成窗口树。`doctor` 会报出
它认到的是哪儿。

## 主循环：看 → 定位 → 动 → 再看

```bash
cu shot --scale 0.5          # 打印 `@image(C:\...png)`，harness 自动把图交给模型
cu tree --depth 5            # 控件名 + 屏幕矩形
cu click 1719 851 --window 47974752
cu type --window 47974752 "你好"
cu shot --window 47974752 --scale 0.5     # 确认真的发生了
```

`tree` 是关键：截图告诉你要点什么，控件树告诉你在哪个像素。

```
Button "Reopen file" @1581,823 277x56
Edit "地址栏" @364,83 1524x56
```

中心点是 `x + w/2, y + h/2`。按名字点比按像素猜稳，尤其界面会重排。

## 坐标：物理像素，同一空间

截图像素 == `tree` 坐标 == `click` 坐标。为此后端一进来就 `SetProcessDPIAware()`。

不做 DPI 感知的进程，`CopyFromScreen` 返回缩放后的桌面（1463x823），而 UIA 报物理
像素（2560x1440）——按 tree 的坐标点下去必偏。每次 `shot` 都会多打一行映射说明，
**这一行你能看到**（harness 把 stderr 并进工具输出，不是丢掉；TUI 里只是另外上色）：

```
captured 577x352 src=1922x1173@467,144 scale=0.3
         ^图大小  ^源矩形     ^源原点    ^缩放
```

算回屏幕坐标：`screen = src原点 + 图内坐标 / scale`。

**别小看原点。** 记事本窗口原点 (467,144)、scale 0.3，「Edit」菜单真实中心 (610,244)
本该落在图里 (42.8, 30)；忘掉原点会算成 (182.8, 73.2)——偏 140x43 px，点到别的东西。

## 截图为什么能直接变成图

`shot` 打印一行 `@image(C:\...\cu-xxxx.png)`。harness 扫描**每个工具**的输出，
认出 `@image(` 就：读文件 → 上传 Files API → 请求里作为 `input_image{file_id}` 发出。
**不是 base64**，同一文件一轮只传一���。

- **必须是 `@image(<path>)` 这个形式** —— 它就是 SPEC 里的 meta。这不是"前缀"，是
  一个有开有合的括号：服务端 `Reference::of_prefix` 要求 `@image(`，`meta::mentions`
  再按括号取到 `)`。所以 `@image: <path>`（曾经的写法）和裸 `@<path>` **都只是普通文字**，
  服务端不认识、**也不会告警**——2026-11-07 就是这么白拍了一整轮的截图。
- **路径不能有空格，也不能有 `)`**：空格把名字截断，`)` 提前闭合引用。`shot` 两种都告警。
- 模型不支持图片时引用被跳过，日志有 warn——**打印了 `@image(...)` 不等于你看到了**。

实测确认过：把随机码画进图、再看图读出来，与封存答案一致。

## 给输入的每个命令都带 --window

`type` / `key` / `click` 会先把指定窗口拿到前台，**拿不到就报错拒绝**：

```
could not focus 198124 "SPEC.md - gongga"; refusing to send input blind
```

不是多余的小心。Windows 拒绝后台进程抢焦点时是**静默失败**，于是 `SendInput` 会把字
打到用户正在看的窗口。实测踩过：以为在给记事本打字，字进了 VS Code。现在用
`AttachThreadInput` 绕过限制，然后**验证** `GetForegroundWindow()` 真换了才继续。

不带 `--window` 时先打印当前前台是谁：`target: foreground hwnd=... "..."`。

## 虚拟桌面：把自己的活和用户隔开（仅 Windows）

这是用户的真实桌面，agent 在上面点来点去本来就是在打扰人。虚拟桌面让这件事可隔离：
新建一个，把自己的窗口搬过去，切过去干活，干完切回来、删掉。

```bash
cu desktop                       # 列出：* 是当前，然后号、名字、GUID
cu desktop new --name agent      # 新建，**不切过去**
cu windows                       # 找到自己要搬的那个窗口
cu desktop move 1381468 2        # 搬到 2 号
cu desktop go 2                  # 切过去（读回确认才算数）
cu shot --window 1381468 --scale 0.5
cu desktop go 1                  # 干完切回用户那个
cu desktop rm 2 --fallback 1     # 删掉，窗口落到 fallback
```

`cu desktop` 不带动作就是 `ls`。其余动作：`name N NAME` 改名、`pin W` 让窗口出现在
每个桌面、`unpin W` 取消。`W` 和别处一样，可以是 hwnd 或标题片段。

**为什么是 pyvda**：创建/切换桌面没有文档化的 Win32 API，走的是 shell 的
`IVirtualDesktopManagerInternal`，它的 GUID 随 build 换。pyvda（MIT，只依赖
comtypes——本来就在）import 时逐个试 GUID 做特征探测，覆盖到 build 26100。这份
build 知识放在一个有人维护的地方，比在这里抄一张会悄悄过期的表强。**懒加载**：只有
desktop 命令和 `--all-desktops` 才 import 它，所以 pyvda 认不出的 build 上
`shot`/`tree`/`click` 照常能用，`doctor` 单独报一行、且不参与 usable 投票。

### 别的桌面上的窗口默认是隐形的

`cu windows` 看不见它们，`--window` 也解析不到——**这是有意的**：截不到，也点不着。
shell 用 `DWMWA_CLOAKED` 把不在当前桌面的窗口藏起来（实测值 2）。要看全：

```bash
cu windows --all-desktops        # 多一列 d<号>；d? 是 view collection 里没有它
```

对别的桌面上的窗口直接 `shot` / `focus` / `type`，会拒绝并说清它在哪儿：

```
error: no window with hwnd 1381468; it is on desktop 2 -- run: cu desktop go 2, or cu windows --all-desktops
```

**cloak=2 不等于「在别的桌面上」**：UWP 幽灵窗口（「Windows 输入体验」）读回来也是 2，
只按 cloak 放行就会把它放回来。真正的判据是 view collection 里有没有它的桌面号——
幽灵没有。所以 `--all-desktops` 只放回**有桌面号、且号不等于当前桌面**的那些。

### 实测出来的语义（别按直觉猜）

| 事 | 实测结果 |
|---|---|
| `new` 会切过去吗 | **不会**：current 不变，桌面数 +1 |
| `rm` 掉当前桌面 | 切到 `--fallback`；不给就落到 1 号 |
| `rm` 只剩的那一个 | 拒绝：`cannot remove desktop 1: it is the only one` |
| `rm` 之后的编号 | 幸存者立刻重编号，所以后端按 **GUID** 核对删没删掉，不按号 |
| `pin` 后切桌面 | 窗口跟着出现在每个桌面；`unpin` 后回到它原本那个 |
| 每个动作的成败 | 都读回核对（`go` 读 current、`move` 读窗口所在桌面、`pin` 读 is_pinned），不把「COM 没抛异常」当成功 |

## 浏览器：读一页正文

浏览器没有「正文」这个控件：整页是一个 `Document`，`tree` 只给一个巨大的矩形，
里面的链接、段落、按钮都不是可点的对象。所以读网页靠**快捷键导航 + 剪贴板落地**，
而不是 `tree` 定位：

```bash
cu windows                                # 找到浏览器的 hwnd 和标题
cu key --window $W ctrl+l                 # 焦点进地址栏
cu type --window $W "https://example.com/article"
cu key --window $W enter
sleep 3
cu shot --window $W --scale 0.5           # 页面真的换了吗
cu click 1280 700 --window $W             # 点正文：不是链接、不是地址栏、不是标签栏
cu key --window $W ctrl+a
cu shot --window $W --scale 0.5           # 整页变蓝才算选中了正文
cu key --window $W ctrl+c
powershell -NoProfile -Command "Set-Clipboard -Value ''"              # 先清空
sleep 1
powershell -NoProfile -Command "Get-Clipboard -Raw | Out-File -Encoding utf8 C:\\path\\page.txt"
```

然后 `read_file` 读那个文件（或 `bash head -40`）。**别让正文回到 bash 的 stdout**：
网页里有引号、反引号、`$`，经过一次 shell 就变成另一种文本；写进文件再读，字节就是字节。

为什么每一步都截图：这条链上每一步都可能**静默落在别的地方**——焦点还在地址栏、
点击落到标签栏切走了页面、`ctrl+c` 复制的是上一轮的东西。每个动作一张图，是唯一能
在下一次动作之前发现的信号；猜坐标盲点两次之后你已经在另一个标签里打字了。

非 Windows：剪贴板那一步换 `xclip -selection clipboard -o`（X11）或 `pbpaste`（macOS），
本 skill 只在 Windows 上实测过这条链。

## 安全边界

这是**用户的真实桌面**，不是沙箱：

- 打字前确认目标。要试打字，开一个自己的临时文件，别往用户正在编辑的文档里写。
- 关闭自己开的东西，别留垃圾窗口。
- 破坏性操作（删除、覆盖保存、安装）先截图确认按钮是什么。
- **截图会把屏幕上的秘密一起带上**：编辑器里的 API key、密码框、私信。截到的图会上传
  并进入对话记录，之后收不回。范围尽量用 `--window` 收窄，别整屏；图里有密钥就先切走。

## 命令

| 命令 | 作用 |
|---|---|
| `cu doctor` | 自检：DPI、控件树、INPUT 结构、截图、虚拟桌面 |
| `cu windows [--all-desktops]` | 顶层窗口：hwnd、pid、矩形、标题（`*` 是前台）；加旗标则含别的桌面并多一列 `d<号>` |
| `cu shot [--window W] [--scale F] [--out P]` | 截图，打印 `@image(<路径>)` |
| `cu tree [--window W] [--depth N] [--max N]` | 控件树：类型、名字、屏幕矩形 |
| `cu click X Y [--double] [--right] [--window W]` | 点击 |
| `cu type [--window W] <文本>` | 输入文本（Unicode，中文正常） |
| `cu key [--window W] ctrl+s` | 组合键 |
| `cu focus W` | 把窗口拿到前台 |
| `cu desktop [ls]` | 列虚拟桌面（仅 Windows，下同）：`*` 是当前，号、名字、GUID |
| `cu desktop new [--name N]` | 新建一个桌面，**不切过去** |
| `cu desktop go N` | 切到 N 号桌面 |
| `cu desktop rm N [--fallback M]` | 删掉 N 号；删的是当前桌面时落到 M（默认 1） |
| `cu desktop name N NAME` | 给桌面改名 |
| `cu desktop move W N` | 把窗口搬到 N 号桌面 |
| `cu desktop pin W` / `unpin W` | 让窗口出现在每个桌面 / 取消 |

`W` 可写 hwnd（`47974752`）或标题片段（`"Notepad"`）；片段匹配多个时报错列候选。
`key` 支持 `ctrl` `shift` `alt` `win` `f1`–`f24` `enter` `tab` `esc` `space`
`backspace` `delete` `up/down/left/right` `home/end` `pageup/pagedown` `insert`
`printscreen` `capslock`，或单个字母，用 `+` 连。

## 踩过的坑（都别再踩回去）

| 现象 | 真因 | 现在怎么做 |
|---|---|---|
| UIA **在 root 上** `FindAll` 卡 **210 秒**，CPU 0 | UIA core 在等某个从不应答的顶层 provider。.NET 的 `UIAutomationClient` 自带 managed client-side provider，所以 PowerShell 里同样的调用 0.3s | 顶层窗口走 `EnumWindows`，UIA 只问**单个窗口**；`tree` 用递归 `FindAll(Children)` 而不是 root 查询 |
| `IUIAutomationTreeWalker` 调 `GetFirstChild` 报 `AttributeError` | comtypes 生成的模块把这个接口给成了无方法的裸指针，`QueryInterface` 也一样 | 不用 walker，递归 `FindAll(Children)` |
| `SendInput` 返回满数但字没进去（PowerShell 版） | PowerShell 不能给**嵌套结构体字段**赋值，写进副本后丢弃 | 输入搬进 C#；Python 版直接在 ctypes 里构造 `INPUT`，`sizeof` 自动是 40 |
| 同上，换布局才好 | x64 上 `INPUT` 的联合体必须从偏移 **8** 开始 | Windows 后端用 `_anonymous_` 联合体，`doctor` 断言 `sizeof(INPUT)` |
| `tree` 报 `Cannot convert "∞" to Int32` | 无矩形的控件 UIA 返回无穷大 | Python 版按 right/bottom 是否 0 判空，打印 `?` |
| `key enter` 报成功但没反应（Linux） | X11 的键名是 `Return`；`xdotool` 遇到未知键名时打印 `No such key name` 却仍然 exit 0 | `culinux.py` 显式映射友好名→keysym，未知键直接 ValueError，并检查 stderr |
| Windows 上多出「Windows 输入体验」之类幽灵窗口 | UWP 隐藏窗口，`EnumWindows` 看得见但点不着 | 用 `DwmGetWindowAttribute(DWMWA_CLOAKED)` 过滤 |
| 加了 `--all-desktops` 之后幽灵窗口又回来了 | 幽灵的 cloak 也是 **2**（`DWM_CLOAKED_SHELL`），和「在别的桌面上」同一个值，光看 cloak 分不开 | 判据改成 view collection 里的桌面号：幽灵没有号，真窗口有且不等于当前桌面 |
| 别的桌面上的窗口 `cu windows` 找不到、`--window` 报 no window | shell 把它 cloak 了，默认列举按设计跳过 | `cu windows --all-desktops` 看它在几号，再 `cu desktop go N`；错误信息本身也会这么说 |
| `cu desktop rm 2` 之后按号核对，说「还在」 | 删掉一个，幸存者立刻重编号，2 号又存在了（是原来的 3 号） | 按 **GUID** 核对删没删掉 |
| 以为 `desktop new` 会切过去，接着就在原桌面上截图 | Windows 创建桌面**不切换** | `new` 之后显式 `go`；`new` 的输出会告诉你当前还在几号 |
| `--window` 被 PowerShell 当自己的参数 | PS 参数解析套在脚本参数上 | PS 回落版走 `CU_ARGV` 环境变量（`\x1f` 分隔） |
| `ctrl+a` 复制回一串 URL | 焦点还在地址栏（`ctrl+l` 之后没点进正文，或点击落空） | `ctrl+a` 之后截图：整页变蓝才是正文；落地后看第一行是不是 `http` |
| 点击把页面切到别的标签、或别的应用 | 坐标从上一帧算的，或落在了标签栏/窗口外；`--window` 只保证把窗口拉前台，不保证坐标在它里面 | 坐标从最近一张 `shot` 算，点击前后各一张图 |
| `Get-Clipboard -Raw` 拿回上一轮的旧文本 | `ctrl+c` 没生效，剪贴板没变 | 复制前先 `Set-Clipboard -Value ''`，落地后确认文件非空 |
| 选中的正文缺头少尾 | 慢页面 / 单页应用还没渲染完 | 截图看到正文出现再选；必要时隔一秒连拍两张 |
| 这版 PowerShell 缺 `[ushort]`/`[uint]` 别名 | PS 5.1 的行为 | PS 回落版用全限定 `[System.UInt16]` |

## 一个完整例子

```bash
cu windows                        # 找到目标 hwnd
cu tree --window 12345 --depth 4  # 读 Edit 框和 Button 的矩形
cu click 700 400 --window 12345   # 点进输入框
cu type --window 12345 "fix: 折叠上下文"
cu click 1780 851 --window 12345  # 坐标来自 tree
cu shot --window 12345 --scale 0.5
```

## 先问一句：真的需要点界面吗

点界面是最慢最脆的一条路。**能用 API、CLI、配置文件完成的，就用那些。** 只有当那个
东西只有 GUI 时，才用这个 skill。
