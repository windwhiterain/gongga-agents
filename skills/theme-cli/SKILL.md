---
name: theme-cli
description: 切换 Windows 系统暗色/亮色主题，可以手动切换，也可以设置每天定时自动切换。任何"怎么让 Windows 定时换主题""暗色模式怎么自动开""把系统改成亮色/暗色""theme-cli 怎么用""theme-cli --help"的问题用它。Windows 已实测；macOS/Linux 未实现。
---

# 定时切换 Windows 明暗主题

Windows 11 **没有**内置的定时换主题功能。设置 > 个性化 > 颜色只有浅色/深色/自定义三档，
没有时间表；唯一自带时间表的是「夜间光照」，但它只调色温暖色，**不切换任务栏和应用的明暗**。

这个 skill 提供一个全局 CLI，两种用法：手动切，或者挂每日定时任务。

```bash
theme-cli light                              # 立刻切成亮色
theme-cli toggle                             # 明暗互切
theme-cli schedule --light 08:00 --dark 20:00  # 每天 08:00 亮、20:00 暗
theme-cli status                             # 看当前主题和排程状态
```

## 帮助

```bash
theme-cli --help              # 总览：命令表 + 选项表 + 示例
theme-cli -h                  # 同上
theme-cli /?                  # 同上（cmd 和 PowerShell 都行）
theme-cli help schedule       # 单个命令的详细说明
theme-cli schedule --help     # 同上，更贴近习惯的写法
theme-cli status --help
theme-cli --version           # theme-cli 1.0.0
```

`schedule` 和 `status` 有各自的分命令帮助；`light` / `dark` / `toggle` 没有专属帮助，
带 `--help` 时回落到总览。

`-?` 也能用，但它由 PowerShell 自己的 `Get-Help` 机制渲染脚本的 comment-based help，
版式和上面那套不同（见下面「实现上几个不显然的地方」）。

## 为什么用注册表 + WM_SETTINGCHANGE

控制明暗的是两个**按用户**的注册表值：

| 值 | 含义 |
|---|---|
| `HKCU\...\Themes\Personalize\SystemUsesLightTheme` | 1 = 亮色外壳（任务栏、开始菜单），0 = 暗色 |
| `HKCU\...\Themes\Personalize\AppsUseLightTheme` | 1 = 亮色应用，0 = 暗色 |

写完这两个值之后还要广播 `WM_SETTINGCHANGE`（`lParam` = `ImmersiveColorSet`），外壳和已打开的
应用才会**立刻重绘**——不需要重启资源管理器，也不需要注销重登。少了这一步的话，任务栏会
保持旧配色直到下次注销。

广播用的是 `SendNotifyMessage` 而不是 `SendMessageTimeout`。后者的超时是**按窗口逐个计算**
的：几个响应慢的窗口各耗满整个超时，累积起来会让一次切换花掉**几十秒**（本机实测 36 秒）。
`SendNotifyMessage` 只投递不等待，实测 **14 毫秒**返回，主题照样立即生效。

两个值独立，所以能做出「暗色任务栏 + 亮色文档」这种组合。CLI 的 `light`/`dark` 会把两个
一起设成同一个值。

## 手动切换

```bash
theme-cli light     # 系统 + 应用都切成亮色
theme-cli dark      # 系统 + 应用都切成暗色
theme-cli toggle    # 按当前状态翻转
```

切换是即时的，会立刻改变任务栏和已打开应用的外观。

## 定时切换

```bash
theme-cli schedule --light 08:00 --dark 20:00
```

装两个每日任务：`WindowsTheme-Light` 和 `WindowsTheme-Dark`。

改时间、只看其中一个、或者全删：

```bash
theme-cli schedule --light 07:30            # 只改亮色时间，暗色时间保持不变
theme-cli schedule --show                   # 看已装的排程
theme-cli schedule --remove                 # 删掉两个任务
```

排程任务用 **Interactive 登录类型**（`RunLevel Limited`），这是刻意的：主题注册表在 `HKCU`
下，必须以登录用户身份运行才能改到自己的账户。如果改成「不管用户是否登录都运行」，任务会
写进 SYSTEM 账户的 HKCU，**对当前用户毫无效果**——这是这类方案最常见的失败原因。

副作用：任务只在**用户已登录**时触发。这是主题机制本身的约束，不是配置失误。

## 看状态

```bash
theme-cli status
```

```
Current theme : Dark (system Dark, apps Dark)

Schedule:
  Light  Ready  at 08:00  next 2026-10-30 08:00  (last result 0)
  Dark   Ready  at 20:00  next 2026-10-29 20:00  (last result 0)
```

`last result 0` 表示上次执行成功。其他值说明那次运行有问题。

给程序读的话加 `--json`：

```bash
theme-cli status --json
```

输出 `system` / `apps` / `combined` 和 `schedule` 数组，含 `installed`、`time`、`nextRun`、
`lastResult`。

## 实现上几个不显然的地方

这几个都是实测踩到的，改代码前先读。

### 参数是自己解析的，没有 param 块

PowerShell 会拒绝任何没在 `param` 块里声明过的参数名，`--help` 在这种写法下**必然在脚本体
执行之前就失败**。所以脚本完全没有 `param` 块：所有 token 原样进 `$args`，由 `Read-Arguments`
手动解析。

两个副作用要注意：

- **函数里看不到脚本级的 `$args`。** 函数有自己的作用域，脚本级的 `$args` 不会自动传进去，
  必须显式传：`Read-Arguments -Tokens $args`。
- **`-File` 和 `.cmd` 两种入口的行为要一致。** 去掉 `param` 块后，两边都把 token 原样交付，
  解析结果相同。

在这套解析里 `--light` / `--dark` **各自吃掉后一个 token**。它们也**不能用 `[switch]`**：
switch 不接收值，`08:00` 会落到位置参数上，而位置参数只有一个槽，`20:00` 就无处可绑，
报「找不到接受自变量 '20:00' 的位置参数」。

只给其中一个时，另一个从已装排程里读回来保留原时间。

### 广播必须用 SendNotifyMessage

见上面「为什么用注册表 + WM_SETTINGCHANGE」一节。用 `SendMessageTimeout` 的话，
超时按窗口逐个累加，本机实测一次切换要 **36 秒**，计划任务会长时间挂在 `Running`。
换成 `SendNotifyMessage` 后手动调用约 **1 秒**、计划任务约 **3.7 秒**。

### 返回空数组的函数要包一层 `@()`

PowerShell 把函数返回的空数组展开成 `$null`，在 `Set-StrictMode -Version Latest` 下
访问 `.Count` 会直接抛「在此对象上找不到属性 Count」。所以调用点要写：

```powershell
$removed = @(Remove-ThemeSchedule)
```

而不是 `$removed = Remove-ThemeSchedule`。同理，`Get-ScheduleState` 的返回值也统一
用 `@()` 包一次，避免单条结果被当成对象。

### cmd wrapper 里不能用 `call`

全局入口 `~/.local/bin/theme-cli.cmd` **不能**用 `call` 转发参数。cmd.exe 的 `call` 内建会
**把 `/?` 当成自己的开关**，打印 CALL 的帮助然后退出 1，而不是把参数转发下去。去掉 `call`
直接调用即可——它是脚本最后一行，调用完本来也不需要返回。

### 计划任务要按位置传模式，不能用 `-Command`

计划任务的 action 是：

```
powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "<theme-cli.ps1>" light
```

`-File` 之后的参数**全部属于脚本**，不再是 powershell.exe 自己的开关。写成 `-Command light`
的话，`-Command` 会作为未知选项进到脚本里，手写解析直接报「Unknown option: -Command」，
任务退出码 1，主题**纹丝不动**。

改参数解析之前这个坑一直没暴露：旧版 param 块里正好有个 `$Command` 参数，`-Command light`
能绑定上去。

所以回归测试里「计划任务真实触发后主题确实变了」这一项不能省——任务装上了、`LastTaskResult`
看着也像那么回事，都不等于它真的会切。

### `-?` 会被 PowerShell 截走

脚本带 comment-based help 时，`-?` 由 PowerShell 的 `Get-Help` 机制在脚本体执行**之前**处理，
渲染的是注释里的帮助文本（SYNOPSIS / DESCRIPTION / EXAMPLES）。内容有效，但版式和
`theme-cli --help` 那套手写帮助不同。强行统一要拿掉 comment-based help，代价大于收益，
所以保留这个例外。

### 错误走 stderr，一行说完

`catch` 里用 `[Console]::Error.WriteLine()` 而不是 `Write-Error`，后者会连脚本路径、行号、
调用栈一起打出来，噪音比信息多：

```
theme-cli: Invalid time '25:99'. Use HH:mm in 24-hour format, for example 08:00.
```

时间格式用 `TryParseExact` 校验并给出自己的报错，而不是让 `ParseExact` 抛 .NET 的
本地化异常消息。

## 实测数据（本机 Windows 11 23H2）

| 指标 | 值 |
|---|---|
| 手动切换延迟 | ~1.0–1.4 s（含 PowerShell 启动与 `Add-Type` 编译） |
| 计划任务端到端 | ~3.7 s，`LastTaskResult = 0` |
| `Add-Type` 编译开销 | ~255 ms |
| 广播本身 | 14 ms（`SendNotifyMessage`） |

验证「主题真的生效」不能只看注册表——注册表只说明值写了。真正的判据是读 WinRT 的
`UISettings.GetColorValue(UIColorType.Background)`，那是外壳**此刻实际在用**的颜色。
实测切换后该值在 `FFFFFF`（亮）和 `000000`（暗）之间正确翻转。

## 装在哪

| 路径 | 作用 |
|---|---|
| `~/.agents/skills/theme-cli/theme-cli.ps1` | 核心实现 |
| `~/.agents/skills/theme-cli/theme-cli` | bash 入口 |
| `~/.agents/skills/theme-cli/theme-cli.cmd` | cmd 入口 |
| `~/.local/bin/theme-cli` | bash 全局入口（`~/.local/bin` 已在 PATH 里） |
| `~/.local/bin/theme-cli.cmd` | cmd 全局入口 |

`~/.local/bin` 已在用户的 PATH 中，所以 `theme-cli` 在任何终端直接可用。PowerShell 5.1 和
PowerShell 7（`pwsh`）都能跑，不需要管理员权限，不需要装任何第三方依赖。

## 几个已知限制

- **固定时间不随季节变化。** 想要真正跟随日出日落，得用 [PowerToys 的 Light Switch](https://learn.microsoft.com/en-us/windows/powertoys/light-switch)，
  它有 `Sunset to Sunrise` 模式（需开定位服务）。本 CLI 只做固定时间。
- **部分应用不会跟着变。** 老式 Win32 程序、自绘控件的程序，以及自带外观开关的浏览器和
  Office，得在各自设置里改成「跟随系统」。这不是 CLI 的 bug。
- **任务只在登录状态下生效**（见上）。
- **和别的换主题工具会打架。** 同时跑 PowerToys Light Switch 或 Auto Dark Mode 的结果是
  互相覆盖。同一时间只留一个。

## 卸载

```bash
theme-cli schedule --remove
```

然后删掉 `~/.agents/skills/theme-cli/` 和 `~/.local/bin/theme-cli{,.cmd}`。当前主题不受影响，
可以继续在 设置 > 个性化 > 颜色 里手动改。
