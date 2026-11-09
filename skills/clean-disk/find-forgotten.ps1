<#
.SYNOPSIS
    只读审计：找出「被遗忘但占着空间」的东西。不删除任何文件。

.DESCRIPTION
    clean-disk.ps1 清的是「缓存」；本脚本找的是它按设计不碰的那一类：
      - 巨大的单文件（一次性打包的 tar/zip、镜像、转储）
      - 孤儿的包管理器安装（anaconda / miniconda / miniforge / mambaforge）
      - 大但**不能删**的系统项目（pagefile / hiberfil / WinSxS / Windows\Installer /
        ProgramData\Package Cache）——列出来是为了让读者别删错
      - 非管理员量不到的位置（System Volume Information 等）——说明为什么要提权
      - 陈旧的大缓存（半年没写过的 IDE 索引、迁移备份、*\backups）

    每个条目给一个结论列：可删 / 需确认 / 不要删 / 需管理员。
    删除永远由人做（或由人明确要求的下一步），本脚本只测量。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\find-forgotten.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\find-forgotten.ps1 -MinMB 100 -Top 40
#>
[CmdletBinding()]
param(
    [int]$MinMB = 300,
    [int]$Top = 25,
    [int]$StaleDays = 120
)

$ErrorActionPreference = 'SilentlyContinue'
$minBytes = [long]$MinMB * 1MB
$staleCut = (Get-Date).AddDays(-$StaleDays)

function Format-Size([long]$b) {
    if ($b -ge 1GB) { return ('{0:N2} GiB' -f ($b / 1GB)) }
    if ($b -ge 1MB) { return ('{0:N1} MiB' -f ($b / 1MB)) }
    return ('{0:N0} KiB' -f ($b / 1KB))
}

function Get-DirSize([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return [long]0 }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return [long]$item.Length }
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if ($null -eq $sum) { return [long]0 }
    return [long]$sum
}

Write-Host ''
Write-Host '==================== 被遗忘的空间审计（只读） ====================' -ForegroundColor White
Write-Host (' 时间: {0}' -f (Get-Date))
Write-Host (' 阈值: >= {0} MB 才算大；{1} 天没写过算陈旧' -f $MinMB, $StaleDays)

$volumes = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue)
Write-Host ''
Write-Host '-- 1. 卷 --' -ForegroundColor Green
foreach ($v in $volumes) {
    Write-Host ('  {0}  free={1}  size={2}' -f $v.DeviceID, (Format-Size ([long]$v.FreeSpace)), (Format-Size ([long]$v.Size)))
}

# 单趟遍历：大文件、陈旧大文件、镜像类、孤儿安装，一次拿全
$bigFiles = New-Object System.Collections.Generic.List[object]
$staleFiles = New-Object System.Collections.Generic.List[object]
$images = New-Object System.Collections.Generic.List[object]
$orphanInstalls = New-Object System.Collections.Generic.List[object]
$archiveExt = @('.tar', '.zip', '.7z', '.gz', '.tgz', '.iso', '.vhdx', '.vhd', '.vmdk', '.wim', '.dmp', '.bak')
$installLeaf = '^(?i)(anaconda3?|miniconda3?|miniforge3?|mambaforge|micromamba)$'
$pruneLeaf = @('System Volume Information', 'WinSxS', '$Recycle.Bin', 'node_modules', '.git')

foreach ($v in $volumes) {
    $root = $v.DeviceID + [char]92
    $stack = New-Object System.Collections.Generic.Stack[string]
    $stack.Push($root)
    while ($stack.Count) {
        $dir = $stack.Pop()
        $leafName = [IO.Path]::GetFileName($dir.TrimEnd([char]92))
        if ($leafName -match $installLeaf) {
            $orphanInstalls.Add([pscustomobject]@{ Path = $dir; Size = (Get-DirSize $dir); Note = '包管理器安装' })
            continue
        }
        try {
            foreach ($f in [IO.Directory]::EnumerateFiles($dir)) {
                try { $fi = [IO.FileInfo]::new($f); $len = $fi.Length } catch { continue }
                if ($len -ge $minBytes) {
                    $bigFiles.Add([pscustomobject]@{ Path = $f; Size = $len; When = $fi.LastWriteTime })
                    if ($fi.LastWriteTime -lt $staleCut) {
                        $staleFiles.Add([pscustomobject]@{ Path = $f; Size = $len; When = $fi.LastWriteTime })
                    }
                    if ($archiveExt -contains [IO.Path]::GetExtension($f).ToLower()) {
                        $images.Add([pscustomobject]@{ Path = $f; Size = $len; When = $fi.LastWriteTime })
                    }
                }
            }
        } catch { }
        try {
            foreach ($sub in [IO.Directory]::EnumerateDirectories($dir)) {
                $subLeaf = [IO.Path]::GetFileName($sub.TrimEnd([char]92))
                if ($pruneLeaf -contains $subLeaf) { continue }
                if ($subLeaf -match '^(?i)(backups?|old|.*\.old)$') {
                    $sz = Get-DirSize $sub
                    if ($sz -ge $minBytes) { $orphanInstalls.Add([pscustomobject]@{ Path = $sub; Size = $sz; Note = '备份目录' }) }
                    continue
                }
                try { if ([IO.File]::GetAttributes($sub) -band [IO.FileAttributes]::ReparsePoint) { continue } } catch { continue }
                $stack.Push($sub)
            }
        } catch { }
    }
}

# 这些是「大但不要删」的：第 2 节也要照着标，否则读者会在第 2 节看见 pagefile 就想动它
$keepPaths = @(
    'C:\pagefile.sys', 'C:\hiberfil.sys', 'C:\Windows\WinSxS',
    'C:\Windows\Installer', 'C:\ProgramData\Package Cache'
)

Write-Host ''
Write-Host ('-- 2. 最大的 {0} 个文件 --' -f $Top) -ForegroundColor Green
Write-Host '  大小        最后写入          结论        路径' -ForegroundColor DarkGray
$bigFiles | Sort-Object Size -Descending | Select-Object -First $Top | ForEach-Object {
    $verdict = if ($keepPaths -contains $_.Path) { '不要删' }
                elseif ($_.When -lt $staleCut) { '陈旧·需确认' }
                else { '在变·需确认' }
    Write-Host ('  {0,-11} {1,-17} {2,-11} {3}' -f (Format-Size $_.Size), $_.When.ToString('yyyy-MM-dd HH:mm'), $verdict, $_.Path)
}

Write-Host ''
Write-Host '-- 3. 孤儿安装 / 备份目录（默认不会被任何清理扫到） --' -ForegroundColor Green
if ($orphanInstalls.Count -eq 0) {
    Write-Host '  无' -ForegroundColor DarkGray
} else {
    Write-Host '  大小        结论          路径' -ForegroundColor DarkGray
    $orphanInstalls | Sort-Object Size -Descending | Select-Object -First 20 | ForEach-Object {
        $verdict = if ($_.Note -eq '包管理器安装') { '需确认·装过' } else { '需确认·备份' }
        Write-Host ('  {0,-11} {1,-13} {2}' -f (Format-Size $_.Size), $verdict, $_.Path)
    }
}

Write-Host ''
Write-Host '-- 4. 一次性打包 / 镜像 / 转储（>= 阈值） --' -ForegroundColor Green
if ($images.Count -eq 0) {
    Write-Host '  无' -ForegroundColor DarkGray
} else {
    $images | Sort-Object Size -Descending | Select-Object -First 15 | ForEach-Object {
        Write-Host ('  {0,-11} {1,-17} 可删·若已送达  {2}' -f (Format-Size $_.Size), $_.When.ToString('yyyy-MM-dd HH:mm'), $_.Path)
    }
}

Write-Host ''
Write-Host '-- 5. 大但**不要删**（列出来是为了别删错） --' -ForegroundColor Yellow
$keep = @(
    @{ P = 'C:\pagefile.sys';                    Why = '页面文件；改大小要在系统设置里改，不能删' },
    @{ P = 'C:\hiberfil.sys';                    Why = '休眠/快速启动；powercfg /h off 才回收，属于功能取舍' },
    @{ P = 'C:\Windows\WinSxS';                  Why = '组件存储；只能 DISM /StartComponentCleanup，绝不能 Remove-Item' },
    @{ P = 'C:\Windows\Installer';              Why = '修复/卸载要用；删了以后装不回去也卸不掉' },
    @{ P = 'C:\ProgramData\Package Cache';      Why = '同上：卸载/修复的安装包缓存' }
)
foreach ($k in $keep) {
    if (-not (Test-Path -LiteralPath $k.P)) { continue }
    Write-Host ('  {0,-11} 不要删         {1}' -f (Format-Size (Get-DirSize $k.P)), $k.P)
    Write-Host ('              {0}' -f $k.Why) -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '-- 6. 非管理员量不到的位置（读出来是 0 或很小，不代表没占） --' -ForegroundColor Yellow
foreach ($p in @('C:\System Volume Information', 'C:\Windows\Temp', 'C:\Windows\Prefetch', 'C:\Windows\CSC')) {
    if (-not (Test-Path -LiteralPath $p)) { continue }
    Write-Host ('  {0,-11} 需管理员       {1}' -f (Format-Size (Get-DirSize $p)), $p)
}
Write-Host '  System Volume Information = 还原点/卷影副本，只能用管理员 `vssadmin list shadowstorage` 量' -ForegroundColor DarkGray

Write-Host ''
Write-Host '-- 7. 应用在 ProgramData 下的目录（有些是某个已删功能的残留） --' -ForegroundColor Green
$appDirs = @(Get-ChildItem 'C:\ProgramData' -Directory -Force -ErrorAction SilentlyContinue |
    ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Size = (Get-DirSize $_.FullName) } } |
    Where-Object { $_.Size -ge $minBytes } | Sort-Object Size -Descending | Select-Object -First 10)
if ($appDirs.Count -eq 0) { Write-Host '  无' -ForegroundColor DarkGray }
else {
    foreach ($a in $appDirs) {
        Write-Host ('  {0,-11} 需确认·看是谁写的  {1}' -f (Format-Size $a.Size), $a.Path)
    }
}

Write-Host ''
Write-Host ' 结论怎么用：`可删` 直接清；`需确认` 问用户（装过什么、还要不要）；' -ForegroundColor DarkGray
Write-Host ' `不要删` 一律不动；`需管理员` 要提权才能量/清。本脚本永不删除任何东西。' -ForegroundColor DarkGray
Write-Host '=================================================================' -ForegroundColor White
