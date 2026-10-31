---
name: clean-disk
description: Windows 全量磁盘清理，一次清掉临时文件、浏览器/应用/编辑器缓存、崩溃转储、Windows 更新残留、DISM 组件、包管理器缓存和所有盘回收站；加 -ProjectRoot 可顺带清理指定代码根内的 target/node_modules 等构建产物。用户说"清理 C 盘/磁盘空间不足/系统垃圾/清理缓存/clean disk/释放磁盘空间"时使用。只删可自动重建的内容，不用于删除个人文件、聊天记录或卸载软件。
---

# Windows 全量磁盘清理

给用户跑 `scripts/../clean-disk.ps1`（与本文件同目录）。脚本**没有清理项开关**——
所有"删掉后会自动重建"的东西默认全清；`-DryRun` 是安全预览，不是开关。
另一个参数 `-ProjectRoot` 是**范围**参数，见下文「项目构建产物」。

## 何时用 / 不要用来干什么

- 用：C 盘/系统盘红了、Temp 几十万小文件、包管理器缓存膨胀、更新残留、想回收空间；
  用户明确给出代码根（如 `C:\resource`）并要求清 Rust `target`/`node_modules` 时，
  走 `-ProjectRoot`。
- 不要用：删 Downloads 里的安装包、清微信/QQ/Telegram 聊天文件、卸载软件、
  清 Docker 卷。这些是用户数据或不可重建的东西，脚本刻意不碰。
- **不要在没有用户明确给出代码根的情况下自作主张全盘扫项目产物**；更不要把
  `C:\`、用户配置文件根传给 `-ProjectRoot`（会被安全闸拒绝）。

## 执行流程（agent 照做）

1. **先 DryRun**，把各项占用汇报给用户（不删除、不提权、不弹 UAC）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\gongga-agents\skills\clean-disk\clean-disk.ps1" -DryRun
   ```

2. 用户确认（或本来就直接要求清理）后实跑。实跑会自动弹**一次 UAC**：
   同意 → 用户级 + 系统级全清；取消 → 只清用户级，系统级项目打印跳过，不算失败。

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\gongga-agents\skills\clean-disk\clean-disk.ps1"
   ```

   提权后的窗口带 `-NoExit`，跑完留着给用户看汇总。DISM 组件清理可能要几分钟，
   提前告诉用户这是正常等待，不要中途关。

3. 跑完汇报：每块盘的**可用空间前后差值**（权威释放量）、被跳过项、日志路径。
   日志固定在 `%LOCALAPPDATA%\DiskCleanup\logs\clean-<时间戳>.log`。

4. 浏览器/IDE/Teams/Slack/Discord/JetBrains **运行中时对应缓存自动跳过**。
   提示用户关掉后重跑即可，**不要强杀进程**。

## 项目构建产物（-ProjectRoot）

默认不扫任何项目目录。用户明确给出一个或多个**代码根**时才启用，识别并删除：

| 产物 | 识别条件（目录名 + 兄弟标记文件） |
| --- | --- |
| Rust `target` | 兄弟有 `Cargo.toml`（工作区根，成员 crate 不会重复命中） |
| Maven `target` | 兄弟有 `pom.xml` |
| `node_modules` | 名字本身即标记，命中即删且不再下钻（嵌套依赖不会重复统计） |
| .NET `bin`/`obj` | 兄弟有 `*.csproj`/`*.fsproj`/`*.vbproj`/`*.vcxproj` |
| `build` | 兄弟有 `build.gradle*`/`settings.gradle*`、`pubspec.yaml` 或 `CMakeLists.txt` |
| `.gradle`（项目级） | 兄弟有 Gradle 标记 |
| `cmake-build-*` | JetBrains CMake 输出目录 |
| Python 缓存 | `__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`.tox`、`.nox` |
| `.venv` | 兄弟有 pyproject.toml/requirements*.txt/setup.py 等工程标记 |
| Elixir `_build` | 兄弟有 `mix.exs` |

刻意**不**碰：`dist`/`out`/`.next` 等可能被提交进仓库的产物、`venv`（无点号，
名字太泛）、Unity 的 `Library`、全局 `~/.cargo`/`~/.gradle`（已在常规清理覆盖）。

```powershell
# 先 DryRun（项目产物清理不需要管理员，DryRun 同样不提权）
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\gongga-agents\skills\clean-disk\clean-disk.ps1" -DryRun -ProjectRoot C:\resource
# 多个根用逗号分隔；实跑时 -ProjectRoot 会经 UAC 提权原样透传到新窗口
...\clean-disk.ps1 -ProjectRoot C:\resource,D:\code
```

向用户说明代价：Rust/`.venv`/`node_modules` 删除后下次要重新编译或重新下载依赖。
如果用户只是问"有哪些 Rust 仓库"而不是要清理，用仓库发现脚本回答（找 `Cargo.toml`
且排除 `.cargo`/`target`/`.git`、见标记即停止下钻），不要直接跑清理。

## 清理边界（设计决策，不要擅自改）

| 清（可重建） | 永不碰 |
| --- | --- |
| Temp/TMP、Windows\Temp、Prefetch | Downloads/文档/桌面等个人文件 |
| 浏览器缓存白名单（Cache/Code Cache/GPUCache/Shader/CacheStorage 等） | 浏览器 Preferences/密码/Cookies/History/Local Storage/扩展 |
| 包管理器缓存（npm/pnpm/yarn/bun/uv/pip/NuGet/go/conda/composer/pub/cargo/gradle/maven/deno/poetry） | 项目内 `target`/`node_modules`/`.gradle`（**默认不扫**；仅当用户显式传 `-ProjectRoot` 时在指定根内按标记删除） |
| Windows 更新下载缓存、Delivery Optimization、DISM StartComponentCleanup | `Windows\Installer`、`ProgramData\Package Cache`（删了无法修复/卸载软件） |
| 内存/小型转储、WER、CBS/WindowsUpdate/DISM 日志 | 事件日志（有诊断/审计价值） |
| `$WINDOWS.~BT/~WS`、`Windows.old` | 系统还原点/卷影副本（回退能力） |
| 缩略图/图标/着色器/RDP 位图缓存 | `hiberfil.sys`/`pagefile.sys`（改变休眠/虚拟内存行为） |
| Docker build cache、停止的容器与悬空镜像（`prune -af`） | Docker **卷**（可能是数据库数据） |
| 所有固定盘回收站 | 微信/QQ/Telegram 等聊天记录与接收文件 |

用户如果明确要求越过右列（例如关休眠、清还原点、删 Windows.old 之外的东西），
单独说明后果并让他显式确认，不要改脚本默认行为。

## 覆盖面

- **浏览器**：Edge/Chrome/Brave/Vivaldi/Yandex/Whale（动态枚举 `Default`/`Profile *`/
  `System Profile`/`Guest Profile`，含 Beta/Dev 通道）、Opera（直接 profile 根）、
  Firefox（Roaming+Local 双根，cache2/startupCache/shader-cache/OfflineCache）。
- **Electron 应用**：Teams 经典版与 2.x（`MSTeams_8wekyb3d8bbwe`）、Slack、Discord。
- **编辑器**：VS Code、Trae、CodeBuddy、Cursor、Windsurf（Cache/CachedData/Code Cache/
  GPUCache/logs）、Zed（只 logs）、JetBrains 全家（caches/log/tmp，IDE 运行中跳过）。
- **系统**：Temp、Prefetch、WER（用户+系统）、MEMORY.DMP/Minidump/LiveKernelReports、
  CBS/WindowsUpdate/DISM 日志、SoftwareDistribution\Download（停 wuauserv/bits）、
  Delivery Optimization（优先用 `Delete-DeliveryOptimizationCache` cmdlet）、
  FontCache（停服务后清）、升级残留目录、DISM StartComponentCleanup、全固定盘回收站。

## 实现上几个不显然的地方

改脚本前先读。

### 删除靠 robocopy 空目录镜像，不是 Remove-Item

`Clear-Folder` 用一个空目录 `robocopy /MIR /XJ /R:0 /W:0` 清空目标内容、保留目标根。
原因：Temp 里常见 260+ 字符长路径、奇葩 ACL 和 junction 循环（AppData 遗留），
`Remove-Item -Recurse` 会半途报错中断；robocopy 原生支持长路径，`/XJ` 不跟重解析点，
占用文件直接跳过、零重试不卡脚本。退出码 0–7 都算成功，≥8 才告警。

### 释放量不能"先量后删就记账"

原同类脚本的典型错误：删除前量一次大小就当释放量——被占用没删掉的文件也被计入。
本脚本每项都算 `before - after`（残留即占用文件），最终再以**固定盘可用空间前后差值**
作为权威数字汇总，两项都打印。包管理器 CLI 和 DISM 自己释放的量只体现在盘差值里。

### 受保护路径是安全闸，不是摆设

`Assert-Cleanable` 拒绝盘符根、UNC、以及 USERPROFILE/LOCALAPPDATA/APPDATA/
ProgramData/Windows/Program Files/Downloads 等**精确匹配**路径。清的是它们的
**子目录**（如 `Windows\Temp`），所以精确匹配而非前缀匹配。新增清理目标时
不要动这张表，也不要传变量拼出来的未校验路径。

### DryRun 不提权

DryRun 下系统级目录大小可能因 ACL 统计不全，但保证零副作用、零 UAC，适合先看；
精确数字以实跑汇总为准。

### DISM 不用 ResetBase

只跑 `/StartComponentCleanup`（等价磁盘清理里的 Windows 更新清理）。
`/ResetBase` 会让已装更新无法卸载，属于不可逆系统状态变更，超出"缓存"范畴。

### 提权窗口用 -NoExit，参数原样透传

`Start-Process -Verb RunAs` 必然开新窗口，不留住的话汇总一闪而过。宿主用
`(Get-Process -Id $PID).Path` 取，所以从 powershell.exe 和 pwsh.exe 启动都能
正确自我提权。脚本只有一个 switch，转发不会遇到参数拼接问题。

## 环境要求

Windows 10/11 + 自带 Windows PowerShell 5.1（无需安装任何东西），pwsh 7+ 同样可跑。
非 Windows 不适用。
