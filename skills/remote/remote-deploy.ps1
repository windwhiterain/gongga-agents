<#
.SYNOPSIS
Installs the remote skill's fast path on this machine and writes the marker that advertises it.

.DESCRIPTION
The fast path is PowerShell Remoting over SSH. It needs two things on the peer:

  * PowerShell 7, because the PSRP client and the "powershell" subsystem both need it;
  * an sshd "powershell" subsystem pointing at that executable, because PowerShell
    Remoting over SSH is carried by a subsystem request and not by a login shell.

This script installs both and then writes the marker file the skill looks for. Running it
again is safe: an already-correct subsystem line is left alone and no service restart is
issued, so the common case -- a peer that was deployed once -- costs one round trip and
drops nothing.

The marker is the convention. A peer advertises the fast path if and only if the marker
exists, and the marker names the executable that was installed, so the other side never
has to guess where PowerShell lives. Two locations are used, machine-wide first:

    %ProgramData%\remote\fast-path.json
    ~\.remote\fast-path.json

.NOTES
Run it elevated. It writes to the sshd configuration and restarts the sshd service, and
only an administrator can do either.

Restarting sshd closes the connection that asked for it, so the restart is issued last
and the script prints what it did before the session goes away. Whether the session
survives is not something this script can promise; the caller re-checks the marker.
#>
[CmdletBinding()]
param(
    # Where the marker is written. The default is the machine-wide location, which is
    # what a deployment as administrator should use.
    [string] $MarkerPath = (Join-Path $env:ProgramData 'remote\fast-path.json'),

    # Skip the service restart even when the subsystem line changed. Useful when the
    # caller intends to restart sshd itself.
    [switch] $NoRestart
)

$ErrorActionPreference = 'Stop'

$sshdConfig = Join-Path $env:ProgramData 'ssh\sshd_config'
$subsystemName = 'powershell'

function Write-Report {
    param([hashtable] $Fields)
    ($Fields | ConvertTo-Json -Compress -Depth 4)
}

function Resolve-PwshPath {
    <#
    .SYNOPSIS
    Finds PowerShell 7 and returns a path the sshd subsystem line can use.

    .DESCRIPTION
    Windows OpenSSH mishandles a subsystem command line that contains spaces, so a path
    under "Program Files" has to be shortened to its 8.3 name. A path that is already
    space-free is preferred and used as it is.
    #>
    $candidates = @()
    $command = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $root) { continue }
        $candidate = Join-Path $root 'PowerShell\7\pwsh.exe'
        if (Test-Path -LiteralPath $candidate) { $candidates += $candidate }
    }
    $candidates = @($candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)
    if ($candidates.Count -eq 0) {
        throw "PowerShell 7 (pwsh) was not found. The fast path needs it on both sides: the peer runs the subsystem and the caller runs the client."
    }

    foreach ($candidate in $candidates) {
        if ($candidate -notmatch '\s') { return $candidate }
    }

    # Every path has a space, so shorten one. The 8.3 name is what sshd can parse.
    foreach ($candidate in $candidates) {
        try {
            $short = (New-Object -ComObject Scripting.FileSystemObject).GetFile($candidate).ShortPath
            if ($short -and $short -notmatch '\s') { return $short }
        }
        catch {
            continue
        }
    }
    throw "pwsh was found at '$($candidates[0])', but that path contains a space and no 8.3 name was available. Windows OpenSSH needs a space-free subsystem path."
}

function Get-ConfiguredSubsystem {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^(?i)subsystem\s+' + [regex]::Escape($subsystemName) + '\s+(.+)$') {
            return $Matches[1].Trim()
        }
    }
    return $null
}

$report = @{
    ok          = $false
    changed     = $false
    restarted   = $false
    marker      = $MarkerPath
    pwsh        = $null
    subsystem   = $null
    previous    = $null
    detail      = ''
}

try {
    $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $elevated) {
        throw "this script needs an elevated session: it writes the sshd configuration and restarts the sshd service, and only an administrator can do either. The account '$([Environment]::UserName)' is not elevated here."
    }

    if (-not (Test-Path -LiteralPath $sshdConfig)) {
        throw "the sshd configuration was not found at '$sshdConfig'"
    }

    $pwsh = Resolve-PwshPath
    $report.pwsh = $pwsh

    # The comparison is between the command alone and the command alone. Including the
    # subsystem name on one side and not the other would make every run look like a
    # change, and a peer would restart its sshd on every deployment.
    $desired = "`"$pwsh`" -sshs -NoLogo"
    $current = Get-ConfiguredSubsystem -Path $sshdConfig
    $report.previous = $current

    if ($current -ne $desired) {
        $report.changed = $true
        $content = Get-Content -LiteralPath $sshdConfig
        $updated = @()
        foreach ($line in $content) {
            if ($line.Trim() -match '^(?i)subsystem\s+' + [regex]::Escape($subsystemName) + '\s+') {
                $updated += "Subsystem $subsystemName $desired"
            }
            else {
                $updated += $line
            }
        }
        if (-not ($content | Where-Object { $_.Trim() -match '^(?i)subsystem\s+' + [regex]::Escape($subsystemName) + '\s+' })) {
            $updated += ""
            $updated += "# Added by the remote skill so PowerShell Remoting over SSH is available."
            $updated += "Subsystem $subsystemName $desired"
        }
        # The file must keep its original encoding, so it is read and written as bytes.
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($sshdConfig, (($updated -join "`r`n") + "`r`n"), $encoding)
        $report.subsystem = $desired
    }
    else {
        $report.subsystem = $current
    }

    $markerDirectory = Split-Path -Parent $MarkerPath
    if (-not (Test-Path -LiteralPath $markerDirectory)) {
        New-Item -ItemType Directory -Force -Path $markerDirectory | Out-Null
    }

    # The marker is written before the restart, because the restart may close the
    # connection this script is running on. A peer that advertises the fast path but
    # whose sshd has not reloaded the subsystem yet is a state the caller can retry
    # from; a peer with no marker at all is a state it has to repair by hand.
    $marker = [ordered]@{
        schema      = 1
        transport   = 'psrp'
        pwsh        = $pwsh
        subsystem   = $subsystemName
        installedAt = (Get-Date).ToString('o')
        host        = [Environment]::MachineName
        user        = [Environment]::UserName
        needsRestart = $report.changed
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($MarkerPath, ($marker | ConvertTo-Json -Depth 4), $encoding)

    # The restart comes last: it may close the connection this script is running on.
    if ($report.changed -and -not $NoRestart) {
        try {
            Restart-Service -Name sshd -Force -ErrorAction Stop
            $report.restarted = $true
        }
        catch {
            $report.detail = "the subsystem line and the marker were written but the sshd service could not be restarted: $($_.Exception.Message)"
        }
    }

    $report.ok = $true
    if (-not $report.detail) { $report.detail = 'the fast path is advertised by the marker' }
}
catch {
    $report.detail = $_.Exception.Message
}

Write-Report $report
