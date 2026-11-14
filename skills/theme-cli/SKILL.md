---
name: theme-cli
description: 切换 Windows 系统暗色/亮色主题，可以手动切换，也可以设置每天定时自动切换。任何"怎么让 Windows 定时换主题""暗色模式怎么自动开""把系统改成亮色/暗色""theme-cli 怎么用""theme-cli --help"的问题用它。Windows 已实测；macOS/Linux 未实现。
---

# 定时切换 Windows 明暗主题

Windows 11 **没有**内置的定时换主题功能。设置 > 个性化 > 颜色只有浅色/深色/自定义三档，
没有时间表；唯一自带时间表的是「夜间光照」，但它只调色温暖色，**不切换任务栏和应用的明暗**。

这个 skill 是一个 Python 包，五个函数，两种用法：手动切，或者挂每日定时任务。

```python
run_skill(id="theme-cli", cmd={"name": "light", "args": {}})   # 立刻切成亮色
run_skill(id="theme-cli", cmd={"name": "toggle", "args": {}})  # 明暗互切
run_skill(id="theme-cli", cmd={"name": "schedule", "args": {"light": "08:00", "dark": "20:00"}})
run_skill(id="theme-cli", cmd={"name": "status", "args": {}})  # 看当前主题和排程状态
```

## 函数

| 函数 | 参数 | 返回 |
|---|---|---|
| `light()` | — | 脚本退出码；外壳和已打开应用立刻重绘 |
| `dark()` | — | 同上 |
| `toggle()` | — | 同上，按当前状态翻转 |
| `status(as_json=False)` | `as_json: bool = False` 给程序读时用一个 JSON 对象 | 同上；打印当前主题与两个排程任务的状态 |
| `schedule(light=None, dark=None, show=False, remove=False)` | `light` / `dark` 是 `HH:mm`；`show=True` 只看已装的排程；`remove=True` 删掉两个任务 | 同上 |

返回值是**脚本的退出码**（`0` 成功，`1` 失败），输出就是脚本的 stdout/stderr。
`schedule` 里没给的那一半保留原时间，所以可以一次只改一半。

参数不对时（比如写了 `schedule(light="08:00")` 却从没装过暗色排程）脚本会以退出码 1
报一行 `theme-cli: ...`，照那行改就行。

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

```python
run_skill(id="theme-cli", cmd={"name": "light", "args": {}})   # 系统 + 应用都切成亮色
run_skill(id="theme-cli", cmd={"name": "dark", "args": {}})    # 系统 + 应用都切成暗色
run_skill(id="theme-cli", cmd={"name": "toggle", "args": {}})  # 按当前状态翻转
```

切换是即时的，会立刻改变任务栏和已打开应用的外观。

## 定时切换

```python
run_skill(id="theme-cli", cmd={"name": "schedule", "args": {"light": "08:00", "dark": "20:00"}})
```

装两个每日任务：`WindowsTheme-Light` 和 `WindowsTheme-Dark`。

改时间、只看其中一个、或者全删：

```python
run_skill(id="theme-cli", cmd={"name": "schedule", "args": {"light": "07:30"}})  # 只改亮色时间
run_skill(id="theme-cli", cmd={"name": "schedule", "args": {"show": True}})      # 看已装的排程
run_skill(id="theme-cli", cmd={"name": "schedule", "args": {"remove": True}})    # 删掉两个任务
```

排程任务用 **Interactive 登录类型**（`RunLevel Limited`），这是刻意的：主题注册表在 `HKCU`
下，必须以登录用户身份运行才能改到自己的账户。如果改成「不管用户是否登录都运行」，任务会
写进 SYSTEM 账户的 HKCU，**对当前用户毫无效果**——这是这类方案最常见的失败原因。

副作用：任务只在**用户已登录**时触发。这是主题机制本身的约束，不是配置失误。

## 看状态

```python
run_skill(id="theme-cli", cmd={"name": "status", "args": {}})
```

```
Current theme : Dark (system Dark, apps Dark)

Schedule:
  Light  Ready  at 08:00  next 2026-10-30 08:00  (last result 0)
  Dark   Ready  at 20:00  next 2026-10-29 20:00  (last result 0)
```

`last result 0` 表示上次执行成功。其他值说明那次运行有问题。

给程序读的话传 `as_json=True`：

```python
run_skill(id="theme-cli", cmd={"name": "status", "args": {"as_json": True}})
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

### 脚本自带的手写帮助仍然可用

包里的函数是入口，但脚本自己的手写帮助没拆掉：直接
`powershell -File <本 skill 目录>/theme-cli.ps1 --help` 仍然给出命令表与示例。
脚本带 comment-based help，所以 `-?` 由 PowerShell 的 `Get-Help` 在脚本体执行**之前**处理，
渲染注释里的 SYNOPSIS / DESCRIPTION / EXAMPLES，版式与手写帮助不同；两者都保留。

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
| `<本 skill 目录>/__init__.py` | 入口：`light` / `dark` / `toggle` / `status` / `schedule` |
| `<本 skill 目录>/theme-cli.ps1` | 实现：注册表、广播、计划任务 |

不需要管理员权限，不需要装任何第三方依赖。PowerShell 5.1 和 PowerShell 7（`pwsh`）都能跑；
包装函数优先用 `pwsh`（在这里它启动更快），没有则退回 `powershell`。

## 几个已知限制

- **固定时间不随季节变化。** 想要真正跟随日出日落，得用 [PowerToys 的 Light Switch](https://learn.microsoft.com/en-us/windows/powertoys/light-switch)，
  它有 `Sunset to Sunrise` 模式（需开定位服务）。本 CLI 只做固定时间。
- **部分应用不会跟着变。** 老式 Win32 程序、自绘控件的程序，以及自带外观开关的浏览器和
  Office，得在各自设置里改成「跟随系统」。这不是 CLI 的 bug。
- **任务只在登录状态下生效**（见上）。
- **和别的换主题工具会打架。** 同时跑 PowerToys Light Switch 或 Auto Dark Mode 的结果是
  互相覆盖。同一时间只留一个。

## 卸载

```python
run_skill(id="theme-cli", cmd={"name": "schedule", "args": {"remove": True}})
```

然后删掉本 skill 的目录。当前主题不受影响，可以继续在 设置 > 个性化 > 颜色 里手动改。
