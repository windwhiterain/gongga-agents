<#
.SYNOPSIS
    Windows 全量磁盘清理脚本（通用版，无清理项开关：可安全清理的全部清理）

.DESCRIPTION
    唯一参数 -DryRun 是安全预览模式（不删除、不提权、不动服务），不是清理项开关。
    -ProjectRoot <路径数组> 是唯一的"范围"参数：默认关闭；显式指定一个或多个代码
    根目录后，扫描并删除其中的构建产物（Rust/Maven target、node_modules、.NET
    bin/obj、Gradle/Flutter/CMake 的 build、Python 缓存/.venv 等），代价是下次
    需要重新编译/重新下载依赖。
    实际执行时：
      - 自动弹一次 UAC 提权（取消则继续用户级清理，系统级项目标记跳过）
      - 全程写日志到 %LOCALAPPDATA%\DiskCleanup\logs\
      - 真实删除用 robocopy 空目录镜像（/MIR /XJ）：长路径友好、不跟重解析点、
        被占用文件自动保留；每项按"清理前 - 残留"计算实际释放量
      - 结束时按固定盘可用空间前后差值给出每块盘的真实释放量

    清理范围（全部默认执行）：
      用户级  Temp/TMP、CrashDumps、SquirrelTemp、RailCrashReport、用户 WER
      着色器  D3DSCache、NVIDIA/AMD/Intel 着色器缓存
      浏览器  Edge/Chrome/Brave/Vivaldi/Opera/Yandex/Whale（动态枚举所有 Profile，
              仅删缓存白名单）、Firefox（cache2/startupCache/shader-cache）
      应用    Teams 经典版/2.x、Slack、Discord（仅缓存白名单）
      编辑器  VS Code/Trae/CodeBuddy/Cursor/Windsurf（Cache/CachedData/GPUCache/logs）、
              Zed logs、JetBrains 全家（caches/log/tmp）
      外壳    IE 遗留 INetCache/WebCache、资源管理器缩略图图标缓存、RDP 位图缓存
      包管理  npm/pnpm/yarn/bun/uv/pip/dotnet(NuGet)/go/conda/composer/dart(pub)
              官方清理命令；cargo/gradle/maven/poetry/pip/deno 等已知缓存目录
      Docker  builder prune -af、system prune -af（不删卷）
      系统级  Windows\Temp、Prefetch、系统 WER、MEMORY.DMP/Minidump/LiveKernelReports、
              CBS/WindowsUpdate/DISM 日志、Windows 更新下载缓存（停 wuauserv/bits 后清）、
              Delivery Optimization 缓存、字体缓存（停 FontCache 后清）、
              $WINDOWS.~BT/~WS、Windows.old、DISM StartComponentCleanup
      收尾    清空所有固定盘回收站

    永不清理（不是"可选"，是边界）：
      个人文件（Downloads/文档/桌面）、浏览器配置/密码/历史/Cookie/Local Storage、
      微信/QQ/Telegram 等聊天数据、Windows\Installer、ProgramData\Package Cache、
      hiberfil.sys/pagefile.sys、系统还原点与卷影副本、事件日志、Docker 卷、
      各项目内的 target/node_modules 等构建产物（默认不扫；只有显式传入
      -ProjectRoot 时才在指定代码根内删除，不做通用全盘扫描）。

    浏览器、编辑器、Teams/Slack/Discord 正在运行时对应项目自动跳过
    （运行中删缓存可能损坏配置），关闭后重跑即可，不会强杀进程。

.PARAMETER DryRun
    预览模式：只统计、列计划、检查占用情况，不做任何删除/服务变更/提权。

.PARAMETER ProjectRoot
    一个或多个代码根目录（逗号分隔或多次传入）。在这些目录内扫描并删除项目
    构建产物：Rust/Maven target、node_modules、.NET bin/obj、Gradle/Flutter/
    CMake 的 build、Python 缓存与 .venv、Elixir _build 等。默认不扫。
    拒绝盘符根、用户配置文件根等受保护路径。发现产物目录即删除且不再下钻，
    因此嵌套的 node_modules/target 不会被重复统计。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\clean-disk.ps1 -DryRun
    先预览各项占用与将执行的命令。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\clean-disk.ps1
    实际执行全量清理（会弹一次 UAC）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\clean-disk.ps1 -DryRun -ProjectRoot C:\resource,D:\code
    只预览：在 C:\resource 和 D:\code 下找构建产物（配合全量清理的其他预览项一起输出）。
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string[]]$ProjectRoot
)

$ErrorActionPreference = 'SilentlyContinue'

# ---------------- 基本信息 / 自动提权 ----------------

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# DryRun 绝不提权；实际清理需要系统级权限，自动提权一次。
# 用 -NoExit 让提升后的窗口保留汇总输出；用户取消 UAC 则降级继续用户级清理。
if (-not $DryRun -and -not $isAdmin -and $PSCommandPath) {
    Write-Host '全量清理需要管理员权限，正在请求提权（请在 UAC 窗口点"是"；点"否"则只清用户级）...' -ForegroundColor Yellow
    try {
        $hostExe = (Get-Process -Id $PID).Path
        $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$PSCommandPath`"")
        if ($ProjectRoot -and $ProjectRoot.Count -gt 0) {
            # Start-Process 对数组元素按空格拼接，含空格的路径要各自带引号
            $argList += '-ProjectRoot'
            foreach ($r in $ProjectRoot) { $argList += "`"$r`"" }
        }
        Start-Process -FilePath $hostExe -WorkingDirectory (Get-Location).Path `
            -Verb RunAs -Wait -ArgumentList $argList | Out-Null
        exit
    } catch {
        Write-Host "提权被取消或失败：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host '继续以普通权限执行（系统级项目将跳过）。' -ForegroundColor Yellow
        $isAdmin = $false
    }
}

# ---------------- 日志 ----------------

$logDir = Join-Path $env:LOCALAPPDATA 'DiskCleanup\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ("clean-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
Start-Transcript -Path $logPath -Append | Out-Null

$script:totalFreed = [long]0
$script:skippedLocked = 0
$script:warnCount = 0

# 受保护路径（精确匹配，不允许清空这些目录本身；其子目录如 Windows\Temp 仍可清）
$script:protectedPaths = @(
    $env:USERPROFILE,
    $env:LOCALAPPDATA,
    $env:APPDATA,
    $env:ProgramData,
    $env:SystemRoot,
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)},
    $env:PUBLIC,
    "$env:SystemDrive\",
    (Join-Path $env:USERPROFILE 'Downloads'),
    (Join-Path $env:USERPROFILE 'Documents'),
    (Join-Path $env:USERPROFILE 'Desktop')
) | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\').ToLowerInvariant() }

# robocopy 镜像用的空源目录（放在 LOCALAPPDATA 下，不在任何被清目录内）
$script:emptyDir = Join-Path $env:LOCALAPPDATA ("DiskCleanup\empty-{0}" -f $PID)
New-Item -ItemType Directory -Force -Path $script:emptyDir | Out-Null

# ---------------- 工具函数 ----------------

function Format-Size {
    param([long]$Bytes)
    if ($Bytes -ge 1TB) { return ("{0:N2} TB" -f ($Bytes / 1TB)) }
    if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N2} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N2} KB" -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "==================== $Title ====================" -ForegroundColor Green
}

function Write-Sub {
    param([string]$Text)
    Write-Host ""
    Write-Host "-- $Text --" -ForegroundColor DarkCyan
}

function Test-ProcessRunning {
    param([string[]]$Names)
    foreach ($n in $Names) {
        if (Get-Process -Name $n -ErrorAction SilentlyContinue) { return $true }
    }
    return $false
}

function Get-FolderSize {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return [long]0 }
    $sum = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
        Measure-Object -Property Length -Sum
    if ($null -eq $sum -or $null -eq $sum.Sum) { return [long]0 }
    return [long]$sum.Sum
}

function Assert-Cleanable {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -notmatch '^[A-Za-z]:\\.+') {
        throw "拒绝清理：仅支持本地盘符绝对路径 -> $Path"
    }
    $low = $full.ToLowerInvariant()
    foreach ($p in $script:protectedPaths) {
        if ($low -eq $p) { throw "拒绝清理：受保护路径 -> $Path" }
    }
}

function Clear-Folder {
    <# 清空目录内容（保留目录本身），返回真实释放字节数。#>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host ("  [跳过] {0} - 不存在" -f $Label) -ForegroundColor DarkGray
        return
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) {
        Write-Host ("  [警告] {0} - 不是目录，跳过" -f $Label) -ForegroundColor Red
        $script:warnCount++
        return
    }
    $full = $item.FullName
    try { Assert-Cleanable $full } catch {
        Write-Host ("  [拒绝] {0} - {1}" -f $Label, $_.Exception.Message) -ForegroundColor Red
        $script:warnCount++
        return
    }
    $before = Get-FolderSize $full
    if ($before -eq 0) {
        Write-Host ("  [空]   {0}" -f $Label) -ForegroundColor DarkGray
        return
    }
    if ($DryRun) {
        Write-Host ("  [预览] {0} = {1}" -f $Label, (Format-Size $before)) -ForegroundColor Yellow
        return
    }
    # 空目录镜像：长路径友好；/XJ 排除重解析点避免 junction 循环；占用文件保留不重试
    $null = & robocopy.exe $script:emptyDir $full /MIR /XJ /R:0 /W:0 /NFL /NDL /NJH /NJS /NP /MT:8
    $rc = $LASTEXITCODE
    $after = Get-FolderSize $full
    $freed = [long]($before - $after)
    if ($freed -lt 0) { $freed = 0 }
    $script:totalFreed += $freed
    if ($after -gt 0) {
        $script:skippedLocked++
        Write-Host ("  [部分] {0} - 释放 {1}，残留 {2}（文件被占用）" -f $Label, (Format-Size $freed), (Format-Size $after)) -ForegroundColor Cyan
    } else {
        Write-Host ("  [完成] {0} - {1}" -f $Label, (Format-Size $freed)) -ForegroundColor Cyan
    }
    if ($rc -ge 8) {
        Write-Host ("         robocopy 退出码 {0}，部分项目可能因权限不足未清理" -f $rc) -ForegroundColor Red
        $script:warnCount++
    }
}

function Clear-ChildFolders {
    <# 清空某目录下每个子目录的内容（保留根目录散落文件，如 deno_history.txt）。#>
    param([string]$Path, [string]$LabelPrefix)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host ("  [跳过] {0} - 不存在" -f $LabelPrefix) -ForegroundColor DarkGray
        return
    }
    Get-ChildItem -LiteralPath $Path -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
        Clear-Folder -Path $_.FullName -Label ("$LabelPrefix\$($_.Name)")
    }
}

function Remove-FilesByPattern {
    param([string]$Folder, [string]$Pattern, [string]$Label)
    if (-not (Test-Path -LiteralPath $Folder)) {
        Write-Host ("  [跳过] {0} - 不存在" -f $Label) -ForegroundColor DarkGray
        return
    }
    $files = @(Get-ChildItem -LiteralPath $Folder -Filter $Pattern -File -Force -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) {
        Write-Host ("  [空]   {0}" -f $Label) -ForegroundColor DarkGray
        return
    }
    [long]$bytes = ($files | Measure-Object -Property Length -Sum).Sum
    if ($DryRun) {
        Write-Host ("  [预览] {0} = {1}（{2} 个文件）" -f $Label, (Format-Size $bytes), $files.Count) -ForegroundColor Yellow
        return
    }
    foreach ($f in $files) { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue }
    $left = @(Get-ChildItem -LiteralPath $Folder -Filter $Pattern -File -Force -ErrorAction SilentlyContinue)
    [long]$leftBytes = ($left | Measure-Object -Property Length -Sum).Sum
    $freed = $bytes - $leftBytes
    $script:totalFreed += $freed
    Write-Host ("  [完成] {0} - {1}" -f $Label, (Format-Size $freed)) -ForegroundColor Cyan
}

function Invoke-CacheCommand {
    <# 调用包管理器自带清理命令。#>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Host ("  [跳过] {0} - 未安装" -f $Label) -ForegroundColor DarkGray
        return
    }
    if ($DryRun) {
        Write-Host ("  [预览] {0} - 将执行: {1} {2}" -f $Label, $Exe, ($Arguments -join ' ')) -ForegroundColor Yellow
        return
    }
    Write-Host ("  [执行] {0} - {1} {2} ..." -f $Label, $Exe, ($Arguments -join ' ')) -ForegroundColor Cyan
    try {
        & $cmd.Source @Arguments 2>&1 | Out-Null
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            Write-Host ("         退出码 {0}（不影响后续清理）" -f $code) -ForegroundColor Yellow
            $script:warnCount++
        }
    } catch {
        Write-Host ("         执行失败: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
        $script:warnCount++
    }
}

function Resolve-GlobRoots {
    param([string[]]$Patterns)
    foreach ($pat in $Patterns) {
        Get-Item -Path $pat -ErrorAction SilentlyContinue |
            Where-Object { $_.PSIsContainer } |
            ForEach-Object { $_.FullName }
    }
}

function Clear-RelativeCaches {
    <# 对应用根目录下一组相对缓存子目录执行清空；进程运行中则整体跳过。#>
    param(
        [string]$Root,
        [string]$Name,
        [string[]]$RelativeDirs,
        [string[]]$ProcessNames
    )
    if (-not (Test-Path -LiteralPath $Root)) { return }
    if (Test-ProcessRunning $ProcessNames) {
        Write-Host ("  [跳过] {0} - 正在运行，关闭后重跑（不强行结束进程）" -f $Name) -ForegroundColor Yellow
        $script:skippedLocked++
        return
    }
    foreach ($rel in $RelativeDirs) {
        Clear-Folder -Path (Join-Path $Root $rel) -Label "$Name\$rel"
    }
}

function Stop-ServicesAndInvoke {
    param([string[]]$ServiceNames, [scriptblock]$Action)
    $stopped = @()
    foreach ($sn in $ServiceNames) {
        $svc = Get-Service -Name $sn -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq 'Running') {
            Stop-Service -Name $sn -Force -ErrorAction SilentlyContinue
            $check = Get-Service -Name $sn -ErrorAction SilentlyContinue
            if ($check -and $check.Status -ne 'Running') { $stopped += $sn }
        }
    }
    try {
        & $Action
    } finally {
        foreach ($sn in $stopped) { Start-Service -Name $sn -ErrorAction SilentlyContinue }
    }
}

function Remove-LegacySystemFolder {
    <# Windows.old / $WINDOWS.~BT 等遗留目录：夺取所有权后清空并删除根目录。#>
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host ("  [跳过] {0} - 不存在" -f $Label) -ForegroundColor DarkGray
        return
    }
    $before = Get-FolderSize $Path
    if ($before -eq 0) {
        if (-not $DryRun) { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue }
        Write-Host ("  [空]   {0}" -f $Label) -ForegroundColor DarkGray
        return
    }
    if ($DryRun) {
        Write-Host ("  [预览] {0} = {1}（删除后无法回退旧系统）" -f $Label, (Format-Size $before)) -ForegroundColor Yellow
        return
    }
    Write-Host ("  [处理] {0} - {1}，夺取所有权中 ..." -f $Label, (Format-Size $before)) -ForegroundColor Cyan
    & takeown.exe /F $Path /R /A /D Y 2>&1 | Out-Null
    & icacls.exe $Path /grant '*S-1-5-32-544:(OI)(CI)F' /T /C /Q 2>&1 | Out-Null
    $null = & robocopy.exe $script:emptyDir $Path /MIR /XJ /R:0 /W:0 /NFL /NDL /NJH /NJS /NP /MT:8
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    $after = Get-FolderSize $Path
    $freed = $before - $after
    if ($freed -lt 0) { $freed = 0 }
    $script:totalFreed += $freed
    if (Test-Path -LiteralPath $Path) {
        Write-Host ("  [部分] {0} - 释放 {1}，目录仍有残留（权限占用）" -f $Label, (Format-Size $freed)) -ForegroundColor Yellow
    } else {
        Write-Host ("  [完成] {0} - {1}，目录已移除" -f $Label, (Format-Size $freed)) -ForegroundColor Cyan
    }
}

function Test-DirHasFile {
    <# 目录下是否存在匹配通配符的文件（用于按兄弟文件判断目录身份）。#>
    param([string]$Dir, [string[]]$Patterns)
    foreach ($p in $Patterns) {
        try {
            $e = [IO.Directory]::EnumerateFiles($Dir, $p).GetEnumerator()
            if ($e.MoveNext()) { return $true }
        } catch { }
    }
    return $false
}

function Remove-ArtifactFolder {
    <# 删除构建产物目录本身（与 Clear-Folder 不同，target/node_modules 连根一起删）。#>
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return }
    $full = $item.FullName
    try { Assert-Cleanable $full } catch {
        Write-Host ("  [拒绝] {0} - {1}" -f $Label, $_.Exception.Message) -ForegroundColor Red
        $script:warnCount++
        return
    }
    $before = Get-FolderSize $full
    if ($DryRun) {
        Write-Host ("  [预览] {0} = {1}" -f $Label, (Format-Size $before)) -ForegroundColor Yellow
        return
    }
    $null = & robocopy.exe $script:emptyDir $full /MIR /XJ /R:0 /W:0 /NFL /NDL /NJH /NJS /NP /MT:8
    $rc = $LASTEXITCODE
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
    $after = [long]0
    if (Test-Path -LiteralPath $full) { $after = Get-FolderSize $full }
    $freed = [long]($before - $after)
    if ($freed -lt 0) { $freed = 0 }
    $script:totalFreed += $freed
    if ($after -gt 0) {
        $script:skippedLocked++
        Write-Host ("  [部分] {0} - 释放 {1}，残留 {2}（文件被占用，先停掉构建/开发服务器）" -f $Label, (Format-Size $freed), (Format-Size $after)) -ForegroundColor Cyan
    } elseif (Test-Path -LiteralPath $full) {
        Write-Host ("  [完成] {0} - 释放 {1}（内容已清空，空目录因权限保留）" -f $Label, (Format-Size $freed)) -ForegroundColor Cyan
    } else {
        Write-Host ("  [完成] {0} - 释放 {1}" -f $Label, (Format-Size $freed)) -ForegroundColor Cyan
    }
    if ($rc -ge 8) {
        Write-Host ("         robocopy 退出码 {0}，部分项目可能因权限不足未清理" -f $rc) -ForegroundColor Red
        $script:warnCount++
    }
}

function Invoke-ProjectArtifactClean {
    <#
      在单个用户显式指定的代码根内扫描构建产物。
      识别规则（目录名 + 兄弟标记文件，避免误删同名普通目录）：
        node_modules                     任意位置（名字本身即 npm 标记），命中即停不下钻
        target + Cargo.toml             Rust 工作区/包
        target + pom.xml                Maven
        bin/obj + *.csproj/*.fsproj/... .NET / VS C++
        build + build.gradle*/pubspec/CMakeLists   Gradle / Flutter / CMake
        .gradle + gradle 标记           Gradle 项目级缓存
        cmake-build-*                   JetBrains CMake 输出
        .venv + Python 工程标记          虚拟环境
        _build + mix.exs                Elixir
        __pycache__/.pytest_cache/.mypy_cache/.ruff_cache/.tox/.nox  Python 缓存
      显式不碰：dist/out/.next 等可能被提交的产物、.git、cargo/gradle 全局缓存。
    #>
    param([string]$Root)

    $rootFull = $null
    try { $rootFull = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path } catch {
        Write-Host ("  [拒绝] 项目根不存在: {0}" -f $Root) -ForegroundColor Red
        $script:warnCount++
        return
    }
    try {
        Assert-Cleanable $rootFull
    } catch {
        Write-Host ("  [拒绝] {0} - {1}" -f $Root, $_.Exception.Message) -ForegroundColor Red
        $script:warnCount++
        return
    }

    $pruneNames = @('.git', '.cargo', '.rustup', '$Recycle.Bin', 'System Volume Information')
    $dotnetMarks = @('*.csproj', '*.fsproj', '*.vbproj', '*.vcxproj')
    $gradleMarks = @('build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts')
    $pythonMarks = @('pyproject.toml', 'requirements.txt', 'requirements*.txt', 'setup.py', 'setup.cfg', 'Pipfile', 'tox.ini', 'environment.yml')
    $pyCacheNames = @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.nox')

    $stack = New-Object System.Collections.Generic.Stack[string]
    $stack.Push($rootFull)
    $hit = 0

    while ($stack.Count) {
        $dir = $stack.Pop()
        $leaf = [IO.Path]::GetFileName($dir.TrimEnd('\'))
        $parent = [IO.Path]::GetDirectoryName($dir.TrimEnd('\'))
        $kind = $null

        if ($leaf -eq 'node_modules') {
            $kind = 'node_modules'
        } elseif ($leaf -eq 'target' -and $parent) {
            if (Test-DirHasFile $parent @('Cargo.toml')) { $kind = 'Rust target' }
            elseif (Test-DirHasFile $parent @('pom.xml')) { $kind = 'Maven target' }
        } elseif (($leaf -eq 'bin' -or $leaf -eq 'obj') -and $parent) {
            if (Test-DirHasFile $parent $dotnetMarks) { $kind = ".NET $leaf" }
        } elseif ($leaf -eq 'build' -and $parent) {
            if (Test-DirHasFile $parent $gradleMarks) { $kind = 'Gradle build' }
            elseif (Test-DirHasFile $parent @('pubspec.yaml')) { $kind = 'Flutter build' }
            elseif (Test-DirHasFile $parent @('CMakeLists.txt')) { $kind = 'CMake build' }
        } elseif ($leaf -eq '.gradle' -and $parent) {
            if (Test-DirHasFile $parent $gradleMarks) { $kind = 'Gradle .gradle' }
        } elseif ($leaf -like 'cmake-build-*') {
            $kind = 'CMake cmake-build'
        } elseif ($leaf -eq '.venv' -and $parent) {
            if (Test-DirHasFile $parent $pythonMarks) { $kind = 'Python .venv' }
        } elseif ($leaf -eq '_build' -and $parent) {
            if (Test-DirHasFile $parent @('mix.exs')) { $kind = 'Elixir _build' }
        } elseif ($pyCacheNames -contains $leaf) {
            $kind = "Python $leaf"
        }

        if ($kind) {
            $rel = $dir.Substring($rootFull.TrimEnd('\').Length).TrimStart('\')
            if (-not $rel) { $rel = '.' }
            Remove-ArtifactFolder -Path $dir -Label ("{0,-16} {1}" -f $kind, $rel)
            $hit++
            continue   # 产物目录本身不再下钻（避免重复统计嵌套 node_modules/target）
        }

        try {
            foreach ($sub in [IO.Directory]::EnumerateDirectories($dir)) {
                $subLeaf = [IO.Path]::GetFileName($sub.TrimEnd('\'))
                if ($pruneNames -contains $subLeaf) { continue }
                try { if ([IO.File]::GetAttributes($sub) -band [IO.FileAttributes]::ReparsePoint) { continue } } catch { continue }
                $stack.Push($sub)
            }
        } catch { }
    }

    if ($hit -eq 0) {
        Write-Host ("  [-] {0} 下未发现构建产物" -f $rootFull) -ForegroundColor DarkGray
    } else {
        Write-Host ("  共处理 {0} 个产物目录（根: {1}）" -f $hit, $rootFull) -ForegroundColor DarkCyan
    }
}

# ---------------- 开始 ----------------

Write-Host ""
Write-Host "==================== Windows 全量磁盘清理 ====================" -ForegroundColor White
Write-Host (" 时间: {0}   计算机: {1}   用户: {2}" -f (Get-Date), $env:COMPUTERNAME, $env:USERNAME)
Write-Host (" 模式: {0}   管理员: {1}" -f $(if ($DryRun) { '预览 DryRun（不删除/不提权/不动服务）' } else { '实际清理' }), $isAdmin)
Write-Host (" 日志: {0}" -f $logPath)
Write-Host (" PowerShell: {0}" -f $PSVersionTable.PSVersion.ToString())
Write-Host "=============================================================" -ForegroundColor White

# 固定盘清理前快照（结束时用前后差值作为真实释放量判据）
$fixedDrives = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue |
    ForEach-Object {
        [pscustomobject]@{ Letter = $_.DeviceID; FreeBefore = [long]$_.FreeSpace; Size = [long]$_.Size }
    })
Write-Host (" 固定盘: {0}" -f (($fixedDrives | ForEach-Object { $_.Letter }) -join ' '))

# ---- 1. 用户临时文件 / 崩溃转储 ----
Write-Section '1/11 用户临时文件与崩溃转储'

$tempTargets = @($env:TEMP, $env:TMP) | Where-Object { $_ } |
    ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\').ToLowerInvariant() } | Select-Object -Unique
foreach ($t in $tempTargets) {
    Clear-Folder -Path $t -Label "Temp ($t)"
}
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'CrashDumps') 'CrashDumps'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'SquirrelTemp') 'SquirrelTemp（Electron 更新残留）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'RailCrashReport') 'RailCrashReport'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\WER') '用户 WER 错误报告'

# ---- 2. GPU / 着色器缓存 ----
Write-Section '2/11 GPU 着色器缓存（自动重建）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'D3DSCache') 'D3DSCache（DirectX）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'NVIDIA\GLCache') 'NVIDIA GLCache'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'NVIDIA\DXCache') 'NVIDIA DXCache'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'NVIDIA\ComputeCache') 'NVIDIA ComputeCache'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'AMD\DxCache') 'AMD DxCache'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'AMD\GLCache') 'AMD GLCache'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Intel\ShaderCache') 'Intel ShaderCache'

# ---- 3. 浏览器缓存（白名单，绝不碰配置） ----
Write-Section '3/11 浏览器缓存（仅缓存目录，运行中跳过）'

$chromiumCacheDirs = @(
    'Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'DawnGraphiteCache',
    'DawnWebGPUCache', 'GrShaderCache', 'ShaderCache', 'GraphiteDawnCache',
    'Media Cache', 'Service Worker\CacheStorage'
)

$chromiumFamilies = @(
    @{ Name = 'Edge';    Patterns = @("$env:LOCALAPPDATA\Microsoft\Edge*\User Data");       Procs = @('msedge') }
    @{ Name = 'Chrome';  Patterns = @("$env:LOCALAPPDATA\Google\Chrome*\User Data", "$env:LOCALAPPDATA\Chromium\User Data"); Procs = @('chrome') }
    @{ Name = 'Brave';   Patterns = @("$env:LOCALAPPDATA\BraveSoftware\Brave-Browser*\User Data"); Procs = @('brave') }
    @{ Name = 'Vivaldi'; Patterns = @("$env:LOCALAPPDATA\Vivaldi\User Data");               Procs = @('vivaldi') }
    @{ Name = 'Yandex';  Patterns = @("$env:LOCALAPPDATA\Yandex\YandexBrowser\User Data"); Procs = @('browser') }
    @{ Name = 'Whale';   Patterns = @("$env:LOCALAPPDATA\Naver\Whale\User Data");          Procs = @('whale') }
)
foreach ($fam in $chromiumFamilies) {
    $roots = @(Resolve-GlobRoots $fam.Patterns)
    if ($roots.Count -eq 0) { continue }
    if (Test-ProcessRunning $fam.Procs) {
        Write-Host ("  [跳过] {0} - 浏览器正在运行，关闭后重跑" -f $fam.Name) -ForegroundColor Yellow
        $script:skippedLocked++
        continue
    }
    foreach ($root in $roots) {
        $profiles = @(Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'Default' -or $_.Name -like 'Profile *' -or $_.Name -eq 'System Profile' -or $_.Name -eq 'Guest Profile' })
        foreach ($pf in $profiles) {
            foreach ($cd in $chromiumCacheDirs) {
                Clear-Folder -Path (Join-Path $pf.FullName $cd) -Label ("$fam.Name\$($pf.Name)\$cd")
            }
        }
    }
}

# Opera 系（Roaming/Local 下的直接 profile 根，无 User Data 层）
foreach ($opRoot in @(Resolve-GlobRoots @("$env:APPDATA\Opera Software\Opera*", "$env:LOCALAPPDATA\Opera Software\Opera*"))) {
    Clear-RelativeCaches -Root $opRoot -Name "Opera\$(Split-Path $opRoot -Leaf)" -RelativeDirs $chromiumCacheDirs -ProcessNames @('opera')
}

# Firefox（Roaming 与 Local 下都枚举 profile）
if (Test-ProcessRunning @('firefox')) {
    Write-Host '  [跳过] Firefox - 正在运行，关闭后重跑' -ForegroundColor Yellow
    $script:skippedLocked++
} else {
    $ffCacheDirs = @('cache2', 'startupCache', 'shader-cache', 'OfflineCache')
    foreach ($ffBase in @("$env:APPDATA\Mozilla\Firefox\Profiles", "$env:LOCALAPPDATA\Mozilla\Firefox\Profiles")) {
        if (Test-Path -LiteralPath $ffBase) {
            foreach ($pf in @(Get-ChildItem -LiteralPath $ffBase -Directory -Force -ErrorAction SilentlyContinue)) {
                foreach ($cd in $ffCacheDirs) {
                    Clear-Folder -Path (Join-Path $pf.FullName $cd) -Label "Firefox\$($pf.Name)\$cd"
                }
            }
        }
    }
}

# ---- 4. Electron 应用缓存 ----
Write-Section '4/11 Teams / Slack / Discord 缓存（仅缓存白名单，运行中跳过）'

$electronApps = @(
    @{ Name = 'Teams 经典版'; Root = "$env:APPDATA\Microsoft\Teams"; Procs = @('Teams', 'ms-teams') }
    @{ Name = 'Teams 2.x';   Root = "$env:LOCALAPPDATA\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams"; Procs = @('ms-teams', 'Teams') }
    @{ Name = 'Slack';       Root = "$env:APPDATA\Slack";   Procs = @('slack') }
    @{ Name = 'Discord';     Root = "$env:APPDATA\discord"; Procs = @('discord') }
)
$electronRelDirs = @('Cache', 'Code Cache', 'GPUCache', 'Service Worker\CacheStorage', 'tmp')
foreach ($app in $electronApps) {
    Clear-RelativeCaches -Root $app.Root -Name $app.Name -RelativeDirs $electronRelDirs -ProcessNames $app.Procs
}

# ---- 5. 编辑器 / IDE ----
Write-Section '5/11 编辑器与 IDE 缓存、日志（运行中跳过缓存）'

$editorRel = @('Cache', 'CachedData', 'Code Cache', 'GPUCache', 'logs')
$editors = @(
    @{ Name = 'VS Code';   Root = "$env:APPDATA\Code";         Procs = @('Code') }
    @{ Name = 'Trae';      Root = "$env:APPDATA\TRAE SOLO";    Procs = @('Trae', 'Trae CN') }
    @{ Name = 'CodeBuddy'; Root = "$env:APPDATA\CodeBuddy CN"; Procs = @('CodeBuddy') }
    @{ Name = 'Cursor';    Root = "$env:APPDATA\Cursor";       Procs = @('Cursor') }
    @{ Name = 'Windsurf';  Root = "$env:APPDATA\Windsurf";     Procs = @('Windsurf') }
)
foreach ($ed in $editors) {
    Clear-RelativeCaches -Root $ed.Root -Name $ed.Name -RelativeDirs $editorRel -ProcessNames $ed.Procs
}
# Zed 只清日志（缓存目录与本地数据库混放，保守处理）
Clear-RelativeCaches -Root "$env:LOCALAPPDATA\Zed" -Name 'Zed' -RelativeDirs @('logs') -ProcessNames @('Zed')

# JetBrains 全家（caches/log/tmp；IDE 运行中整体跳过）
$jbProcs = @('idea64', 'idea', 'webstorm64', 'webstorm', 'pycharm64', 'pycharm', 'rider64', 'rider',
    'goland64', 'goland', 'clion64', 'clion', 'phpstorm64', 'phpstorm', 'rubymine64', 'rubymine',
    'datagrip64', 'datagrip', 'studio64', 'studio', 'fleet')
$jbRoot = Join-Path $env:LOCALAPPDATA 'JetBrains'
if (Test-Path -LiteralPath $jbRoot) {
    if (Test-ProcessRunning $jbProcs) {
        Write-Host '  [跳过] JetBrains IDE - 正在运行，关闭后重跑（避免索引中途失效）' -ForegroundColor Yellow
        $script:skippedLocked++
    } else {
        foreach ($prod in @(Get-ChildItem -LiteralPath $jbRoot -Directory -Force -ErrorAction SilentlyContinue)) {
            foreach ($sub in @('caches', 'log', 'logs', 'tmp')) {
                $p = Join-Path $prod.FullName $sub
                if (Test-Path -LiteralPath $p) { Clear-Folder -Path $p -Label "JetBrains\$($prod.Name)\$sub" }
            }
        }
    }
}

# ---- 6. 外壳与系统用户缓存 ----
Write-Section '6/11 外壳与用户级系统缓存'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\INetCache') 'INetCache（IE/WinINET 遗留）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\WebCache') 'WebCache（IE 遗留，通常被占用）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Explorer') 'Explorer 缩略图/图标缓存（被占用部分保留）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\Terminal Server Client\Cache') 'RDP 位图缓存'

# ---- 7. 开发包管理器 / 工具链缓存 ----
Write-Section '7/11 包管理器与工具链缓存（官方清理命令）'

# gradle 守护进程持有缓存文件锁，先停
$gradleCmd = Get-Command gradle -ErrorAction SilentlyContinue
if ($gradleCmd -and -not $DryRun) {
    Write-Host '  [执行] 停止 Gradle 守护进程（gradle --stop）...' -ForegroundColor DarkCyan
    & $gradleCmd.Source --stop 2>&1 | Out-Null
} elseif ($gradleCmd -and $DryRun) {
    Write-Host '  [预览] 将先执行 gradle --stop 再清 caches' -ForegroundColor Yellow
}

Invoke-CacheCommand -Exe 'npm'      -Arguments @('cache', 'clean', '--force') -Label 'npm 缓存'
Invoke-CacheCommand -Exe 'pnpm'     -Arguments @('store', 'prune')             -Label 'pnpm store'
Invoke-CacheCommand -Exe 'yarn'     -Arguments @('cache', 'clean')             -Label 'yarn 缓存'
Invoke-CacheCommand -Exe 'bun'      -Arguments @('pm', 'cache', 'rm')          -Label 'bun 缓存'
Invoke-CacheCommand -Exe 'uv'       -Arguments @('cache', 'clean')             -Label 'uv 缓存'
Invoke-CacheCommand -Exe 'pip'      -Arguments @('cache', 'purge')             -Label 'pip 缓存'
Invoke-CacheCommand -Exe 'dotnet'   -Arguments @('nuget', 'locals', 'all', '--clear') -Label 'dotnet/NuGet 缓存'
Invoke-CacheCommand -Exe 'go'       -Arguments @('clean', '-cache', '-modcache') -Label 'go 构建与模块缓存'
Invoke-CacheCommand -Exe 'conda'    -Arguments @('clean', '-afy')              -Label 'conda 缓存'
Invoke-CacheCommand -Exe 'composer' -Arguments @('clear-cache')                -Label 'composer 缓存'
Invoke-CacheCommand -Exe 'dart'     -Arguments @('pub', 'cache', 'clean', '-f') -Label 'pub (Flutter/Dart) 缓存'

Write-Sub '已知缓存目录（未安装对应 CLI 或作为补充时直接清）'
Clear-Folder -Path (Join-Path $env:USERPROFILE '.cargo\registry\cache')     'cargo registry crate 缓存'
Clear-Folder -Path (Join-Path $env:USERPROFILE '.cargo\registry\src')       'cargo registry 解包源码'
Clear-Folder -Path (Join-Path $env:USERPROFILE '.cargo\git\checkouts')      'cargo git checkouts'
Clear-Folder -Path (Join-Path $env:USERPROFILE '.gradle\caches')            'Gradle caches'
Clear-Folder -Path (Join-Path $env:USERPROFILE '.m2\repository')            'Maven 本地仓库（settings.xml 保留）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'NuGet\v3-cache')           'NuGet HTTP 缓存'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'pip\cache')                'pip 缓存目录（补充）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Yarn\Cache')               'Yarn 缓存目录（补充）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'pypoetry\Cache\repository') 'Poetry HTTP 仓库缓存'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'pypoetry\Cache\artifacts')  'Poetry 构建产物缓存'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Pub\Cache\hosted')         'pub hosted 包缓存（全局激活的 bin 保留）'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Pub\Cache\_temp')          'pub 临时缓存'
Clear-Folder -Path (Join-Path $env:LOCALAPPDATA 'Composer\files')           'Composer 缓存目录（补充）'
# Deno：只清子目录，保留根目录散落文件（如 REPL 历史）
$denoRoot = if ($env:DENO_DIR) { $env:DENO_DIR } else { Join-Path $env:LOCALAPPDATA 'deno' }
Clear-ChildFolders -Path $denoRoot -LabelPrefix "Deno ($denoRoot)"

# ---- 8. Docker ----
Write-Section '8/11 Docker 构建缓存与无用资源（不删卷）'
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCmd) {
    $engineOk = $false
    if (-not $DryRun) {
        & $dockerCmd.Source info 2>&1 | Out-Null
        $engineOk = ($LASTEXITCODE -eq 0)
    }
    if ($DryRun) {
        Write-Host '  [预览] 将执行: docker builder prune -af; docker system prune -af（不删卷）' -ForegroundColor Yellow
    } elseif ($engineOk) {
        Invoke-CacheCommand -Exe 'docker' -Arguments @('builder', 'prune', '-af') -Label 'Docker buildx 构建缓存'
        Invoke-CacheCommand -Exe 'docker' -Arguments @('system', 'prune', '-af')  -Label 'Docker 无用镜像/容器/网络'
    } else {
        Write-Host '  [跳过] Docker 引擎未运行' -ForegroundColor DarkGray
    }
} else {
    Write-Host '  [跳过] 未安装 docker' -ForegroundColor DarkGray
}

# ---- 9. 项目构建产物（仅 -ProjectRoot 显式指定时） ----
Write-Section '9/11 项目构建产物（仅 -ProjectRoot 指定的代码根，默认关闭）'
if ($ProjectRoot -and $ProjectRoot.Count -gt 0) {
    Write-Host ' 识别：Rust/Maven target、node_modules、.NET bin/obj、Gradle/Flutter/CMake build、' -ForegroundColor DarkCyan
    Write-Host '       cmake-build-*、Python 缓存与 .venv、Elixir _build（删除后需重新编译/下载）' -ForegroundColor DarkCyan
    foreach ($pr in $ProjectRoot) {
        Write-Sub "项目根: $pr"
        Invoke-ProjectArtifactClean -Root $pr
    }
} else {
    Write-Host '  [跳过] 未指定 -ProjectRoot：项目 target/node_modules 等不在通用清理范围。' -ForegroundColor DarkGray
    Write-Host '         需要时显式指定代码根，例如：-ProjectRoot C:\resource,D:\code' -ForegroundColor DarkGray
}

# ---- 10. 系统级（需管理员） ----
Write-Section '10/11 系统级清理'
if ($isAdmin) {
    Write-Sub '系统临时与预读取'
    Clear-Folder -Path (Join-Path $env:SystemRoot 'Temp') 'Windows\Temp'
    Clear-Folder -Path (Join-Path $env:SystemRoot 'Prefetch') 'Prefetch（预读取，自动重建；首次开机略慢）'

    Write-Sub '系统错误报告与内存转储'
    Clear-Folder -Path 'C:\ProgramData\Microsoft\Windows\WER' '系统 WER 错误报告'
    Remove-FilesByPattern -Folder $env:SystemRoot -Pattern 'MEMORY.DMP' -Label '内核内存转储 MEMORY.DMP'
    Remove-FilesByPattern -Folder (Join-Path $env:SystemRoot 'Minidump') -Pattern '*.dmp' -Label 'Minidump 小型转储'
    Clear-Folder -Path (Join-Path $env:SystemRoot 'LiveKernelReports') 'LiveKernelReports'

    Write-Sub 'Windows 日志文件（CBS / WindowsUpdate / DISM）'
    Clear-Folder -Path (Join-Path $env:SystemRoot 'Logs\CBS') 'CBS  servicing 日志'
    Clear-Folder -Path (Join-Path $env:SystemRoot 'Logs\WindowsUpdate') 'WindowsUpdate 日志'
    Clear-Folder -Path (Join-Path $env:SystemRoot 'Logs\DISM') 'DISM 日志'

    Write-Sub 'Windows 更新下载缓存（临时停止 wuauserv/bits）'
    Stop-ServicesAndInvoke -ServiceNames @('wuauserv', 'bits') -Action {
        Clear-Folder -Path (Join-Path $env:SystemRoot 'SoftwareDistribution\Download') 'SoftwareDistribution\Download'
    }

    Write-Sub 'Delivery Optimization 传递优化缓存'
    if (Get-Command 'Delete-DeliveryOptimizationCache' -ErrorAction SilentlyContinue) {
        if ($DryRun) {
            Write-Host '  [预览] 将执行: Delete-DeliveryOptimizationCache -Force' -ForegroundColor Yellow
        } else {
            Write-Host '  [执行] Delete-DeliveryOptimizationCache -Force ...' -ForegroundColor Cyan
            $null = Delete-DeliveryOptimizationCache -Force -ErrorAction SilentlyContinue
        }
    } else {
        Clear-Folder -Path 'C:\Windows\ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Cache' 'DeliveryOptimization\Cache'
    }

    Write-Sub '字体缓存服务（临时停止 FontCache）'
    Stop-ServicesAndInvoke -ServiceNames @('FontCache', 'FontCache3.0.0.0') -Action {
        Clear-Folder -Path 'C:\Windows\ServiceProfiles\LocalService\AppData\Local\FontCache' 'Windows FontCache'
    }

    Write-Sub '旧系统 / 升级残留'
    Remove-LegacySystemFolder -Path (Join-Path $env:SystemDrive '$WINDOWS.~BT') -Label '$WINDOWS.~BT（临时安装文件）'
    Remove-LegacySystemFolder -Path (Join-Path $env:SystemDrive '$WINDOWS.~WS') -Label '$WINDOWS.~WS（升级助手残留）'
    Remove-LegacySystemFolder -Path (Join-Path $env:SystemDrive 'Windows.old') -Label 'Windows.old（旧系统，删除后无法回退）'

    Write-Sub 'DISM 组件存储清理（StartComponentCleanup，可能数分钟）'
    if ($DryRun) {
        Write-Host '  [预览] 将执行: Dism.exe /Online /Cleanup-Image /StartComponentCleanup' -ForegroundColor Yellow
    } else {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Write-Host '  [执行] DISM StartComponentCleanup ...' -ForegroundColor Cyan
        $null = & Dism.exe /Online /Cleanup-Image /StartComponentCleanup /Quiet
        $sw.Stop()
        Write-Host ("         DISM 完成，耗时 {0:N1} 分钟（退出码 {1}）" -f $sw.Elapsed.TotalMinutes, $LASTEXITCODE) -ForegroundColor DarkCyan
    }
} else {
    Write-Host '  [跳过] 非管理员：Windows\Temp、Prefetch、WER、内存转储、更新缓存、' -ForegroundColor Yellow
    Write-Host '         字体缓存、Windows.old、DISM 等系统级项目未执行。' -ForegroundColor Yellow
    Write-Host '         重新以管理员身份运行（或同意 UAC 提权）即可覆盖。' -ForegroundColor Yellow
}

# ---- 11. 回收站（所有固定盘） ----
Write-Section '11/11 回收站（所有固定盘）'
foreach ($d in $fixedDrives) {
    if ($DryRun) {
        Write-Host ("  [预览] 将清空 {0} 回收站" -f $d.Letter) -ForegroundColor Yellow
    } else {
        Clear-RecycleBin -DriveLetter $d.Letter.TrimEnd(':') -Force -ErrorAction SilentlyContinue
        Write-Host ("  [完成] {0} 回收站已清空" -f $d.Letter) -ForegroundColor Cyan
    }
}

# ---------------- 汇总 ----------------

Write-Host ""
Write-Host "===================== 汇总 =====================" -ForegroundColor White

if ($DryRun) {
    Write-Host ' 以上为预览：未删除任何内容、未提权、未变更服务。' -ForegroundColor Yellow
    Write-Host ' 去掉 -DryRun 重新执行即开始全量清理（会弹一次 UAC）。' -ForegroundColor Yellow
} else {
    # 重新读取各固定盘可用空间，用差值给出真实释放量
    $drivesAfter = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue
    [long]$deltaTotal = 0
    foreach ($d in $fixedDrives) {
        $afterObj = $drivesAfter | Where-Object { $_.DeviceID -eq $d.Letter }
        if ($afterObj) {
            $delta = [long]$afterObj.FreeSpace - $d.FreeBefore
            if ($delta -lt 0) { $delta = 0 }
            $deltaTotal += $delta
            Write-Host (" {0} 可用 {1} -> {2}   实际释放 {3}" -f `
                $d.Letter, (Format-Size $d.FreeBefore), (Format-Size ([long]$afterObj.FreeSpace)), (Format-Size $delta))
        }
    }
    Write-Host ""
    Write-Host (" 各项目累计释放（不含 DISM/回收站/CLI 自有统计）: {0}" -f (Format-Size $script:totalFreed)) -ForegroundColor Green
    Write-Host (" 磁盘可用空间实际增量（权威数字）:               {0}" -f (Format-Size $deltaTotal)) -ForegroundColor Green
    if ($script:skippedLocked -gt 0) {
        Write-Host (" 有 {0} 类项目因软件运行中被跳过或仅部分清理：关闭相关软件后重跑即可。" -f $script:skippedLocked) -ForegroundColor Yellow
    }
    if ($script:warnCount -gt 0) {
        Write-Host (" 有 {0} 条警告，详见日志。" -f $script:warnCount) -ForegroundColor Yellow
    }
}
Write-Host (" 日志: {0}" -f $logPath) -ForegroundColor DarkGray
Write-Host " 边界：个人文件、浏览器配置、聊天记录、Windows\Installer、休眠/页面文件、" -ForegroundColor DarkGray
Write-Host "       系统还原点、事件日志、Docker 卷、项目构建产物不在清理范围内。" -ForegroundColor DarkGray
Write-Host "================================================" -ForegroundColor White

# ---------------- 收尾 ----------------

Remove-Item -LiteralPath $script:emptyDir -Recurse -Force -ErrorAction SilentlyContinue
Stop-Transcript | Out-Null
