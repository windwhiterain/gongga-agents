<#
.SYNOPSIS
Runs one script on a peer through the remote skill's fast path, without Python.

.DESCRIPTION
This is the PowerShell-native entry point for the fast path. It starts the bridge for
the duration of the call, sends one request, prints what came back and exits.

The bridge holds one PowerShell Remoting session to the peer. Starting it costs a session
open, so this command pays that on every call. The Python client in the same skill keeps
the session warm between calls and is the one to use for repeated work; this command
exists so a PowerShell session -- including one running inside an agent -- can reach a
peer without a Python toolchain.

The wire protocol is the skill's own, and it is documented in remote-bridge.ps1:

    request   R <id> <base64(script)>
    reply     O <id> <base64 chunk>   standard output
              E <id> <base64 chunk>   standard error
              X <id> <exit code>      the request is finished

Exit codes: 0 and the peer's own code are passed through, 124 means the call timed out
and the outcome is unknown, and 97 means the peer's runspace ended instead of answering.

.EXAMPLE
.\remote-call.ps1 -Script 'Get-Date' -HostName 192.0.2.10 -UserName ops -KeyFilePath $env:USERPROFILE\.ssh\id_ed25519

.EXAMPLE
.\remote-call.ps1 -File .\probe.ps1 -HostName 192.0.2.10 -UserName ops
#>
[CmdletBinding(DefaultParameterSetName = 'Script')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Script', Position = 0)]
    [string] $Script,

    [Parameter(Mandatory, ParameterSetName = 'File', Position = 0)]
    [string] $File,

    [Parameter(Mandatory)]
    [string] $HostName,

    [string] $UserName,

    [string] $KeyFilePath,

    # A port other than 22, a jump host and the host-key policy go to the ssh client as
    # "-o key=value" pairs. They are separate parameters rather than one hashtable
    # because a hashtable cannot be bound from a command line reliably.
    [int] $SshPort = 22,

    [string] $SshJump,

    [string] $SshStrict = 'accept-new',

    # When set, the script is written to a file on the peer and this interpreter runs it.
    [string] $Runner,

    [int] $TimeoutSeconds = 60,

    [switch] $Json
)

$ErrorActionPreference = 'Stop'

$bridgePath = Join-Path $PSScriptRoot 'remote-bridge.ps1'
if (-not (Test-Path -LiteralPath $bridgePath)) {
    throw "the bridge was not found next to this script at '$bridgePath'"
}

if ($PSCmdlet.ParameterSetName -eq 'File') {
    if (-not (Test-Path -LiteralPath $File)) { throw "script file not found: $File" }
    $code = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $File).ProviderPath, [Text.UTF8Encoding]::new($false))
}
else {
    $code = $Script
}

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) {
    throw "PowerShell 7 (pwsh) was not found. The fast path needs it: it runs the client that carries the session."
}

$arguments = @('-NoLogo', '-NoProfile', '-File', ('"' + $bridgePath + '"'), '-HostName', ('"' + $HostName + '"'))
if ($UserName) { $arguments += @('-UserName', ('"' + $UserName + '"')) }
if ($KeyFilePath) { $arguments += @('-KeyFilePath', ('"' + $KeyFilePath + '"')) }
if ($Runner) { $arguments += @('-Runner', ('"' + $Runner + '"')) }
if ($SshPort -gt 0 -and $SshPort -ne 22) { $arguments += @('-SshPort', "$SshPort") }
if ($SshJump) { $arguments += @('-SshJump', ('"' + $SshJump + '"')) }
if ($SshStrict) { $arguments += @('-SshStrict', ('"' + $SshStrict + '"')) }

$process = [Diagnostics.Process]::new()
$process.StartInfo.FileName = $pwsh
$process.StartInfo.Arguments = ($arguments -join ' ')
$process.StartInfo.UseShellExecute = $false
# The bridge is left with this session's console rather than a hidden one of its own:
# PowerShell's SSH transport starts an ssh.exe of its own, and a child that inherits
# a console cannot flash a window. Giving the bridge no console at all would make
# that ssh.exe allocate a visible one.
$process.StartInfo.CreateNoWindow = $false
$process.StartInfo.RedirectStandardInput = $true
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
$process.StartInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$process.StartInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
if (-not $process.Start()) { throw 'the bridge did not start' }

$request = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$payload = [Convert]::ToBase64String([Text.UTF8Encoding]::new($false).GetBytes($code))
$standardOutput = @()
$standardError = @()
$exitCode = $null
$timedOut = $false

try {
    $process.StandardInput.WriteLine('R ' + $request + ' ' + $payload)
    $process.StandardInput.Flush()

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($null -eq $exitCode) {
        if ((Get-Date) -gt $deadline) {
            $timedOut = $true
            break
        }
        $line = $process.StandardOutput.ReadLine()
        if ($null -eq $line) { break }
        $parts = $line.Split(' ', 3)
        if ($parts.Length -lt 2 -or $parts[1] -ne $request) { continue }
        if ($parts[0] -eq 'X') {
            $exitCode = 0
            if ($parts.Length -gt 2) { [void][int]::TryParse($parts[2], [ref]$exitCode) }
            break
        }
        if ($parts[0] -ne 'O' -and $parts[0] -ne 'E') { continue }
        $body = if ($parts.Length -gt 2) { $parts[2] } else { '' }
        try { $chunk = [Convert]::FromBase64String($body) } catch { continue }
        if ($parts[0] -eq 'O') { $standardOutput += $chunk } else { $standardError += $chunk }
    }
}
finally {
    try { $process.StandardInput.WriteLine('Q'); $process.StandardInput.Flush() } catch { }
    if (-not $process.WaitForExit(5000)) { try { $process.Kill() } catch { } }
    $process.Dispose()
}

if ($timedOut) {
    $message = "the call timed out after ${TimeoutSeconds}s; the peer may still be running the command and its outcome is unknown"
    if ($Json) {
        [ordered]@{ ok = $false; timedOut = $true; output = ''; errorText = $message; exitCode = $null } | ConvertTo-Json -Compress
    }
    else {
        [Console]::Error.WriteLine($message)
    }
    exit 124
}

if ($null -ne $standardOutput.Count) {
    [Console]::Out.Write([Text.Encoding]::UTF8.GetString($standardOutput))
}
if ($null -ne $standardError.Count) {
    [Console]::Error.Write([Text.Encoding]::UTF8.GetString($standardError))
}

if ($Json) {
    [ordered]@{
        ok       = $true
        output   = [Text.Encoding]::UTF8.GetString($standardOutput)
        errorText = [Text.Encoding]::UTF8.GetString($standardError)
        exitCode = $exitCode
    } | ConvertTo-Json -Compress
}

if ($null -eq $exitCode) { exit 1 }
exit $exitCode
