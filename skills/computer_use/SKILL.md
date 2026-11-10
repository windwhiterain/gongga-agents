---
name: computer_use
description: 用鼠标和键盘真的操作桌面——截图看屏、按控件树定位、点击/打字/按键，然后截图确认；也能新建/切换虚拟桌面、把窗口搬过去，把自己的活和用户隔开（虚拟桌面仅 Windows）。任何只能通过图形界面完成的事（原生应用、装不了 CLI 的软件、拖拽、菜单）用它。Windows 已验证，macOS/Linux 后端已写但未实测。
metadata:
  entry: cu
---

# 操作桌面

`$SKILL_DIR/cu` 是一条命令，三个平台：`shot`、`tree`、`click`、
`type`、`key`、`focus`、`windows`、`doctor` 在哪儿都叫这个名字，只有后端换掉。

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
| Windows | `cuwin.py`：ctypes + UIAutomation | **实测通过**（截图、控件树、点击、打字、组合键、焦点全套；build 22631） |
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
- **想不打扰人，别指望虚拟桌面**：它只是同一登录会话的多块桌面，同一输入队列、同一
  前台窗口，而且同一时刻只有一块在屏上——切过去就是把用户的屏换掉。真隔离要么不起
  窗口（离屏渲染截图），要么开另一个登录会话/另一台机器。

## 命令

| 命令 | 作用 |
|---|---|
| `cu doctor` | 自检：DPI、控件树、INPUT 结构、截图 |
| `cu windows` | 本桌面的顶层窗口：hwnd、pid、矩形、标题（`*` 是前台） |
| `cu shot [--window W] [--scale F] [--out P]` | 截图，打印 `@image(<路径>)` |
| `cu tree [--window W] [--depth N] [--max N]` | 控件树：类型、名字、屏幕矩形 |
| `cu click X Y [--double] [--right] [--window W]` | 点击 |
| `cu type [--window W] <文本>` | 输入文本（Unicode，中文正常） |
| `cu key [--window W] ctrl+s` | 组合键 |
| `cu focus W` | 把窗口拿到前台 |

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
