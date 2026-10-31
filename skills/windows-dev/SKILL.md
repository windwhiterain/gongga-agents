---
name: windows-dev
description: Windows 上 agentic 开发的四类环境坑的诊断与治理——行尾符（CRLF/LF）、字符编码（GBK/UTF-8/BOM）、文件路径（260 长路径/保留设备名/大小写冲突）、系统环境（执行策略/长路径开关/Git autocrlf）。当 agent 在 Windows 上批量写文件或跑脚本后出错、shell 脚本报 `\r: command not found`、git diff 出现满屏假变更、中文变成乱码、或读写某个路径失败时使用。默认 doctor 只做只读体检；eol 与 encoding 可按要求改写文件。
---

# Windows agentic 开发环境体检与治理

给 agent 用的 Windows 环境诊断工具。跑 `windows-dev.ps1`（与本文件同目录）。

Windows 上 agent 写脚本时遇到的"莫名其妙的错误"，绝大多数来自四类问题，它们的
共同特点是**不报错，或者报一个跟原因毫无关系的错**：

| | 典型症状 |
| --- | --- |
| **eol** | `.sh` 报 `\r: command not found`；`git diff` 整个文件全是变更；字符串匹配全部落空 |
| **encoding** | 中文变问号或 `锟斤拷`；Python 报 `SyntaxError: invalid character '﻿'`；JSON 解析在第一个字符就失败 |
| **path** | `Remove-Item` / `git checkout` 报路径太长；两个只差大小写的文件互相覆盖 |
| **env** | `.ps1` 跑不起来；长路径读写失败；中文路径显示为 `\344\275\240` |

## 何时用 / 不要用来干什么

- 用：agent 在 Windows 上批量生成/修改文件之后、换新机器或新仓库开工前、
  以及上面表格里任一症状出现时。新项目初始化用 `eol guard` 一次性把规则钉死。
- 不要用：当代码 linter 或格式化工具用（它只管行尾和编码，不管代码风格）；
  不要指望它处理二进制文件（会主动跳过）；不要用来批量重命名文件。
- **`env` 永远只读**，它只打印修复命令，不碰注册表也不改 `git config`。改这两样
  的影响是全机器/全局的，必须用户自己执行。

## 执行流程（agent 照做）

1. **先 doctor**（只读，不改任何文件）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\gongga-agents\skills\windows-dev\windows-dev.ps1" doctor <目录>
   ```

   四个维度全扫一遍，出一份汇总报告，末尾给出对应的修复命令。

2. 看报告，**只有症状对得上才 fix**。fix 会直接覆盖原文件、不留备份，所以：
   - 有 git 的目录：**fix 前确保工作区干净**，出错靠 git 兜底；
   - 没有 git 的目录：先自己复制一份，或者先 `scan` 看清涉及哪些文件再决定。

   ```powershell
   # 行尾符
   ...\windows-dev.ps1 eol <目录> -Action fix
   # 编码
   ...\windows-dev.ps1 encoding <目录> -Action fix
   ```

3. **新项目先 guard 再动手**，这样之后所有文件一出生就合规：

   ```powershell
   ...\windows-dev.ps1 eol <目录> -Action guard
   ```

   **幂等的「确保」动作，不是「生成」动作**：文件不存在就创建（`.vscode/`、
   `.zed/` 目录也会一并建）；已存在就修正——把错误的 eol 规则改对
   （`*.sh eol=crlf` → `eol=lf`，`"files.eol": "\r\n"` → `"\n"`）、把缺失的补上，
   你自己写的其他规则（`providers.toml linguist-generated`、`editor.fontSize` 之类）
   原样保留，注释也不动。再跑一次会报「已合规，无需改动」。
   存量文件还要 `git add --renormalize .` 才会真正生效。

   一共四个文件，覆盖四个层面：

   | 文件 | 管的层面 | 谁读 |
   | --- | --- | --- |
   | `.gitattributes` | 仓库里存什么 | Git |
   | `.editorconfig` | 跨编辑器约定 | 装了 EditorConfig 插件的编辑器 |
   | `.vscode/settings.json` | 编辑器自己的保存行为 | VS Code / Cursor / Trae / Windsurf / CodeBuddy |
   | `.zed/settings.json` | 同上 | Zed |

4. 参数形式：`windows-dev.ps1 <命令> [一个或多个目录] [-Action scan|fix|guard] [-Limit N]`。
   命令是 `doctor`（默认）/`eol`/`encoding`/`path`/`env`。目录是位置参数，可以传多个。
   **`path` 与 `env` 永远只读**，传 `-Action fix` 会被忽略并提示。

## 覆盖范围

### eol（行尾符）

扫描所有文本文件，按行尾形态分成 `lf` / `crlf` / `mixed` / `cr` / `none` 并统计。
目标行尾的判定优先级：

1. 目录里有 `.gitattributes` → 解析其中的 `eol=` 规则，**后出现者优先**（与 Git 语义一致）；
2. 没有 → 兜底：`.bat` / `.cmd` 保持 CRLF，其余一律 LF。

`.bat` / `.cmd` 必须 CRLF：cmd.exe 对多行括号块和 `goto` 的解析绑死在 CRLF 上，
LF 版本在部分机器上会静默执行错行。

### encoding（字符编码）

识别 `utf8` / `utf8-bom` / `utf16-le` / `utf16-be` / `utf32-le` / `utf32-be` /
`non-utf8` / `empty`。fix 时统一转成 UTF-8，并顺手把 CRLF 归一成 LF。

**例外**：`.ps1` / `.psm1` / `.psd1` 会**保留** UTF-8 BOM。Windows PowerShell 5.1
读无 BOM 的 `.ps1` 时按系统 ANSI(936) 解码，去掉 BOM 等于让中文注释和字符串全变乱码。

`non-utf8` 按代码页 936（GBK）解码——这是中文 Windows 的默认 ANSI 代码页。

### path（文件路径）

检测：超过 260 字符（MAX_PATH）、非法字符 `<>:"/\|?*` 与控制字符、保留设备名
（`CON`/`NUL`/`COM1`…）、结尾空格或点、名字里有空格。以及 **Git 索引里的大小写冲突**。

大小写冲突只查 Git 索引，不查工作区——见下方「不显然的地方」。

### env（环境体检）

PowerShell 版本与执行策略、控制台输出编码、长路径开关（`LongPathsEnabled`）、
系统 ANSI 代码页（是否开了全局 UTF-8）、开发者模式（符号链接权限）、
Git 的 `core.autocrlf` / `safecrlf` / `longpaths` / `quotepath` / `ignorecase`，
并对没设好的项给出具体命令。

## 边界（设计决策，不要擅自改）

| 碰 | 永不碰 |
| --- | --- |
| 已知文本后缀与已知无后缀名（Makefile/Dockerfile/LICENSE…）的文件 | 二进制文件（嗅探到 NUL 即跳过） |
| 四个配置文件里 guard 负责的那几条规则（改错、补缺） | 这些文件里你自己写的其他规则与注释（原样保留，不覆盖不删除） |
| 单文件 ≤ 20MB | 更大的文件（跳过并在报告里计数） |
| 报告里相对路径 | 注册表、`git config`、任何系统级设置（只打印命令） |

默认跳过的目录：`.git`、`.svn`、`.hg`、`node_modules`、`vendor`、`target`、`dist`、
`build`、`bin`、`obj`、`out`、`.next`、`.venv`、`__pycache__`、`.gradle`、`.terraform`、
`.idea`、`.vs`、`coverage` 等。改第三方和生成物的行尾没有意义，量还极大。

## 实现上几个不显然的地方

改脚本前先读。

### 无 BOM 的 UTF-16 会被严格 UTF-8 校验"合法"放过

ASCII 文本编码成 UTF-16 后每隔一个字节就是 `0x00`。而 `0x00` 同时是一个完全合法的
UTF-8 字符（U+0000），所以严格 UTF-8 解码器会把整段读过去、判定为 `utf8`——内容是
`"u\0n\0i\0"` 这种东西却没人报警。PowerShell 5.1 的 `>` 与 `Out-File` 产出的正是这类
文件。必须在 UTF-8 校验**之前**用「偶/奇位 NUL 分布」的启发式拦下来
（`Test-BareUtf16Signature`）。

### guard 是幂等的，所以「剔除待替换属性」必须写全

guard 每跑一次都要得到同样的结果，否则第一次改完、第二次又改，文件内容会越跑越脏。
实现上，重写一条已存在的规则行时，要先把目标属性之外的旧属性剔干净再加目标值——
而 `text` 在 `.gitattributes` 里有三种写法：`text`、`text=auto`、`-text`。
只剔除裸 `text` 的话，`* text=auto eol=lf` 会被当成"其他属性"留下 `text=auto`，
于是每跑一次就重复追加一个，行会变成 `* text=auto eol=lf text=auto text=auto`。
`Update-GitAttributesRules` 里三种写法都要剔除。

### PSCustomObject 的 `.Count` 是 `$null`，不是 1

PowerShell 函数返回数组时会把元素枚举到管道，只有一个元素时调用方拿到的是那个
元素本身、不是数组。多数类型无所谓（标量的 `.Count` 兜底成 1），但
`[pscustomobject]` 有自己的属性查找，访问不存在的 `.Count` 得到 `$null`：

```powershell
([pscustomobject]@{A=1}).Count   # $null  —— 坑
(Get-Item win.ini).Count         # 1
'abc'.Count                      # 1
```

后果很隐蔽：`Get-GitAttributesEolRules` 返回「只有一条 eol 规则」这种最常见的
输入时，调用方的 `$rules.Count -gt 0` 为假，整套规则被静默忽略、退回兜底逻辑
（看着还能跑，但 `*.bat eol=crlf` 这类规则就失效了）。凡是返回 PSCustomObject
数组的函数，调用处一律用 `@()` 包起来。

### 编辑器配置不能用 JSON 解析器往返

VS Code 与 Zed 的 settings.json 是 **JSONC**（允许注释和尾随逗号）。PS 5.1 的
`ConvertFrom-Json` 遇到注释直接报错；就算解析成功，`ConvertTo-Json` 也会重排键顺序、
改掉缩进、丢掉注释——等于把用户整理过的配置文件洗一遍。`Update-JsonSettings`
因此做文本级合并：正则定位目标键换值，缺失的插到根对象开头（跳过开头的注释块，
沿用文件原有的缩进单位）。

插入必须**倒序**（每次都插在最前面），这样"后面还有没有内容"这个判断永远成立，
逗号才不会错位。正序插会让第一条漏掉逗号、最后一条多一个尾随逗号——JSONC 容忍，
但没理由制造。

Zed 的键名别写错：`line_ending` 取值是 `detect` / `prefer_lf` / `prefer_crlf` /
`enforce_lf` / `enforce_crlf`，要强制 LF 是 `enforce_lf`。网上流传的
`"end_of_line": "lf"` 不是官方键名。另据 Zed 官方文档，`.editorconfig` 的
`end_of_line` 会**覆盖** `line_ending`，所以两份都写不冲突。

### 写文件一律 `WriteAllBytes` / 显式 `UTF8Encoding`

`[IO.File]::WriteAllText(path, text)` 在 .NET Framework 下默认**带 BOM**，而且可能重新
引入 CRLF。用 `WriteAllBytes` + 自己构造的 `UTF8Encoding($true|$false)` 才能精确控制
「要不要 BOM」和「行尾是啥」。

### 大小写冲突只能查 Git 索引

Windows 文件系统大小写不敏感，同一目录下压根存不下 `Foo.ts` 和 `foo.ts` 两个文件——
它们要么检出时就互相覆盖了，要么没检出来。所以工作区扫描永远扫不到这个冲突，
扫了也是白扫（曾经写过，是死代码，已删）。真正的证据在 `git ls-files` 里：索引是纯
文本，可以老老实实记着两个名字。

### UTF-16 文件在 eol 通道被当成二进制跳过，这是故意的

UTF-16 含大量 NUL，会被二进制嗅探命中。但 UTF-16 不该按字节改行尾——它的行尾是
`0D 00 0A 00`，改了就废。这类文件应该走 `encoding` 通道整体转成 UTF-8，转的过程中
顺带就把行尾归一了。`encoding` 通道因此**不能**用 `Test-LooksBinary` 做前置过滤。

### 这个脚本文件本身必须是 UTF-8 BOM + LF

PowerShell 5.1 靠 BOM 判断 `.ps1` 的编码。本文件里全是中文注释和输出字符串，
一旦被谁去掉 BOM 或转成 CRLF，在 PS 5.1 上打开就是乱码（或行为异常）。
改完这个文件后确认：开头三字节是 `EF BB BF`，全文无 `CRLF`。

### 报告用相对路径

绝对路径在 120 列的控制台里必然折行，一折行就看不出是哪个文件。唯一例外是「超过
260 字符」那一项——它必须显示绝对长度，否则看不出长在哪，所以打印成
`263 字符  sub\file.txt`。

## 环境要求

Windows 10/11 + 自带 Windows PowerShell 5.1（无需安装任何东西），pwsh 7+ 同样可跑。
非 Windows 直接退出。Git 只在「读 `.gitattributes` 规则」和「查索引大小写冲突」时用到，
没有 Git 会自动降级到兜底规则，不影响其他功能。
