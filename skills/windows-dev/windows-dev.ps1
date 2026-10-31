#requires -Version 5.1
<#
.SYNOPSIS
    Windows agentic 开发环境的四道坎：换行符、字符编码、文件路径、系统环境。

.DESCRIPTION
    Agent 在 Windows 上跑脚本时的绝大多数"莫名其妙的错误"都来自这四类问题，
    而且它们的共同特点是：不报错，或者报一个跟原因毫无关系的错。

      eol       行尾符。Windows 原生 CRLF，仓库/解释器要 LF。混用会让 shell
                脚本报 `\r: command not found`、让 diff 满屏、让 agent 的
                字符串匹配全部落空。
      encoding  编码。中文 Windows 默认 GBK(936)，PowerShell 5.1 默认写
                UTF-16/带 BOM 的 UTF-8。一个无 BOM 的 UTF-8 文件被 PS 5.1
                按 ANSI 读出来就是乱码，而且这个乱码会被原样写回、污染文件。
      path      路径。260 字符 MAX_PATH、保留设备名、大小写不敏感导致的
                Git 冲突、结尾空格与点。
      env       环境体检。执行策略、长路径开关、Git autocrlf、终端输出编码。

    五个命令：
      doctor    默认。把下面四项全部只读扫一遍，出汇总报告，不改任何东西。
      eol       scan（只读报告）/ fix（直接覆盖）/ guard（写 .gitattributes
                与 .editorconfig）
      encoding  scan（只读报告）/ fix（去 BOM 或 GBK/UTF-16 转 UTF-8，直接覆盖）
      path      scan（只读报告；路径问题没有安全的自动修法）
      env       只读报告 + 给出修复命令（不自动改注册表、不自动改 git config）

    改写行为（fix）：
      直接覆盖原文件，不生成任何备份。改写不可回滚，所以先跑 scan 看清楚再 fix；
      有 git 的仓库依赖 git 本身来兜底（fix 前保持工作区干净）。
      .git 目录与二进制文件（嗅探到 NUL 字节）一律跳过。

.PARAMETER Command
    doctor | eol | encoding | path | env。默认 doctor。

.PARAMETER Path
    位置参数 1。一个或多个扫描根，默认当前目录；多个用空格分隔。

.PARAMETER Action
    scan（默认，只读）| fix（直接覆盖式改写）| guard（仅 eol：生成配置文件）。
    encoding 只接受 scan/fix；path 与 env 永远只读。

.PARAMETER Limit
    报告里最多列出多少个问题文件，默认 20。

.EXAMPLE
    windows-dev.ps1
    全量只读体检。

.EXAMPLE
    windows-dev.ps1 eol C:\repo -Action fix
    把 C:\repo 下的文本文件按规则统一行尾，直接覆盖。

.EXAMPLE
    windows-dev.ps1 eol C:\repo -Action guard
    在 C:\repo 写入 .gitattributes 与 .editorconfig（已存在则跳过）。

.EXAMPLE
    windows-dev.ps1 encoding C:\repo -Action fix
    去 UTF-8 BOM、把 GBK/UTF-16 文件转成 UTF-8，直接覆盖。

.EXAMPLE
    windows-dev.ps1 env
    打印 PowerShell 版本、执行策略、长路径开关、Git 配置并给出修复命令。
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('doctor', 'eol', 'encoding', 'path', 'env')]
    [string]$Command = 'doctor',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Path = @('.'),

    [ValidateSet('scan', 'fix', 'guard')]
    [string]$Action = 'scan',

    [int]$Limit = 20
)

$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# 控制台编码：PS 5.1 重定向时默认走 ANSI，中文会变成问号。提前切成 UTF-8。
# ---------------------------------------------------------------------------
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch {
    # ISE 或某些宿主下 [Console] 不可用，忽略即可，不影响扫描结果。
}

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 必须保持 CRLF 的类型。cmd.exe 对多行括号块与 goto 的解析在多年前就绑死在
# CRLF 上，LF 版本的 .bat 在某些机器上会静默执行错行。
$script:CrlfExtensions = @('.bat', '.cmd')

# 需要保留 UTF-8 BOM 的类型。Windows PowerShell 5.1 读无 BOM 的 .ps1 时按系统
# ANSI(936) 解码，中文注释/字符串直接乱码；pwsh 7 无此问题但留着 BOM 也没害处。
$script:BomKeepExtensions = @('.ps1', '.psm1', '.psd1')

# 视为文本并参与扫描的后缀。不在表里的文件除非是已知无后缀名，否则不碰。
$script:TextExtensions = @(
    '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.d.ts', '.vue', '.svelte', '.astro',
    '.py', '.pyi', '.rb', '.go', '.rs', '.java', '.kt', '.kts', '.scala', '.groovy',
    '.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.php', '.swift', '.pl', '.pm', '.lua',
    '.ex', '.exs', '.erl', '.hs', '.ml', '.clj', '.r', '.jl', '.dart', '.zig', '.nim',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.psm1', '.psd1', '.bat', '.cmd',
    '.json', '.jsonc', '.json5', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.properties', '.xml', '.csv', '.tsv', '.sql', '.env',
    '.md', '.markdown', '.rst', '.txt', '.adoc',
    '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.gradle', '.cmake', '.dockerfile', '.gitignore', '.gitattributes', '.editorconfig'
)

# 无后缀但确实是文本的文件名。
$script:TextFileNames = @(
    'Makefile', 'makefile', 'GNUmakefile', 'Dockerfile', 'dockerfile',
    'LICENSE', 'LICENCE', 'README', 'CHANGELOG', 'CMakeLists.txt',
    '.gitignore', '.gitattributes', '.editorconfig', '.env', '.npmrc', '.nvmrc'
)

# 第三方/生成物目录：改它们的行尾没有意义，量还极大。
$script:ExcludedDirectories = @(
    '.git', '.svn', '.hg', '.bzr',
    'node_modules', 'vendor', 'bower_components', 'jspm_packages',
    'target', 'dist', 'build', 'bin', 'obj', 'out', '.next', '.nuxt', '.output',
    '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.gradle', '.terraform', '.idea', '.vs', '.vscode-test', 'coverage'
)

# 超过这个大小就不读全文，只在报告里标 skipped。防止误把大二进制拖进内存。
$script:MaxFileBytes = 20MB

# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------

function Write-SectionTitle {
    param([string]$Title)
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkCyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host ('=' * 78) -ForegroundColor DarkCyan
}

function Write-SubTitle {
    param([string]$Title)
    Write-Host ''
    Write-Host "  $Title" -ForegroundColor Yellow
    Write-Host ('  ' + ('-' * 74)) -ForegroundColor DarkGray
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [OK]   $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
}

function Write-Bad {
    param([string]$Message)
    Write-Host "  [BAD]  $Message" -ForegroundColor Red
}

function Write-Info {
    param([string]$Message)
    Write-Host "  [..]   $Message" -ForegroundColor Gray
}

# 报告里一律显示相对扫描根的路径：绝对路径在 120 列的控制台里必然折行，
# 一折行就没法一眼看出是哪个文件。
function Get-DisplayPath {
    param([string]$FullName, [string]$Root)

    if ([string]::IsNullOrEmpty($Root)) { return $FullName }
    $rootTrimmed = $Root.TrimEnd('\', '/')
    if ($rootTrimmed.Length -eq 0) { return $FullName }
    if ($FullName.StartsWith($rootTrimmed, [StringComparison]::OrdinalIgnoreCase)) {
        $relative = $FullName.Substring($rootTrimmed.Length).TrimStart('\', '/')
        if ([string]::IsNullOrEmpty($relative)) { return '.' }
        return $relative
    }
    return $FullName
}

# ---------------------------------------------------------------------------
# 文件枚举
# ---------------------------------------------------------------------------

function Test-PathHasExcludedSegment {
    param([string]$FullName, [string]$RootFullName)

    $relative = $FullName.Substring([Math]::Min($RootFullName.Length, $FullName.Length))
    $segments = $relative -split '[\\/]'
    foreach ($segment in $segments) {
        if ($script:ExcludedDirectories -contains $segment) { return $true }
    }
    return $false
}

function Test-IsTextCandidate {
    param([IO.FileInfo]$File)

    if ($script:TextFileNames -contains $File.Name) { return $true }
    $extension = $File.Extension
    if ([string]::IsNullOrEmpty($extension)) { return $false }
    return ($script:TextExtensions -contains $extension.ToLowerInvariant())
}

function Get-TargetFiles {
    param([string]$Root)

    $item = Get-Item -LiteralPath $Root -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        Write-Bad "路径不存在：$Root"
        return @()
    }
    $rootFullName = $item.FullName.TrimEnd('\', '/')

    if ($item.PSIsContainer) {
        $children = @(Get-ChildItem -LiteralPath $item.FullName -Recurse -File `
                        -ErrorAction SilentlyContinue -ErrorVariable enumerationErrors)
    } else {
        $children = @($item)
    }

    $result = New-Object System.Collections.Generic.List[IO.FileInfo]
    foreach ($file in $children) {
        if (-not (Test-IsTextCandidate $file)) { continue }
        if (Test-PathHasExcludedSegment $file.FullName $rootFullName) { continue }
        # 备份文件不该被当成源码扫，也不该被修。
        if ($file.Extension -ieq '.bak') { continue }
        $result.Add($file)
    }

    if ($enumerationErrors.Count -gt 0) {
        Write-Warn ("枚举时有 $($enumerationErrors.Count) 个目录读取失败（多半是长路径或权限），已跳过")
    }
    return $result.ToArray()
}

function Read-FileBytes {
    param([IO.FileInfo]$File)

    if ($File.Length -gt $script:MaxFileBytes) {
        return $null
    }
    try {
        return [IO.File]::ReadAllBytes($File.FullName)
    } catch {
        return $null
    }
}

# NUL 字节是二进制最可靠的信号。UTF-16 也有大量 0x00，所以这类文件会被判成
# 二进制并跳过行尾处理——这恰好是我们想要的：UTF-16 该走 encoding 通道，
# 而不是被当成文本按字节改行尾。
function Test-LooksBinary {
    param([byte[]]$Bytes)

    $probeLength = [Math]::Min($Bytes.Length, 8192)
    for ($i = 0; $i -lt $probeLength; $i++) {
        if ($Bytes[$i] -eq 0) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------
# .gitattributes 解析
# ---------------------------------------------------------------------------

# 只支持 Git 模式里最常见的三种形式：'*'、'*.ext'、'精确文件名'。
# 不做 glob 通配——99% 的 .gitattributes 只用这三种，做全套反而容易匹配错。
function Test-GitAttributesPattern {
    param([string]$Pattern, [string]$FileName)

    if ($Pattern -eq '*') { return $true }
    if ($Pattern.StartsWith('*.')) {
        return $FileName.EndsWith($Pattern.Substring(1), [StringComparison]::OrdinalIgnoreCase)
    }
    if ($Pattern.StartsWith('*')) {
        return $FileName.EndsWith($Pattern.Substring(1), [StringComparison]::OrdinalIgnoreCase)
    }
    return [string]::Equals($Pattern, $FileName, [StringComparison]::OrdinalIgnoreCase)
}

# 返回按"后出现者优先"排序的规则列表 —— 这与 Git 的语义一致。
function Get-GitAttributesEolRules {
    param([string]$Root)

    $attributesPath = Join-Path $Root '.gitattributes'
    if (-not (Test-Path -LiteralPath $attributesPath)) { return @() }

    $rules = New-Object System.Collections.Generic.List[object]
    foreach ($line in (Get-Content -LiteralPath $attributesPath -ErrorAction SilentlyContinue)) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrEmpty($trimmed)) { continue }
        if ($trimmed.StartsWith('#')) { continue }

        $tokens = $trimmed -split '\s+'
        if ($tokens.Count -lt 2) { continue }

        $eol = $null
        foreach ($token in $tokens) {
            if ($token -eq 'eol=lf') { $eol = 'lf' }
            elseif ($token -eq 'eol=crlf') { $eol = 'crlf' }
            elseif ($token -eq '-text' -or $token -eq 'binary') { $eol = 'binary' }
        }
        if ($null -eq $eol) { continue }

        $rules.Add([pscustomobject]@{ Pattern = $tokens[0]; Eol = $eol })
    }
    return $rules.ToArray()
}

function Get-DesiredEol {
    param([string]$FileName, [object[]]$Rules)

    for ($i = $Rules.Count - 1; $i -ge 0; $i--) {
        if (Test-GitAttributesPattern $Rules[$i].Pattern $FileName) {
            return $Rules[$i].Eol
        }
    }
    # 没有 .gitattributes 时的兜底：Windows 批处理保持 CRLF，其余一律 LF。
    if ($script:CrlfExtensions -contains ([IO.Path]::GetExtension($FileName).ToLowerInvariant())) {
        return 'crlf'
    }
    return 'lf'
}

# ---------------------------------------------------------------------------
# 行尾符
# ---------------------------------------------------------------------------

function Get-LineEndingStatistics {
    param([byte[]]$Bytes)

    $crlf = 0
    $lf = 0
    $cr = 0
    for ($i = 0; $i -lt $Bytes.Length; $i++) {
        $byte = $Bytes[$i]
        if ($byte -eq 13) {
            if (($i + 1) -lt $Bytes.Length -and $Bytes[$i + 1] -eq 10) {
                $crlf++
                $i++
            } else {
                $cr++
            }
        } elseif ($byte -eq 10) {
            $lf++
        }
    }
    return [pscustomobject]@{
        Crlf = $crlf
        Lf   = $lf
        Cr   = $cr
        Kind = if ($crlf -gt 0 -and $lf -gt 0) { 'mixed' }
               elseif ($crlf -gt 0) { 'crlf' }
               elseif ($lf -gt 0) { 'lf' }
               elseif ($cr -gt 0) { 'cr' }
               else { 'none' }
    }
}

function Convert-BytesLineEndings {
    param([byte[]]$Bytes, [string]$TargetEol)

    if ($TargetEol -eq 'crlf') {
        # 先 CRLF->LF 归一，再 LF->CRLF，避免 LF 被二次加工成 CRCRLF。
        $normalized = Convert-BytesLineEndings -Bytes $Bytes -TargetEol 'lf'
        $output = New-Object System.Collections.Generic.List[byte]
        foreach ($byte in $normalized) {
            if ($byte -eq 10) {
                $output.Add(13)
                $output.Add(10)
            } else {
                $output.Add($byte)
            }
        }
        return $output.ToArray()
    }

    $output = New-Object System.Collections.Generic.List[byte] ($Bytes.Length)
    for ($i = 0; $i -lt $Bytes.Length; $i++) {
        $byte = $Bytes[$i]
        if ($byte -eq 13) {
            $output.Add(10)
            if (($i + 1) -lt $Bytes.Length -and $Bytes[$i + 1] -eq 10) { $i++ }
        } else {
            $output.Add($byte)
        }
    }
    return $output.ToArray()
}

# PowerShell 的坑：函数返回数组时会被枚举到管道，只有一个元素时调用方拿到的是
# 那个元素本身而不是数组。对大多数类型这无所谓（标量的 .Count 会兜底成 1），
# 但 PSCustomObject 有自己的属性查找，访问不存在的 .Count 得到的是 $null 而不是 1：
#
#   ([pscustomobject]@{A=1}).Count   ->  $null      （坑）
#   (Get-Item win.ini).Count         ->  1
#   'abc'.Count                      ->  1
#
# 所以任何返回 PSCustomObject 数组的函数，调用处必须用 @() 包一下，否则
# 「只有一条规则」这种最常见的输入会被静默当成「没有规则」，退回兜底逻辑。
function Invoke-LineEndingScan {
    param([string]$Root, [switch]$Fix)

    Write-SubTitle "行尾符 — $(if ($Fix) { 'fix（直接覆盖，无备份）' } else { 'scan（只读）' })"

    $files = @(Get-TargetFiles $Root)
    if ($files.Count -eq 0) {
        Write-Ok '没有找到需要检查的文本文件'
        return
    }

    # @() 不能省。见 Invoke-LineEndingScan 上方的注释：单条规则时函数返回的是
    # 单个 PSCustomObject，而 PSCustomObject 的 .Count 是 $null 不是 1。
    $rules = @(Get-GitAttributesEolRules $Root)
    if ($rules.Count -gt 0) {
        Write-Info "按 $Root\.gitattributes 的 eol 规则判定目标行尾（规则数：$($rules.Count)）"
    } else {
        Write-Info '没有 .gitattributes，用兜底规则：除 .bat/.cmd 外一律 LF'
    }

    $kindCounts = @{}
    $problems = New-Object System.Collections.Generic.List[object]
    $fixedCount = 0
    $skippedBinary = 0
    $skippedLarge = 0

    foreach ($file in $files) {
        $bytes = Read-FileBytes $file
        if ($null -eq $bytes) { $skippedLarge++; continue }
        if (Test-LooksBinary $bytes) { $skippedBinary++; continue }

        $statistics = Get-LineEndingStatistics $bytes
        $key = $statistics.Kind
        if (-not $kindCounts.ContainsKey($key)) { $kindCounts[$key] = 0 }
        $kindCounts[$key] = $kindCounts[$key] + 1

        $desired = Get-DesiredEol $file.Name $rules
        if ($desired -eq 'binary') { continue }
        if ($statistics.Kind -eq $desired) { continue }
        if ($statistics.Kind -eq 'none') { continue }

        $problems.Add([pscustomobject]@{
            File    = $file
            Current = $statistics.Kind
            Desired = $desired
        })

        if ($Fix) {
            $converted = Convert-BytesLineEndings -Bytes $bytes -TargetEol $desired
            try {
                # WriteAllBytes：绕开 PowerShell 默认"UTF-8 带 BOM"的坑，
                # 也绕开 WriteAllText 可能重新引入 CRLF 的坑。
                [IO.File]::WriteAllBytes($file.FullName, $converted)
                $fixedCount++
            } catch {
                Write-Bad "写入失败：$($file.FullName) ($($_.Exception.Message))"
            }
        }
    }

    Write-Host ''
    Write-Host "  扫描文本文件 $($files.Count) 个（跳过：二进制 $skippedBinary，过大 $skippedLarge）"
    foreach ($key in ($kindCounts.Keys | Sort-Object)) {
        Write-Host ("    {0,-8} {1}" -f $key, $kindCounts[$key])
    }

    if ($problems.Count -eq 0) {
        Write-Host ''
        Write-Ok '所有文件的行尾都已经符合规则'
        return
    }

    Write-Host ''
    if ($Fix) {
        Write-Ok "已改写 $fixedCount 个文件（直接覆盖，未留备份）"
    } else {
        Write-Warn "有 $($problems.Count) 个文件的行尾不符合规则"
    }

    $shown = [Math]::Min($problems.Count, $Limit)
    for ($i = 0; $i -lt $shown; $i++) {
        $p = $problems[$i]
        Write-Host ("    {0}  ->  {1}    {2}" -f $p.Current.PadRight(6), $p.Desired, (Get-DisplayPath $p.File.FullName $Root))
    }
    if ($problems.Count -gt $shown) {
        Write-Host "    ... 另有 $($problems.Count - $shown) 个，用 -Limit 调整显示条数"
    }

    if (-not $Fix -and $problems.Count -gt 0) {
        Write-Host ''
        Write-Info '修一遍：windows-dev.ps1 eol <目录> -Action fix'
    }
}

# guard 要保证的 .gitattributes 规则。语义是"钉死这几条"，不是"重写整个文件"：
# 文件已经存在时，只改这几条对应的行、补上缺失的，其他行原样保留。
# Note 用来在新建或追加时分组，只在与上一条不同时才写一行注释。
$script:GuardAttributeRules = @(
    @{ Pattern = '*';     Attributes = 'text=auto eol=lf'; Note = '仓库内统一存 LF；检出时也保持 LF' }
    @{ Pattern = '*.bat'; Attributes = 'text eol=crlf';     Note = 'Windows 批处理必须 CRLF，cmd.exe 对 LF 版本会解析错行' }
    @{ Pattern = '*.cmd'; Attributes = 'text eol=crlf';     Note = $null }
    @{ Pattern = '*.sh';  Attributes = 'text eol=lf';       Note = 'Shell 脚本永远 LF' }
    @{ Pattern = '*.png';  Attributes = 'binary'; Note = '二进制不要碰' }
    @{ Pattern = '*.jpg';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.jpeg'; Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.gif';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.ico';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.pdf';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.zip';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.gz';   Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.7z';   Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.woff'; Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.woff2';Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.ttf';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.eot';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.exe';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.dll';  Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.so';   Attributes = 'binary'; Note = $null }
    @{ Pattern = '*.dylib';Attributes = 'binary'; Note = $null }
)

# .editorconfig 同理，按段组织。root 是段之外的顶层键，单独处理。
$script:GuardEditorConfigSections = @(
    @{
        Section = '[*]'
        Note    = $null
        Keys    = @(
            @{ Key = 'end_of_line';              Value = 'lf' }
            @{ Key = 'charset';                  Value = 'utf-8' }
            @{ Key = 'insert_final_newline';     Value = 'true' }
            @{ Key = 'trim_trailing_whitespace'; Value = 'true' }
        )
    }
    @{
        Section = '[*.{bat,cmd}]'
        Note    = 'Windows 批处理例外'
        Keys    = @( @{ Key = 'end_of_line'; Value = 'crlf' } )
    }
)

# 编辑器原生配置。.editorconfig 是跨编辑器的通用约定，但每个编辑器还有自己的
# 一份；两份都写，没装 EditorConfig 插件的机器也能生效。
#
# VS Code 系（VS Code / Cursor / Trae / Windsurf / CodeBuddy）共用 .vscode/settings.json。
# files.eol 设成 "\n" 才是真的写 LF；留 "" 或 "auto" 就退回系统默认（Windows 上又是 CRLF）。
$script:GuardVsCodeSettings = @(
    @{ Key = 'files.eol';                    Value = '"\n"' }
    @{ Key = 'files.encoding';               Value = '"utf8"' }
    @{ Key = 'files.insertFinalNewline';     Value = 'true' }
    @{ Key = 'files.trimTrailingWhitespace'; Value = 'true' }
)

# Zed 的 line_ending 取值：detect / prefer_lf / prefer_crlf / enforce_lf / enforce_crlf。
# enforce_lf 才会在 format 与 save 时强制 LF。官方文档注明 .editorconfig 的
# end_of_line 会覆盖这一项，所以两份都写不冲突。
# Zed 没有编码设置项（它只认 UTF-8），所以这里不写 encoding。
$script:GuardZedSettings = @(
    @{ Key = 'line_ending';                        Value = '"enforce_lf"' }
    @{ Key = 'ensure_final_newline_on_save';       Value = 'true' }
    @{ Key = 'remove_trailing_whitespace_on_save'; Value = 'true' }
)

function Get-RuleIndexByPattern {
    param([object[]]$Rules, [string]$Pattern)

    for ($i = 0; $i -lt $Rules.Count; $i++) {
        if ($Rules[$i].Pattern -eq $Pattern) { return $i }
    }
    return -1
}

function Update-GitAttributesRules {
    param([string]$ExistingContent)

    $output = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    $rewrites = 0

    if (-not [string]::IsNullOrEmpty($ExistingContent)) {
        foreach ($line in ($ExistingContent -split "`r?`n")) {
            $trimmed = $line.Trim()

            # 注释与空行原样保留。
            if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) {
                $output.Add($line)
                continue
            }

            $tokens = $trimmed -split '\s+'
            $index = Get-RuleIndexByPattern -Rules $script:GuardAttributeRules -Pattern $tokens[0]
            if ($index -lt 0) {
                $output.Add($line)
                continue
            }

            # 归 guard 管的行：留下目标属性之外的其他属性（比如 working-tree-encoding），
            # 把 text / eol / binary 换成目标值。
            $kept = @()
            for ($i = 1; $i -lt $tokens.Count; $i++) {
                $token = $tokens[$i]
                # text 有三种写法（text / text=auto / -text），都得剔除，
                # 否则 text=auto 会被当成"其他属性"留下来、每跑一次重复追加一次。
                if ($token -like 'eol=*') { continue }
                if ($token -like 'text=*') { continue }
                if ($token -eq 'text' -or $token -eq '-text' -or $token -eq 'binary') { continue }
                $kept += $token
            }
            $rule = $script:GuardAttributeRules[$index]
            $newLine = "$($rule.Pattern) " + ((@($rule.Attributes) + $kept) -join ' ')
            $seen[$rule.Pattern] = $true
            if ($newLine -ne $trimmed) { $rewrites++ }
            $output.Add($newLine)
        }
    }

    $appended = 0
    $lastNote = $null
    foreach ($rule in $script:GuardAttributeRules) {
        if ($seen.ContainsKey($rule.Pattern)) { continue }
        # 只在换分组时才空一行。每条之间都空行会把文件撑得很散。
        if (-not [string]::IsNullOrEmpty($rule.Note) -and $rule.Note -ne $lastNote) {
            if ($output.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace($output[$output.Count - 1])) {
                $output.Add('')
            }
            $output.Add("# $($rule.Note)")
        }
        $lastNote = $rule.Note
        $output.Add("$($rule.Pattern) $($rule.Attributes)")
        $appended++
    }

    return [pscustomobject]@{
        Content  = ($output -join "`n")
        Rewrites = $rewrites
        Appended = $appended
    }
}

function Update-EditorConfigRules {
    param([string]$ExistingContent)

    $output = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($section in $script:GuardEditorConfigSections) { $seen[$section.Section] = @{} }
    $rewrites = 0
    $hasRoot = $false
    $currentSection = $null

    if (-not [string]::IsNullOrEmpty($ExistingContent)) {
        foreach ($line in ($ExistingContent -split "`r?`n")) {
            $trimmed = $line.Trim()

            if ($trimmed.StartsWith('[')) {
                $currentSection = $trimmed
                $output.Add($line)
                continue
            }
            if ([string]::IsNullOrWhiteSpace($trimmed) -or
                $trimmed.StartsWith('#') -or $trimmed.StartsWith(';')) {
                $output.Add($line)
                continue
            }

            $parts = $trimmed -split '=', 2
            if ($parts.Count -lt 2) { $output.Add($line); continue }
            $key = $parts[0].Trim().ToLowerInvariant()

            # root 是段之外的顶层键。
            if ($null -eq $currentSection -and $key -eq 'root') {
                $hasRoot = $true
                if ($parts[1].Trim().ToLowerInvariant() -ne 'true') {
                    $output.Add('root = true')
                    $rewrites++
                } else {
                    $output.Add($line)
                }
                continue
            }

            $target = $null
            foreach ($section in $script:GuardEditorConfigSections) {
                if ($section.Section -eq $currentSection) { $target = $section; break }
            }
            if ($null -eq $target) { $output.Add($line); continue }

            $keyRule = $null
            foreach ($k in $target.Keys) {
                if ($k.Key -eq $key) { $keyRule = $k; break }
            }
            if ($null -eq $keyRule) { $output.Add($line); continue }

            $seen[$currentSection][$key] = $true
            $newLine = "$($keyRule.Key) = $($keyRule.Value)"
            if ($newLine -ne $trimmed) { $rewrites++ }
            $output.Add($newLine)
        }
    }

    if (-not $hasRoot) {
        $output.Insert(0, 'root = true')
        if ($output.Count -gt 1) { $output.Insert(1, '') }
    }

    $appended = 0
    foreach ($section in $script:GuardEditorConfigSections) {
        $missing = @()
        foreach ($k in $section.Keys) {
            if (-not $seen[$section.Section].ContainsKey($k.Key)) { $missing += $k }
        }
        if ($missing.Count -eq 0) { continue }

        if ($seen[$section.Section].Count -gt 0) {
            # 段已存在：插到该段最后一行之后、下一个段之前。
            $insertAt = -1
            $inside = $false
            for ($i = 0; $i -lt $output.Count; $i++) {
                $t = $output[$i].Trim()
                if ($t -eq $section.Section) { $inside = $true; continue }
                if ($inside -and $t.StartsWith('[')) { break }
                if ($inside -and -not [string]::IsNullOrWhiteSpace($t) -and
                    -not $t.StartsWith('#') -and -not $t.StartsWith(';')) { $insertAt = $i }
            }
            if ($insertAt -lt 0) { $insertAt = $output.Count - 1 }
            $lines = New-Object System.Collections.Generic.List[string]
            foreach ($k in $missing) { $lines.Add("$($k.Key) = $($k.Value)"); $appended++ }
            $output.InsertRange($insertAt + 1, $lines)
        } else {
            if ($output.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace($output[$output.Count - 1])) {
                $output.Add('')
            }
            if (-not [string]::IsNullOrEmpty($section.Note)) { $output.Add("# $($section.Note)") }
            $output.Add($section.Section)
            foreach ($k in $missing) { $output.Add("$($k.Key) = $($k.Value)"); $appended++ }
        }
    }

    return [pscustomobject]@{
        Content  = ($output -join "`n")
        Rewrites = $rewrites
        Appended = $appended
    }
}

# 编辑器配置是 JSONC（VS Code 和 Zed 都允许注释与尾随逗号），所以不能用
# ConvertFrom-Json / ConvertTo-Json 往返：PS 5.1 的 ConvertFrom-Json 遇到注释直接报错，
# 而且往返会重排键顺序、改掉缩进、丢掉注释。这里做文本级合并：
# 目标键存在就换值，不存在就在根对象开头插入，其余内容一个字节都不动。
#
# 只处理根对象里的标量键。返回 $null 表示这个文件不是 JSON 对象，交给调用方报错。
function Update-JsonSettings {
    param([string]$ExistingContent, [object[]]$Settings)

    $rewrites = 0
    $appended = 0

    if ([string]::IsNullOrWhiteSpace($ExistingContent)) {
        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add('{')
        for ($i = 0; $i -lt $Settings.Count; $i++) {
            $comma = if ($i -lt $Settings.Count - 1) { ',' } else { '' }
            $lines.Add("  `"$($Settings[$i].Key)`": $($Settings[$i].Value)$comma")
        }
        $lines.Add('}')
        return [pscustomobject]@{
            Content  = ($lines -join "`n")
            Rewrites = 0
            Appended = $Settings.Count
        }
    }

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($ExistingContent -split "`r?`n")) { $lines.Add($line) }

    # 定位根对象（跳过开头的注释行）。
    $openIndex = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $t = $lines[$i].Trim()
        if ($t.StartsWith('//') -or $t.StartsWith('/*')) { continue }
        if ($t.Contains('{')) { $openIndex = $i; break }
    }
    if ($openIndex -lt 0) { return $null }

    $closeIndex = -1
    for ($i = $lines.Count - 1; $i -gt $openIndex; $i--) {
        if ($lines[$i].Trim().Contains('}')) { $closeIndex = $i; break }
    }
    if ($closeIndex -lt 0) { return $null }

    # 缩进跟文件里现有的条目保持一致（2 空格还是 4 空格，别自作主张换风格）。
    $indentUnit = '  '
    for ($j = $openIndex + 1; $j -lt $closeIndex; $j++) {
        $t = $lines[$j]
        if ([string]::IsNullOrWhiteSpace($t)) { continue }
        if ($t.Trim().StartsWith('//') -or $t.Trim().StartsWith('/*')) { continue }
        $match = [regex]::Match($t, '^(\s+)')
        if ($match.Success) { $indentUnit = $match.Groups[1].Value }
        break
    }

    # 先替换已存在的键。
    $missing = New-Object System.Collections.Generic.List[object]
    foreach ($setting in $Settings) {
        $pattern = '^(\s*)"' + [regex]::Escape($setting.Key) + '"\s*:\s*(.*?)(,?)\s*$'
        $found = $false
        for ($i = $openIndex + 1; $i -lt $closeIndex; $i++) {
            $match = [regex]::Match($lines[$i], $pattern)
            if (-not $match.Success) { continue }

            $indent = $match.Groups[1].Value
            $comma = $match.Groups[3].Value
            $newLine = "$indent`"$($setting.Key)`": $($setting.Value)$comma"
            if ($newLine -ne $lines[$i]) { $rewrites++ }
            $lines[$i] = $newLine
            $found = $true
            break
        }
        if (-not $found) { $missing.Add($setting) }
    }

    # 插入位置：跳过根对象开头那段注释与空行，否则会把注释挤到文件中间。
    $insertAt = $openIndex + 1
    while ($insertAt -lt $closeIndex) {
        $t = $lines[$insertAt].Trim()
        if ([string]::IsNullOrEmpty($t) -or $t.StartsWith('//') -or $t.StartsWith('/*')) {
            $insertAt++
            continue
        }
        break
    }

    # 倒序插入：每次都插在最前面，于是"后面还有没有内容"这个判断永远成立，
    # 逗号才不会错位。正序插会让第一条漏掉逗号、最后一条多一个尾随逗号。
    for ($i = $missing.Count - 1; $i -ge 0; $i--) {
        $setting = $missing[$i]
        $hasContentAfter = $false
        for ($j = $insertAt; $j -lt $closeIndex; $j++) {
            $t = $lines[$j].Trim()
            if ([string]::IsNullOrEmpty($t)) { continue }
            if ($t.StartsWith('//') -or $t.StartsWith('/*')) { continue }
            $hasContentAfter = $true
            break
        }
        $comma = if ($hasContentAfter) { ',' } else { '' }

        $lines.Insert($insertAt, "$indentUnit`"$($setting.Key)`": $($setting.Value)$comma")
        $closeIndex++
        $appended++
    }

    return [pscustomobject]@{
        Content  = ($lines -join "`n")
        Rewrites = $rewrites
        Appended = $appended
    }
}

function Invoke-LineEndingGuard {
    param([string]$Root)

    Write-SubTitle '行尾符 — guard（确保配置文件里的规则正确）'

    $item = Get-Item -LiteralPath $Root -ErrorAction SilentlyContinue
    if ($null -eq $item -or -not $item.PSIsContainer) {
        Write-Bad "需要一个目录：$Root"
        return
    }

    foreach ($entry in @(
        @{ Name = '.gitattributes';        Kind = 'attributes' }
        @{ Name = '.editorconfig';         Kind = 'editorconfig' }
        @{ Name = '.vscode/settings.json'; Kind = 'vscode' }
        @{ Name = '.zed/settings.json';    Kind = 'zed' }
    )) {
        $target = Join-Path $item.FullName $entry.Name

        $existing = $null
        if (Test-Path -LiteralPath $target) {
            $existing = [IO.File]::ReadAllText($target)
        }

        switch ($entry.Kind) {
            'attributes'   { $result = Update-GitAttributesRules -ExistingContent $existing }
            'editorconfig' { $result = Update-EditorConfigRules  -ExistingContent $existing }
            'vscode'       { $result = Update-JsonSettings -ExistingContent $existing -Settings $script:GuardVsCodeSettings }
            'zed'          { $result = Update-JsonSettings -ExistingContent $existing -Settings $script:GuardZedSettings }
        }

        if ($null -eq $result) {
            Write-Warn "$($entry.Name) 不是 JSON 对象，跳过（先自己检查格式）"
            continue
        }

        $content = $result.Content -replace "`r`n", "`n"

        if ($null -ne $existing -and $content -eq ($existing -replace "`r`n", "`n")) {
            Write-Ok "$($entry.Name) 已合规，无需改动"
            continue
        }

        try {
            # 显式写成 UTF-8 无 BOM + LF。默认编码会把中文注释写坏。
            $encoding = New-Object Text.UTF8Encoding($false)

            # 编辑器配置通常在子目录里，第一次跑时目录还不存在。
            $directory = Split-Path -Parent $target
            if (-not (Test-Path -LiteralPath $directory)) {
                New-Item -ItemType Directory -Path $directory -Force | Out-Null
            }

            [IO.File]::WriteAllText($target, $content, $encoding)

            if ($null -eq $existing) {
                Write-Ok "已创建 $($entry.Name)"
            } else {
                Write-Ok "已更新 $($entry.Name)（改写 $($result.Rewrites) 行，追加 $($result.Appended) 行，其余规则保留）"
            }
        } catch {
            Write-Bad "写入失败：$target ($($_.Exception.Message))"
        }
    }

    Write-Host ''
    Write-Info '存量文件还需要 renormalize 一次才会真正生效：'
    Write-Host '    git add --renormalize .' -ForegroundColor DarkGray
    Write-Host '    git commit -m "统一仓库换行符为 LF"' -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------

# 裸 UTF-16（没写 BOM）的识别。
#
# ASCII 文本编码成 UTF-16 后，每隔一个字节就是 0x00。麻烦在于 0x00 同时是一个
# 完全合法的 UTF-8 字符(U+0000)，所以严格 UTF-8 校验会把它整段"合法"读过去，
# 把一个 UTF-16 文件判定成 utf8 —— 内容全是 "u\0n\0i\0" 这种鬼东西却没人报警。
# PowerShell 的 `>` 与 Out-File 在 5.1 上产出的就是这类文件，是重灾区。
#
# 判据：采样区里要么偶数位几乎全是 NUL（大端），要么奇数位几乎全是 NUL（小端），
# 而另一侧几乎没有。真 UTF-16 文本的 NUL 侧接近 100%。
function Test-BareUtf16Signature {
    param([byte[]]$Bytes)

    if ($Bytes.Length -lt 4) { return $null }

    $probeLength = [Math]::Min($Bytes.Length, 4096)
    if ($probeLength % 2 -ne 0) { $probeLength-- }
    if ($probeLength -lt 4) { return $null }

    $evenNull = 0
    $oddNull = 0
    for ($i = 0; $i -lt $probeLength; $i += 2) {
        if ($Bytes[$i] -eq 0) { $evenNull++ }
        if ($Bytes[$i + 1] -eq 0) { $oddNull++ }
    }

    $half = $probeLength / 2
    if ($oddNull -ge $half * 0.5 -and $evenNull -le $half * 0.1) { return 'utf16-le' }
    if ($evenNull -ge $half * 0.5 -and $oddNull -le $half * 0.1) { return 'utf16-be' }
    return $null
}

function Get-FileEncodingInfo {
    param([byte[]]$Bytes)

    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        return 'utf8-bom'
    }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) {
        if ($Bytes.Length -ge 4 -and $Bytes[2] -eq 0x00 -and $Bytes[3] -eq 0x00) { return 'utf32-le' }
        return 'utf16-le'
    }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFE -and $Bytes[1] -eq 0xFF) {
        return 'utf16-be'
    }
    if ($Bytes.Length -ge 4 -and $Bytes[0] -eq 0x00 -and $Bytes[1] -eq 0x00 -and
        $Bytes[2] -eq 0xFE -and $Bytes[3] -eq 0xFF) {
        return 'utf32-be'
    }
    if ($Bytes.Length -eq 0) { return 'empty' }

    # 必须在 UTF-8 校验之前拦：见 Test-BareUtf16Signature 的说明。
    $bareUtf16 = Test-BareUtf16Signature $Bytes
    if ($null -ne $bareUtf16) { return $bareUtf16 }

    # 严格 UTF-8 校验：非法序列直接抛，而不是像默认解码器那样替换成 U+FFFD。
    # 用替换字符的话，"是不是 UTF-8"这个问题就永远得不到确定答案。
    $strict = New-Object Text.UTF8Encoding($false, $true)
    try {
        $null = $strict.GetString($Bytes)
        return 'utf8'
    } catch {
        return 'non-utf8'
    }
}

function Get-AnsiEncoding {
    # .NET Core 默认不带 GBK 等代码页，需要显式注册 provider。
    try {
        return [Text.Encoding]::GetEncoding(936)
    } catch {
        try {
            [Text.Encoding]::RegisterProvider([Text.CodePagesEncodingProvider]::Instance)
            return [Text.Encoding]::GetEncoding(936)
        } catch {
            return [Text.Encoding]::Default
        }
    }
}

function Invoke-EncodingScan {
    param([string]$Root, [switch]$Fix)

    Write-SubTitle "字符编码 — $(if ($Fix) { 'fix（直接覆盖，无备份）' } else { 'scan（只读）' })"

    $files = @(Get-TargetFiles $Root)
    if ($files.Count -eq 0) {
        Write-Ok '没有找到需要检查的文本文件'
        return
    }

    # UTF-16 含大量 NUL，会被 Test-LooksBinary 判为二进制；这里不能走那条路，
    # 否则 PowerShell 重定向产出的 UTF-16 文件（最常见的坑）会被完全漏掉。
    $kindCounts = @{}
    $problems = New-Object System.Collections.Generic.List[object]
    $fixedCount = 0
    $skippedBinary = 0
    $skippedLarge = 0

    foreach ($file in $files) {
        $bytes = Read-FileBytes $file
        if ($null -eq $bytes) { $skippedLarge++; continue }

        $encodingKind = Get-FileEncodingInfo $bytes
        if ($encodingKind -eq 'binary') { $skippedBinary++; continue }

        # 非 UTF-8 家族且含 NUL 的，才是真二进制。
        if ($encodingKind -eq 'non-utf8' -and (Test-LooksBinary $bytes)) { $skippedBinary++; continue }

        if (-not $kindCounts.ContainsKey($encodingKind)) { $kindCounts[$encodingKind] = 0 }
        $kindCounts[$encodingKind] = $kindCounts[$encodingKind] + 1

        $extension = ([IO.Path]::GetExtension($file.Name)).ToLowerInvariant()
        $keepBom = $script:BomKeepExtensions -contains $extension

        $desired = 'utf8'
        if ($encodingKind -eq 'utf8-bom' -and $keepBom) { $desired = 'utf8-bom' }

        if ($encodingKind -eq $desired) { continue }
        if ($encodingKind -eq 'empty') { continue }

        $problems.Add([pscustomobject]@{
            File     = $file
            Current  = $encodingKind
            Desired  = $desired
            KeepBom  = $keepBom
        })

        if ($Fix) {
            try {
                switch ($encodingKind) {
                    'utf8-bom' {
                        $text = [Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
                    }
                    'utf16-le' { $text = [Text.Encoding]::Unicode.GetString($bytes) }
                    'utf16-be' { $text = [Text.Encoding]::BigEndianUnicode.GetString($bytes) }
                    'utf32-le' { $text = [Text.Encoding]::UTF32.GetString($bytes) }
                    'utf32-be' { $text = (New-Object Text.UTF32Encoding($true, $false)).GetString($bytes) }
                    default {
                        # non-utf8：按中文 Windows 的 ANSI 代码页 936 解。
                        $text = (Get-AnsiEncoding).GetString($bytes)
                    }
                }

                # 统一成 LF 再写：否则转编码的同时会把 CRLF 一起固化下去。
                $text = $text -replace "`r`n", "`n"

                if ($keepBom) {
                    $targetEncoding = New-Object Text.UTF8Encoding($true)
                } else {
                    $targetEncoding = New-Object Text.UTF8Encoding($false)
                }

                [IO.File]::WriteAllText($file.FullName, $text, $targetEncoding)
                $fixedCount++
            } catch {
                Write-Bad "转换失败：$($file.FullName) ($($_.Exception.Message))"
            }
        }
    }

    Write-Host ''
    Write-Host "  扫描文本文件 $($files.Count) 个（跳过：二进制 $skippedBinary，过大 $skippedLarge）"
    foreach ($key in ($kindCounts.Keys | Sort-Object)) {
        Write-Host ("    {0,-12} {1}" -f $key, $kindCounts[$key])
    }

    if ($problems.Count -eq 0) {
        Write-Host ''
        Write-Ok '所有文件的编码都已经是 UTF-8 家族'
        return
    }

    Write-Host ''
    if ($Fix) {
        Write-Ok "已改写 $fixedCount 个文件（直接覆盖，未留备份）"
    } else {
        Write-Warn "有 $($problems.Count) 个文件的编码需要转换"
    }

    $shown = [Math]::Min($problems.Count, $Limit)
    for ($i = 0; $i -lt $shown; $i++) {
        $p = $problems[$i]
        Write-Host ("    {0}  ->  {1}   {2}" -f $p.Current.PadRight(10), $p.Desired, (Get-DisplayPath $p.File.FullName $Root))
    }
    if ($problems.Count -gt $shown) {
        Write-Host "    ... 另有 $($problems.Count - $shown) 个，用 -Limit 调整显示条数"
    }

    if (-not $Fix -and $problems.Count -gt 0) {
        Write-Host ''
        Write-Info '修一遍：windows-dev.ps1 encoding <目录> -Action fix'
        Write-Info '注意：.ps1/.psm1/.psd1 会保留 BOM —— Windows PowerShell 5.1 靠它判断编码。'
    }
}

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

$script:ReservedDeviceNames = @(
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
)

function Invoke-PathScan {
    param([string]$Root)

    Write-SubTitle '文件路径 — scan（只读；这类问题没有安全的自动修法）'

    $item = Get-Item -LiteralPath $Root -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        Write-Bad "路径不存在：$Root"
        return
    }
    $rootFullName = $item.FullName.TrimEnd('\', '/')

    $entries = @(Get-ChildItem -LiteralPath $item.FullName -Recurse `
                   -ErrorAction SilentlyContinue -ErrorVariable enumerationErrors)

    if ($entries.Count -eq 0) {
        Write-Ok '目录为空'
        return
    }

    $tooLong = New-Object System.Collections.Generic.List[string]
    $illegalCharacters = New-Object System.Collections.Generic.List[string]
    $reservedNames = New-Object System.Collections.Generic.List[string]
    $trailingSpaceOrDot = New-Object System.Collections.Generic.List[string]
    $spacesInName = New-Object System.Collections.Generic.List[string]

    foreach ($entry in $entries) {
        $name = $entry.Name
        $fullName = $entry.FullName

        if ($fullName.Length -ge 260) {
            $tooLong.Add($fullName)
        }

        if ($name -match '[<>:"/\\|?*]') {
            $illegalCharacters.Add($fullName)
        }
        if ($name -match '[\x00-\x1f]') {
            $illegalCharacters.Add($fullName)
        }

        $stem = [IO.Path]::GetFileNameWithoutExtension($name).ToUpperInvariant()
        if ($script:ReservedDeviceNames -contains $stem) {
            $reservedNames.Add($fullName)
        }

        if ($name -ne $name.TrimEnd(' ', '.')) {
            $trailingSpaceOrDot.Add($fullName)
        }

        if ($name -match '\s') {
            $spacesInName.Add($fullName)
        }
    }

    $hasProblem = $false

    if ($tooLong.Count -gt 0) {
        $hasProblem = $true
        Write-Warn "超过 260 字符（MAX_PATH）的路径 $($tooLong.Count) 条 —— 读写都会失败"
        foreach ($path in ($tooLong | Select-Object -First $Limit)) {
            # 这一项要显示绝对长度，否则看不出"长"在哪；路径本身仍用相对形式免得折行。
            Write-Host ("    {0} 字符  {1}" -f $path.Length, (Get-DisplayPath $path $rootFullName)) -ForegroundColor DarkGray
        }
    }

    if ($illegalCharacters.Count -gt 0) {
        $hasProblem = $true
        Write-Bad "含 Windows 非法字符的路径 $($illegalCharacters.Count) 条"
        foreach ($path in ($illegalCharacters | Select-Object -First $Limit)) {
            Write-Host ("    {0}" -f (Get-DisplayPath $path $rootFullName)) -ForegroundColor DarkGray
        }
    }

    if ($reservedNames.Count -gt 0) {
        $hasProblem = $true
        Write-Bad "用了保留设备名（CON/NUL/COM1...）的路径 $($reservedNames.Count) 条"
        foreach ($path in ($reservedNames | Select-Object -First $Limit)) {
            Write-Host ("    {0}" -f (Get-DisplayPath $path $rootFullName)) -ForegroundColor DarkGray
        }
    }

    if ($trailingSpaceOrDot.Count -gt 0) {
        $hasProblem = $true
        Write-Bad "结尾是空格或点（Windows 会静默吃掉）的路径 $($trailingSpaceOrDot.Count) 条"
        foreach ($path in ($trailingSpaceOrDot | Select-Object -First $Limit)) {
            Write-Host ("    {0}" -f (Get-DisplayPath $path $rootFullName)) -ForegroundColor DarkGray
        }
    }

    if ($spacesInName.Count -gt 0) {
        Write-Info "名字里有空格的路径 $($spacesInName.Count) 条（合法，但在 shell 里要记得加引号）"
        foreach ($path in ($spacesInName | Select-Object -First 3)) {
            Write-Host ("    {0}" -f (Get-DisplayPath $path $rootFullName)) -ForegroundColor DarkGray
        }
    }

    if ($enumerationErrors.Count -gt 0) {
        Write-Warn "枚举时有 $($enumerationErrors.Count) 个条目读取失败（长路径/权限），这部分无法判断"
    }

    if (-not $hasProblem) {
        Write-Ok '没有发现路径问题'
    }

    # 大小写冲突只能查 Git 索引，不能查工作区。
    #
    # Windows 文件系统大小写不敏感，所以同一目录下压根存不下 Foo.ts 和 foo.ts
    # 两个文件——它们要么在检出时就互相覆盖了，要么根本没检出来。工作区扫描
    # 永远扫不到这个冲突，扫了也是白扫。但 Git 索引是纯文本，里面可以老老实实
    # 记着两个名字，所以真正的证据在 `git ls-files` 里。
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -ne $git -and (Test-Path -LiteralPath (Join-Path $rootFullName '.git'))) {
        Write-Host ''
        $previousLocation = Get-Location
        try {
            Set-Location -LiteralPath $rootFullName
            $indexed = @(& git ls-files 2>$null)
            if ($LASTEXITCODE -eq 0 -and $indexed.Count -gt 0) {
                $indexGroups = @{}
                foreach ($line in $indexed) {
                    $key = $line.ToLowerInvariant()
                    if (-not $indexGroups.ContainsKey($key)) {
                        $indexGroups[$key] = New-Object System.Collections.Generic.List[string]
                    }
                    $indexGroups[$key].Add($line)
                }
                $indexConflicts = @()
                foreach ($key in $indexGroups.Keys) {
                    if ($indexGroups[$key].Count -le 1) { continue }
                    $distinct = @($indexGroups[$key] | Sort-Object -CaseSensitive -Unique)
                    if ($distinct.Count -gt 1) { $indexConflicts += $distinct }
                }
                if ($indexConflicts.Count -gt 0) {
                    Write-Bad "Git 索引里有 $($indexConflicts.Count) 个仅大小写不同的条目"
                    foreach ($line in ($indexConflicts | Select-Object -First $Limit)) {
                        Write-Host "    $line" -ForegroundColor DarkGray
                    }
                    Write-Info '修法：git mv 成同一个名字，或在 Linux 上处理'
                } else {
                    Write-Ok 'Git 索引里没有大小写冲突'
                }
            }
        } catch {
            Write-Info 'Git 索引检查跳过'
        } finally {
            Set-Location -LiteralPath $previousLocation
        }
    }
}

# ---------------------------------------------------------------------------
# 环境体检
# ---------------------------------------------------------------------------

function Get-RegistryValueSafely {
    param([string]$Path, [string]$Name)

    try {
        $key = Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop
        return $key.$Name
    } catch {
        return $null
    }
}

function Invoke-EnvironmentReport {
    Write-SubTitle '环境体检 — 只读，只给修复命令，不自动改'

    Write-Host ''
    Write-Host "  PowerShell      $($PSVersionTable.PSVersion)"
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        Write-Info 'Windows PowerShell 5.1：读无 BOM 的 .ps1 会按 ANSI 解码，中文会乱码。'
        Write-Info '  给 .ps1 保留 UTF-8 BOM，或者装 pwsh 7+。'
    }

    $policy = (Get-ExecutionPolicy -Scope CurrentUser -ErrorAction SilentlyContinue)
    Write-Host "  执行策略(用户)  $policy"
    if ($policy -eq 'Restricted' -or $policy -eq 'Undefined') {
        Write-Info '跑脚本时用：powershell -NoProfile -ExecutionPolicy Bypass -File <脚本>'
    }

    $consoleEncoding = 'unknown'
    try { $consoleEncoding = [Console]::OutputEncoding.WebName } catch { }
    Write-Host "  控制台输出编码  $consoleEncoding"

    Write-Host ''
    $longPaths = Get-RegistryValueSafely `
        -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled'
    if ($longPaths -eq 1) {
        Write-Ok '长路径(LongPathsEnabled) 已开启'
    } else {
        Write-Warn '长路径未开启 —— 超过 260 字符的路径读写会失败'
        Write-Host '    开启（需管理员）：' -ForegroundColor DarkGray
        Write-Host '    New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `' -ForegroundColor DarkGray
        Write-Host '      -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force' -ForegroundColor DarkGray
    }

    $activeCodePage = Get-RegistryValueSafely `
        -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage' -Name 'ACP'
    Write-Host "  系统 ANSI 代码页 $activeCodePage"
    if ($activeCodePage -eq '65001') {
        Write-Info '已开启全局 UTF-8(Beta)。好处是 GBK 坑消失，代价是老的非 Unicode 程序会乱码。'
    } else {
        Write-Info '未开启全局 UTF-8。' 
        Write-Info '  开启能根治 GBK 问题，但会影响所有非 Unicode 老程序，需自己权衡。'
    }

    $developerMode = Get-RegistryValueSafely `
        -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' `
        -Name 'AllowDevelopmentWithoutDevLicense'
    if ($developerMode -eq 1) {
        Write-Ok '开发者模式已开启（可以无管理员创建符号链接）'
    } else {
        Write-Info '开发者模式未开启 —— mklink 创建符号链接需要管理员'
    }

    Write-Host ''
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        Write-Warn '没有找到 git'
        return
    }

    Write-Host "  git             $((& git --version) -join '')"
    foreach ($name in @('core.autocrlf', 'core.safecrlf', 'core.longpaths', 'core.quotepath', 'core.ignorecase')) {
        $value = (& git config --global --get $name 2>$null)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrEmpty($value)) { $value = '(未设置)' }
        Write-Host ("  {0,-16} {1}" -f $name, $value)
    }

    $autocrlf = (& git config --global --get core.autocrlf 2>$null)
    if ($autocrlf -ne 'input') {
        Write-Host ''
        Write-Info '推荐（跨平台项目）：git config --global core.autocrlf input'
        Write-Info '  提交时 CRLF->LF，检出时保持 LF。.gitattributes 的规则会覆盖它。'
    }

    $quotepath = (& git config --global --get core.quotepath 2>$null)
    if ($quotepath -ne 'false') {
        Write-Info '推荐：git config --global core.quotepath false （中文路径不再显示成 \344\275\240）'
    }

    $longPathsGit = (& git config --global --get core.longpaths 2>$null)
    if ($longPathsGit -ne 'true') {
        Write-Info '推荐：git config --global core.longpaths true （配合系统长路径开关）'
    }
}

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if ($env:OS -ne 'Windows_NT') {
    Write-Bad '这个脚本只面向 Windows'
    exit 1
}

if ($Command -eq 'env' -and $Action -ne 'scan') {
    Write-Info 'env 永远只读，忽略 action'
    $Action = 'scan'
}
if ($Command -eq 'path' -and $Action -ne 'scan') {
    Write-Warn 'path 没有安全的自动修法，强制走 scan'
    $Action = 'scan'
}
if ($Command -eq 'encoding' -and $Action -eq 'guard') {
    Write-Bad 'encoding 没有 guard（编码靠 .editorconfig 的 charset 约束，那是 eol guard 写的）'
    exit 1
}

$resolvedPaths = @()
foreach ($p in $Path) {
    $item = Get-Item -LiteralPath $p -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        Write-Bad "路径不存在，跳过：$p"
        continue
    }
    $resolvedPaths += $item.FullName
}
if ($resolvedPaths.Count -eq 0) {
    Write-Bad '没有可用的路径'
    exit 1
}

$commands = if ($Command -eq 'doctor') { @('eol', 'encoding', 'path', 'env') } else { @($Command) }

Write-SectionTitle "windows-dev — $Command $(if ($Command -ne 'doctor' -and $Command -ne 'env') { $Action })"
Write-Host "  扫描根：$($resolvedPaths -join ', ')" -ForegroundColor DarkGray
Write-Host "  时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray

foreach ($command in $commands) {
    switch ($command) {
        'eol' {
            foreach ($root in $resolvedPaths) {
                if ($Action -eq 'guard') { Invoke-LineEndingGuard $root }
                else { Invoke-LineEndingScan $Root -Fix:($Action -eq 'fix') }
            }
        }
        'encoding' {
            foreach ($root in $resolvedPaths) {
                Invoke-EncodingScan $root -Fix:($Action -eq 'fix')
            }
        }
        'path' {
            foreach ($root in $resolvedPaths) { Invoke-PathScan $root }
        }
        'env' {
            Invoke-EnvironmentReport
        }
    }
}

if ($Command -eq 'doctor') {
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkCyan
    Write-Host ' doctor 只做只读检查，没有改动任何文件。' -ForegroundColor Cyan
    Write-Host ' 要修：windows-dev.ps1 eol <目录> -Action fix  /  encoding <目录> -Action fix' -ForegroundColor DarkGray
    Write-Host ('=' * 78) -ForegroundColor DarkCyan
}
