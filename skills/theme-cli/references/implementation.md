# Reference: how the theme switch actually works

## The two registry values

Windows stores the light/dark appearance as two independent per-user DWORD values:

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize
    AppsUseLightTheme     1 = light apps,    0 = dark
    SystemUsesLightTheme  1 = light shell,   0 = dark
```

`SystemUsesLightTheme` covers the taskbar, the Start menu and the system UI.
`AppsUseLightTheme` covers applications that follow the system appearance.

The two are independent, which is what makes the Windows "Custom" mode possible:
a dark taskbar with light application windows is just the two values disagreeing.

The values are per-user (`HKCU`), never machine-wide (`HKLM`). This is the single
most important fact for the scheduler: **the task must run as the signed-in user**,
or it writes to a different account's hive and has no visible effect.

## The broadcast

Writing the values does not by itself repaint anything. Windows learns about the
change through a `WM_SETTINGCHANGE` broadcast:

```
WM_SETTINGCHANGE (0x001A)
  hWnd  = HWND_BROADCAST (0xFFFF)
  wParam = 0
  lParam = "ImmersiveColorSet"
```

`ImmersiveColorSet` is what tells the shell and modern apps that the *color
theme* changed, as opposed to a generic system parameter change.

Without the broadcast the taskbar keeps its old colours until the next sign-out.
With it, the repaint happens within a frame.

### Why SendNotifyMessage and not SendMessageTimeout

`SendMessageTimeout` waits for every top-level window to acknowledge the message,
and its `uTimeout` applies **per window**. A handful of slow or unresponsive
windows each burn the full timeout, so the totals add up. Measured on this
machine:

| API | Timeout arg | Wall clock |
|---|---|---|
| `SendMessageTimeout` | 5000 ms | 36 s |
| `SendMessageTimeout` | 1000 ms | 7.5 s |
| `SendNotifyMessage` | n/a | 14 ms |

`SendNotifyMessage` posts the message to every top-level window and returns
without waiting for any of them to process it. The shell still repaints, because
the message is queued to it normally. This is the correct primitive here: we want
the message delivered, not a synchronous round-trip with every app on the desktop.

Consequence for the scheduler: with `SendMessageTimeout` the task stayed in the
`Running` state for over 30 seconds. With `SendNotifyMessage` it finishes in
about 3.7 seconds and reports `LastTaskResult = 0`.

## Verifying the change took effect

The registry value only proves the write happened. The authoritative check is the
colour the shell is *actually using right now*, readable through WinRT:

```powershell
$type = [Windows.UI.ViewManagement.UISettings, Windows.UI, ContentType = WindowsRuntime]
$settings = [Windows.UI.ViewManagement.UISettings]::new()
$color = $settings.GetColorValue([Windows.UI.ViewManagement.UIColorType]::Background)
```

`Background` returns `FFFFFFFF` in light mode and `FF000000` in dark mode. If this
value does not flip after a switch, the broadcast did not reach the shell.

## The scheduled task shape

```powershell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" {1}' -f $scriptPath, $mode)

$trigger  = New-ScheduledTaskTrigger -Daily -At $time
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
```

The mode is passed as a **positional** argument. Everything after `-File <script>` belongs
to the script, not to `powershell.exe`, so a `-Command light` switch would arrive as an
unknown option, the task would exit with code `1`, and the theme would not change. An
earlier revision of this script used `-Command` and only worked because the old `param`
block happened to declare a parameter with that name.

`LogonType Interactive` is deliberate and not a shortcut. A task that runs whether
the user is logged on or not executes as SYSTEM, so it writes to
`HKEY_USERS\S-1-5-18\...\Personalize` instead of the user's own hive. The task
reports success and nothing happens. The trade-off is that the task only fires
while the user is signed in, which is inherent to per-user theme state.

`-WindowStyle Hidden` keeps a console window from flashing at the scheduled time.

## Argument parsing

The script has **no `param` block**. PowerShell rejects any argument that does not
name a declared parameter, so `--help` could never reach the script body under a
normal parameter block. With no `param` block, every token arrives verbatim in
`$args`, identically under `-File` invocation and through the `.cmd` launcher, and
`Read-Arguments` parses them by hand.

Two consequences worth remembering:

- A function body cannot see the script-level `$args`; it has its own scope. The
  call site must pass the tokens explicitly: `Read-Arguments -Tokens $args`.
- `--light` and `--dark` each consume the **following** token. They cannot be
  `[switch]` parameters: a switch takes no value, so `08:00` would fall through to
  a positional parameter, and with only one positional slot `20:00` has nowhere to
  bind.

## The help surface

| Invocation | Result |
|---|---|
| `theme-cli --help`, `theme-cli -h`, `theme-cli /?` | General help: command table, option table, examples |
| `theme-cli help` | Same as above |
| `theme-cli help schedule` | Per-command help for `schedule` |
| `theme-cli schedule --help` | Same as above |
| `theme-cli status --help` | Per-command help for `status` |
| `theme-cli light --help` | Falls back to the general help |
| `theme-cli --version` | `theme-cli 1.0.0` |

`-?` is the one exception: PowerShell intercepts it and renders the script's
comment-based help before the body runs. The content is useful (synopsis,
description, examples) but the layout differs from the hand-written help.
Unifying them would mean dropping comment-based help entirely, which costs more
than it buys.

## Error output

Errors go to stderr as a single line, and the exit code is `1`:

```
theme-cli: Invalid time '25:99'. Use HH:mm in 24-hour format, for example 08:00.
```

`Write-Error` is deliberately avoided here: it prints the script path, the line
number and the call stack, which buries the actual message. The schedule time is
validated with `TryParseExact` so the failure carries a message written for the
user rather than a localized .NET exception string.

## Files

| Path | Role |
|---|---|
| `~/.agents/skills/theme-cli/theme-cli.ps1` | The implementation |
| `~/.agents/skills/theme-cli/theme-cli` | bash entry point |
| `~/.agents/skills/theme-cli/theme-cli.cmd` | cmd entry point |
| `~/.local/bin/theme-cli` | global bash entry point |
| `~/.local/bin/theme-cli.cmd` | global cmd entry point |

`~/.local/bin` is already on the user `PATH`, so `theme-cli` resolves from any
shell. The scheduler points at `theme-cli.ps1` directly rather than through the
wrappers, so a scheduled run does not depend on `PATH` resolution.

The global `.cmd` launcher invokes the skill launcher **without** `call`. cmd.exe's
`CALL` builtin consumes `/?` as its own switch, prints the help for `CALL` and
exits with `1` instead of forwarding the argument.
