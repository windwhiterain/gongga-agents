<#
.SYNOPSIS
Switches the Windows theme between light and dark, manually or on a daily schedule.

.DESCRIPTION
Two per-user registry values drive the Windows appearance:
  SystemUsesLightTheme - 1 = light shell (taskbar, Start), 0 = dark
  AppsUseLightTheme    - 1 = light apps, 0 = dark

Both live under HKCU, so a change always applies to the account that runs the
command. After writing them the script broadcasts WM_SETTINGCHANGE, which makes
the shell and running applications repaint immediately without restarting
Explorer or signing out.

The schedule is implemented with two daily Task Scheduler tasks, one per mode.
They run with interactive logon on purpose: the theme registry is per-user, so a
task running as SYSTEM would write to the wrong account.

.EXAMPLE
theme-cli light
theme-cli toggle
theme-cli schedule --light 08:00 --dark 20:00
theme-cli schedule --light 06:45
theme-cli schedule --remove
theme-cli status --json
theme-cli --help
theme-cli help schedule
#>

# Arguments are parsed by hand rather than through a param block. PowerShell
# rejects any argument that does not name a declared parameter, so `--help`
# would fail before the script body ran. With no param block every token
# arrives verbatim in $args, in both -File and .cmd invocation.
#
# `-?` is the one exception: PowerShell intercepts it and renders this
# comment-based help instead of passing the token to the script.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$personalizeKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
$lightTaskName = 'WindowsTheme-Light'
$darkTaskName = 'WindowsTheme-Dark'

function Get-ThemeState {
    $properties = Get-ItemProperty -Path $personalizeKey -ErrorAction Stop

    $system = if ($properties.SystemUsesLightTheme -eq 1) { 'Light' } else { 'Dark' }
    $apps = if ($properties.AppsUseLightTheme -eq 1) { 'Light' } else { 'Dark' }

    [pscustomobject]@{
        System = $system
        Apps = $apps
        Combined = if ($system -eq $apps) { $system } else { 'Mixed' }
    }
}

function Set-ThemeMode {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Light', 'Dark')]
        [string]$Mode
    )

    $value = if ($Mode -eq 'Light') { 1 } else { 0 }

    foreach ($name in 'SystemUsesLightTheme', 'AppsUseLightTheme') {
        New-ItemProperty -Path $personalizeKey -Name $name -Value $value -PropertyType DWord -Force | Out-Null
    }

    if (-not ('Native.ThemeBroadcast' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace Native
{
    public static class ThemeBroadcast
    {
        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        public static extern bool SendNotifyMessage(
            IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam);
    }
}
'@
    }

    # SendNotifyMessage posts the message to every top-level window without
    # waiting for each one to answer. SendMessageTimeout would apply its
    # timeout per window, so a few slow windows make the call take seconds.
    $broadcastSent = [Native.ThemeBroadcast]::SendNotifyMessage(
        [IntPtr]0xffff,
        0x1A,
        [UIntPtr]::Zero,
        'ImmersiveColorSet')

    [pscustomobject]@{
        Mode = $Mode
        Broadcast = $broadcastSent
    }
}

function ConvertTo-ScheduledTime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $parsed = [datetime]::MinValue
    $isValid = [datetime]::TryParseExact(
        $Value,
        @('HH:mm'),
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::None,
        [ref]$parsed)

    if (-not $isValid) {
        throw ("Invalid time '{0}'. Use HH:mm in 24-hour format, for example 08:00." -f $Value)
    }

    [datetime]::Today.AddHours($parsed.Hour).AddMinutes($parsed.Minute)
}

function Get-ScheduleState {
    $entries = @()

    foreach ($definition in @(
            @{ Name = $lightTaskName; Mode = 'Light' },
            @{ Name = $darkTaskName; Mode = 'Dark' })) {

        $task = Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            $entries += [pscustomobject]@{
                TaskName = $definition.Name
                Mode = $definition.Mode
                Installed = $false
                State = $null
                NextRun = $null
                LastResult = $null
                Time = $null
            }
            continue
        }

        $info = Get-ScheduledTaskInfo -TaskName $definition.Name -ErrorAction SilentlyContinue
        $time = $null
        $trigger = $task.Triggers | Select-Object -First 1
        if ($null -ne $trigger -and $null -ne $trigger.StartBoundary) {
            $time = ([datetime]$trigger.StartBoundary).ToString('HH:mm')
        }

        $entries += [pscustomobject]@{
            TaskName = $definition.Name
            Mode = $definition.Mode
            Installed = $true
            State = [string]$task.State
            NextRun = if ($null -ne $info) { $info.NextRunTime } else { $null }
            LastResult = if ($null -ne $info) { $info.LastTaskResult } else { $null }
            Time = $time
        }
    }

    $entries
}

function Register-ThemeSchedule {
    param(
        [Parameter(Mandatory = $true)]
        [datetime]$LightTime,

        [Parameter(Mandatory = $true)]
        [datetime]$DarkTime
    )

    $scriptPath = $PSCommandPath
    if (-not $scriptPath) {
        throw 'Cannot resolve the path of theme-cli.ps1.'
    }

    $userId = "$env:USERDOMAIN\$env:USERNAME"

    foreach ($definition in @(
            @{ Name = $lightTaskName; Mode = 'Light'; Time = $LightTime },
            @{ Name = $darkTaskName; Mode = 'Dark'; Time = $DarkTime })) {

        # The mode is passed as a positional argument. Under -File everything
        # after the script path belongs to the script, so a "-Command light"
        # switch would arrive as an unknown option instead of the mode.
        $action = New-ScheduledTaskAction `
            -Execute 'powershell.exe' `
            -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" {1}' -f $scriptPath, $definition.Mode.ToLower())

        $trigger = New-ScheduledTaskTrigger -Daily -At $definition.Time

        $principal = New-ScheduledTaskPrincipal `
            -UserId $userId `
            -LogonType Interactive `
            -RunLevel Limited

        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

        Register-ScheduledTask `
            -TaskName $definition.Name `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description ('Switch the Windows theme to {0}.' -f $definition.Mode) `
            -Force | Out-Null
    }
}

function Remove-ThemeSchedule {
    $removed = @()

    foreach ($name in $lightTaskName, $darkTaskName) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            continue
        }

        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        $removed += $name
    }

    $removed
}

function Show-Help {
    param(
        [string]$Topic = ''
    )

    switch ($Topic) {
        'schedule' {
            @"
theme-cli schedule - manage the daily theme schedule.

Usage:
  theme-cli schedule --light HH:mm --dark HH:mm   Install or update the schedule.
  theme-cli schedule --light HH:mm                Update one side, keep the other.
  theme-cli schedule --show                       Show the installed schedule.
  theme-cli schedule --remove                     Remove both scheduled tasks.

Options:
  --light HH:mm   Time of day to switch to the light theme.
  --dark HH:mm    Time of day to switch to the dark theme.
  --show          Print the installed schedule.
  --remove        Unregister the scheduled tasks. The current theme is unchanged.

Examples:
  theme-cli schedule --light 08:00 --dark 20:00
  theme-cli schedule --light 06:45
  theme-cli schedule --remove
"@
            break
        }

        'status' {
            @"
theme-cli status - show the current theme and the schedule.

Usage:
  theme-cli status            Human-readable output.
  theme-cli status --json     Machine-readable output.

Options:
  --json   Emit JSON with the system theme, the app theme and the schedule.

The system theme and the app theme are reported separately because Windows keeps
them in two independent registry values.
"@
            break
        }

        default {
            @"
theme-cli - switch the Windows theme between light and dark.

Usage:
  theme-cli <command> [options]

Commands:
  light                 Switch to the light theme.
  dark                  Switch to the dark theme.
  toggle                Flip the current theme.
  status                Show the current theme and the schedule.
  schedule              Manage the daily schedule.
  help                  Show this help.

Options:
  -h, --help, -?        Show help. Use 'theme-cli help <command>' for details.
  --light HH:mm         Time of day to switch to the light theme.
  --dark HH:mm          Time of day to switch to the dark theme.
  --show                Show the installed schedule.
  --remove              Remove the scheduled tasks.
  --json                Machine-readable output.

Examples:
  theme-cli light
  theme-cli toggle
  theme-cli schedule --light 08:00 --dark 20:00
  theme-cli schedule --remove
  theme-cli status --json
"@
        }
    }
}

function Read-Arguments {
    param(
        [string[]]$Tokens
    )

    $parsed = [pscustomobject]@{
        Command = $null
        Light = $null
        Dark = $null
        Remove = $false
        Show = $false
        Json = $false
        Help = $false
        HelpTopic = $null
    }

    $index = 0

    while ($index -lt $Tokens.Count) {
        $token = $Tokens[$index]

        switch -CaseSensitive ($token) {
            { $_ -in @('-h', '--help', '-?', '/?') } {
                $parsed.Help = $true
                if ($null -ne $parsed.Command -and $parsed.Command -ne 'help') {
                    $parsed.HelpTopic = $parsed.Command
                }
                break
            }
            '--version' {
                $parsed.Help = $true
                $parsed.HelpTopic = 'version'
                break
            }
            '--json' { $parsed.Json = $true; break }
            '--show' { $parsed.Show = $true; break }
            '--remove' { $parsed.Remove = $true; break }
            '--light' {
                $index++
                if ($index -ge $tokens.Count) {
                    throw '--light requires a time in HH:mm format.'
                }
                $parsed.Light = $tokens[$index]
                break
            }
            '--dark' {
                $index++
                if ($index -ge $tokens.Count) {
                    throw '--dark requires a time in HH:mm format.'
                }
                $parsed.Dark = $tokens[$index]
                break
            }
            default {
                if ($token.StartsWith('-')) {
                    throw "Unknown option: $token"
                }
                if ($null -eq $parsed.Command) {
                    $parsed.Command = $token.ToLowerInvariant()
                    if ($parsed.Help -and $null -eq $parsed.HelpTopic) {
                        $parsed.HelpTopic = $parsed.Command
                    }
                }
                elseif ($parsed.Command -eq 'help' -and $null -eq $parsed.HelpTopic) {
                    $parsed.HelpTopic = $token.ToLowerInvariant()
                }
                else {
                    throw "Unexpected argument: $token"
                }
            }
        }

        $index++
    }

    $parsed
}

try {
    if (-not (Test-Path $personalizeKey)) {
        throw "Registry key not found: $personalizeKey"
    }

    $options = Read-Arguments -Tokens $args

    if ($options.Help) {
        if ($options.HelpTopic -eq 'version') {
            Write-Output 'theme-cli 1.0.0'
        }
        else {
            Show-Help -Topic $options.HelpTopic
        }
        exit 0
    }

    $Command = if ($null -eq $options.Command) { 'status' } else { $options.Command }
    $Light = $options.Light
    $Dark = $options.Dark
    $Remove = $options.Remove
    $Show = $options.Show
    $Json = $options.Json

    switch ($Command) {
        'help' {
            Show-Help -Topic $options.HelpTopic
        }

        'light' {
            $result = Set-ThemeMode -Mode 'Light'
            Write-Output ('Theme set to Light (broadcast={0}).' -f $result.Broadcast)
        }

        'dark' {
            $result = Set-ThemeMode -Mode 'Dark'
            Write-Output ('Theme set to Dark (broadcast={0}).' -f $result.Broadcast)
        }

        'toggle' {
            $state = Get-ThemeState
            $target = if ($state.System -eq 'Light') { 'Dark' } else { 'Light' }
            $result = Set-ThemeMode -Mode $target
            Write-Output ('Theme toggled to {0} (broadcast={1}).' -f $target, $result.Broadcast)
        }

        'status' {
            $state = Get-ThemeState
            $schedule = @(Get-ScheduleState)

            if ($Json) {
                [pscustomobject]@{
                    system = $state.System
                    apps = $state.Apps
                    combined = $state.Combined
                    schedule = @($schedule | ForEach-Object {
                            [pscustomobject]@{
                                taskName = $_.TaskName
                                mode = $_.Mode
                                installed = $_.Installed
                                state = $_.State
                                time = $_.Time
                                nextRun = $_.NextRun
                                lastResult = $_.LastResult
                            }
                        })
                } | ConvertTo-Json -Depth 4
                break
            }

            Write-Output ('Current theme : {0} (system {1}, apps {2})' -f $state.Combined, $state.System, $state.Apps)
            Write-Output ''
            Write-Output 'Schedule:'
            foreach ($entry in $schedule) {
                if (-not $entry.Installed) {
                    Write-Output ('  {0,-6} not installed' -f $entry.Mode)
                    continue
                }

                $nextRun = if ($null -ne $entry.NextRun) { $entry.NextRun.ToString('yyyy-MM-dd HH:mm') } else { '-' }
                Write-Output ('  {0,-6} {1}  at {2}  next {3}  (last result {4})' -f `
                        $entry.Mode, $entry.State, $entry.Time, $nextRun, $entry.LastResult)
            }
        }

        'schedule' {
            if ($Remove) {
                $removed = @(Remove-ThemeSchedule)
                if ($removed.Count -eq 0) {
                    Write-Output 'No scheduled tasks found.'
                }
                else {
                    Write-Output ('Removed: {0}' -f ($removed -join ', '))
                }
                break
            }

            if ($Show) {
                $schedule = @(Get-ScheduleState)
                foreach ($entry in $schedule) {
                    if (-not $entry.Installed) {
                        Write-Output ('  {0,-6} not installed' -f $entry.Mode)
                        continue
                    }

                    $nextRun = if ($null -ne $entry.NextRun) { $entry.NextRun.ToString('yyyy-MM-dd HH:mm') } else { '-' }
                    Write-Output ('  {0,-6} at {1}  next {2}' -f $entry.Mode, $entry.Time, $nextRun)
                }
                break
            }

            if ([string]::IsNullOrWhiteSpace($Light) -and [string]::IsNullOrWhiteSpace($Dark)) {
                throw 'Specify --light HH:mm and/or --dark HH:mm, or use --show / --remove.'
            }

            $lightTime = if ([string]::IsNullOrWhiteSpace($Light)) { $null } else { ConvertTo-ScheduledTime -Value $Light }
            $darkTime = if ([string]::IsNullOrWhiteSpace($Dark)) { $null } else { ConvertTo-ScheduledTime -Value $Dark }

            $existing = @(Get-ScheduleState)
            if ($null -eq $lightTime) {
                $kept = $existing | Where-Object { $_.Mode -eq 'Light' } | Select-Object -First 1
                if ($null -eq $kept -or -not $kept.Installed -or -not $kept.Time) {
                    throw 'No existing light schedule to keep. Specify --light HH:mm too.'
                }
                $lightTime = ConvertTo-ScheduledTime -Value $kept.Time
            }

            if ($null -eq $darkTime) {
                $kept = $existing | Where-Object { $_.Mode -eq 'Dark' } | Select-Object -First 1
                if ($null -eq $kept -or -not $kept.Installed -or -not $kept.Time) {
                    throw 'No existing dark schedule to keep. Specify --dark HH:mm too.'
                }
                $darkTime = ConvertTo-ScheduledTime -Value $kept.Time
            }

            Register-ThemeSchedule -LightTime $lightTime -DarkTime $darkTime
            Write-Output ('Schedule installed: light at {0}, dark at {1}.' -f `
                    $lightTime.ToString('HH:mm'), $darkTime.ToString('HH:mm'))
        }

        default {
            throw "Unknown command: $Command. Run 'theme-cli --help' for usage."
        }
    }
}
catch {
    $message = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_.ToString()
    }
    [Console]::Error.WriteLine('theme-cli: {0}' -f $message)
    exit 1
}
