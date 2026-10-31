<#
.SYNOPSIS
Holds one PowerShell Remoting session to a peer and speaks the remote skill's frame protocol.

.DESCRIPTION
The remote skill already has a wire protocol between a client and whatever executes a
script on the peer:

    request   R <id> <base64(script)>
    reply     O <id> <base64 chunk>   standard output
              E <id> <base64 chunk>   standard error
              X <id> <exit code>      the request is finished

The plain SSH transport implements it with a loop running inside the peer's login shell.
That loop spawns a child shell per request, which costs a full interpreter start-up on
every command -- measured at about 290 ms for PowerShell 7 on the peer this was written
against. This bridge implements the same protocol over PowerShell Remoting instead: one
PSRP session is opened once and stays open, and each request is evaluated inside the
peer's live runspace, so nothing is started per command.

Both transports therefore share every layer above the wire: the client, the daemon, the
output framing, the truncation rule and the timeout rule are one implementation.

A PSRP session is created by the PowerShell client, not by ssh, so this bridge needs
PowerShell 7 on the machine that runs it. The peer needs PowerShell 7 and the
"powershell" subsystem registered in its sshd configuration; `remote-deploy.ps1`
installs that and writes the marker the skill looks for.

.NOTES
Fidelity differs from the SSH transport and the difference is deliberate:

  * standard output and standard error stay separate, both as UTF-8 text;
  * the exit code is $LASTEXITCODE, so a native command's code comes back verbatim;
  * a PowerShell "exit N" ends the peer's runspace instead of returning a code, so the
    exit code is unknown and the next request rebuilds the runspace;
  * output is text, so binary belongs on the SSH transport or in a file transfer.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $HostName,

    [string] $UserName,

    [string] $KeyFilePath,

    # A port other than 22, a jump host and the host-key policy are handed to the ssh
    # client as "-o key=value" pairs, because New-PSSession's SSH parameter set has no
    # dedicated switch for any of them. They are separate parameters rather than one
    # hashtable because a hashtable cannot be bound from a command line reliably.
    [int] $SshPort = 22,

    [string] $SshJump,

    [string] $SshStrict = 'accept-new',

    # When set, the script is written to a file on the peer and that interpreter runs it.
    # A command line has its own quoting rules; a file has the script's own bytes.
    [string] $Runner,

    # "auto" lets the bridge make sure it has a hidden console of its own. "owned"
    # says the caller already gave it one, which saves the check entirely.
    [ValidateSet('auto', 'owned')]
    [string] $ConsoleMode = 'auto',

    [int] $MaxOutputChars = 4000000
)

$ErrorActionPreference = 'Stop'

function Initialize-HiddenConsole {
    <#
    .SYNOPSIS
    Makes sure this process and the children it starts cannot put a window on a desktop.

    .DESCRIPTION
    PowerShell's SSH transport starts an ssh.exe of its own. A child that inherits a
    console cannot flash a window, but a child whose parent has no console at all is
    given a brand new console -- a visible one. So when this process finds itself
    without a console, it allocates a hidden one first, and the ssh.exe that comes
    later inherits that instead of making a visible one.

    GetConsoleWindow is the question that can be asked exactly: it answers a valid
    handle when a console exists, hidden or not, and zero when none does. The .NET
    property Console.WindowWidth is not that question -- it throws for a hidden
    console too -- so it is only used as the cheap first guess that avoids the
    interop entirely in the common case.
    #>
    if ($ConsoleMode -eq 'owned') { return }

    $looksUsable = $true
    try { $null = [Console]::WindowWidth } catch { $looksUsable = $false }
    if ($looksUsable) { return }

    try {
        if (-not ('Native.ConsoleWindow' -as [type])) {
            Add-Type -Namespace Native -Name ConsoleWindow -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[DllImport("kernel32.dll")] public static extern bool AllocConsole();
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
'@
        }
        $window = [Native.ConsoleWindow]::GetConsoleWindow()
        if ($window -ne [IntPtr]::Zero) { return }
        [void][Native.ConsoleWindow]::AllocConsole()
        $window = [Native.ConsoleWindow]::GetConsoleWindow()
        if ($window -ne [IntPtr]::Zero) { [void][Native.ConsoleWindow]::ShowWindow($window, 0) }
    }
    catch {
        # A compiler that is not available, or a denied call, must not take the bridge
        # down with it. A window is a nuisance; a dead session is an outage.
        [Console]::Error.WriteLine("remote: could not prepare a hidden console: " + $_.Exception.Message)
    }
}

Initialize-HiddenConsole

# The frames are ASCII, but PowerShell would otherwise emit a byte order mark and a
# UTF-16 default on the way out, which the client reads as text.
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

# Must match the client's chunk size. It is a multiple of four, so every chunk decodes
# on its own and concatenating them reproduces the original bytes.
$CHUNK = 3000

$script:session = $null
$script:reconnects = 0

function Write-Frame {
    param(
        [string] $Tag,
        [string] $Id,
        [string] $Text
    )
    if ($null -eq $Text -or $Text.Length -eq 0) { return }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
    $base64 = [Convert]::ToBase64String($bytes)
    for ($offset = 0; $offset -lt $base64.Length; $offset += $CHUNK) {
        $length = [Math]::Min($CHUNK, $base64.Length - $offset)
        [Console]::Out.WriteLine($Tag + ' ' + $Id + ' ' + $base64.Substring($offset, $length))
    }
}

function Open-RemoteSession {
    $options = @{ StrictHostKeyChecking = $SshStrict }
    if ($SshPort -gt 0 -and $SshPort -ne 22) { $options.Port = "$SshPort" }
    if ($SshJump) { $options.ProxyJump = $SshJump }

    $parameters = @{ HostName = $HostName; ErrorAction = 'Stop' }
    if ($UserName) { $parameters.UserName = $UserName }
    if ($KeyFilePath) { $parameters.KeyFilePath = $KeyFilePath }
    if ($options.Count -gt 0) { $parameters.Options = $options }
    New-PSSession @parameters
}

function Get-RemoteSession {
    if ($null -ne $script:session -and $script:session.State -eq 'Opened') {
        return $script:session
    }
    if ($null -ne $script:session) {
        Remove-PSSession -Session $script:session -ErrorAction SilentlyContinue
        $script:session = $null
        $script:reconnects++
    }
    $script:session = Open-RemoteSession
    return $script:session
}

# Runs on the peer, inside its runspace. It returns a value, so a request that produced
# no output still comes back as an object; a null result therefore means the runspace
# itself died, which is a different thing and is reported as such.
$execution = {
    param($code, $runner, $maxChars)

    $ErrorActionPreference = 'Continue'
    $ProgressPreference = 'SilentlyContinue'
    # $LASTEXITCODE is scope-local, so it is reset here and read here. Reading the
    # global copy instead would miss the code a native command just set.
    $global:LASTEXITCODE = 0
    $LASTEXITCODE = 0
    $Error.Clear()

    $mixed = $null
    if ($runner) {
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ('remote-' + [Guid]::NewGuid().ToString('N'))
        try {
            [IO.File]::WriteAllText($temporary, $code, [Text.UTF8Encoding]::new($false))
            $mixed = & $runner $temporary 2>&1
        }
        finally {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
    else {
        $mixed = Invoke-Expression $code 2>&1
    }

    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    $standardOutput = @()
    $standardError = @()
    foreach ($item in @($mixed)) {
        if ($item -is [Management.Automation.WarningRecord]) {
            $standardError += "WARNING: $($item.Message)"
        }
        elseif ($item -is [Management.Automation.ErrorRecord]) {
            $standardError += [string]$item.Exception.Message
        }
        else {
            $standardOutput += $item
        }
    }

    $stdoutText = ''
    if ($standardOutput.Count -gt 0) { $stdoutText = [string]($standardOutput | Out-String -Width 4096) }
    $stderrText = ''
    if ($standardError.Count -gt 0) { $stderrText = $standardError -join [Environment]::NewLine }

    $truncated = $false
    if ($stdoutText.Length -gt $maxChars) {
        $stdoutText = $stdoutText.Substring(0, $maxChars)
        $truncated = $true
    }
    if ($stderrText.Length -gt $maxChars) {
        $stderrText = $stderrText.Substring(0, $maxChars)
        $truncated = $true
    }

    [pscustomobject]@{
        stdout    = $stdoutText
        stderr    = $stderrText
        exitCode  = $exitCode
        truncated = $truncated
    }
}

# The exit code reported when the peer's runspace ended instead of answering. 95 is the
# client's "the script could not be decoded", so this one is deliberately different.
$RUNSPACE_GONE = 97

while ($null -ne ($line = [Console]::In.ReadLine())) {
    if ($line -eq 'Q') { break }
    if ($line -notmatch '^R (\S+) (\S+)$') { continue }
    $id = $Matches[1]
    $payload = $Matches[2]

    try {
        $code = [Text.UTF8Encoding]::new($false).GetString([Convert]::FromBase64String($payload))
    }
    catch {
        Write-Frame 'E' $id ("remote: could not decode the script: " + $_.Exception.Message + [Environment]::NewLine)
        [Console]::Out.WriteLine('X ' + $id + ' 95')
        continue
    }

    $result = $null
    $failure = ''
    try {
        $session = Get-RemoteSession
        $result = Invoke-Command -Session $session -ScriptBlock $execution -ArgumentList $code, $Runner, $MaxOutputChars
    }
    catch {
        $failure = $_.Exception.Message
    }

    if ($null -eq $result) {
        # Either the script ended the runspace, or the call failed outright. The two are
        # told apart by the session's own state, and the answer is reported either way
        # because a runspace that is gone cannot report its own exit code.
        $state = 'unknown'
        if ($null -ne $script:session) {
            $state = [string]$script:session.State
            Remove-PSSession -Session $script:session -ErrorAction SilentlyContinue
            $script:session = $null
        }
        $reason = if ($failure) { $failure } else { "the script ended the peer's runspace (session state: $state)" }
        Write-Frame 'E' $id ("remote: $reason; the exit code is unknown and the runspace is rebuilt on the next request" + [Environment]::NewLine)
        [Console]::Out.WriteLine('X ' + $id + ' ' + $RUNSPACE_GONE)
        continue
    }

    Write-Frame 'O' $id $result.stdout
    Write-Frame 'E' $id $result.stderr
    [Console]::Out.WriteLine('X ' + $id + ' ' + [int]$result.exitCode)
}

if ($null -ne $script:session) {
    Remove-PSSession -Session $script:session -ErrorAction SilentlyContinue
    $script:session = $null
}
