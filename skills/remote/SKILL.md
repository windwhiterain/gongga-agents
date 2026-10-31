---
name: remote
description: Use when a task must run on another machine over SSH — run commands, move files, or check that a link works. Covers the plain SSH transport (byte-exact output, exact exit codes, any POSIX or Windows shell), the faster PowerShell Remoting transport and how to install it on a peer, the traps that silently corrupt output or lose exit codes, and how to prove a remote action really happened. Trigger phrases: 远端 / 另一台机器 / 远程主机 / the other PC / remote host / run it on the box.
---

# Driving another machine, and proving it worked

Running a command on another machine over SSH is easy to do badly, and the failures are
quiet. Output arrives in an encoding nobody declared, an exit code becomes 1, a quote
gets eaten by one of three shells, or the call hangs forever because ssh inherited a
stdin that never closes. Each of those costs a debugging session, and none of them is
about the remote machine at all.

So the shape here is deliberate:

  * A script is sent as base64 and written to a file on the remote side before it runs.
    Nothing about it passes through a shell's parser on the way, so there is no quoting
    layer to get wrong.
  * Output comes back base64-framed, so it is bytes rather than "text in whatever code
    page the remote console had". A GBK Windows console no longer produces mojibake.
  * The exit code is carried explicitly in the reply, because a channel that only
    reports "ssh failed" loses the difference between 1 and 7.
  * stdin is never inherited: either the transport owns it (a session) or it is
    /dev/null (a one-shot). The caller's stdin is a pipe that may never close.

Nothing here reads `~/.ssh/config`, a roster, or any other local convention unless you
ask for it. A target is whatever you tell it: host, port, user, identity. Store it once
and it is just a name.

## Two transports, one wire protocol

Both transports speak the same frames, so the client, the daemon, the output framing,
the truncation rule and the timeout rule are one implementation. Only the way the peer
is reached differs.

| | `ssh` | `psrp` |
|---|---|---|
| reaches the peer by | a login shell that runs a loop | PowerShell Remoting over SSH |
| what a request starts | a child shell **on the peer** | nothing — the peer's runspace is already open |
| peer-side cost | ~290 ms (an interpreter start-up) | ~40–110 ms |
| standard output / error | separate, byte-exact | separate, UTF-8 text |
| exit code | exact, including the script's own `exit N` | `$LASTEXITCODE`; a PowerShell `exit N` ends the runspace instead |
| binary output | yes | no — use `put`/`get`, which are transport-independent |
| POSIX scripts | yes | only with a named interpreter (`--runner`) |
| peer setup | none | PowerShell 7 and one sshd subsystem line |
| measured session round trip | 442 ms | **51 ms** |

The numbers are from one peer: a Windows host whose login shell was PowerShell 7.6.9.
The ssh transport's cost is that interpreter's start-up, so a peer with a cheaper shell
is cheaper too.

The fast path is worth about **3× end to end** for a command that does nothing
(`remote run` measured 165 ms over psrp against 490 ms over ssh, and 261 ms against
845 ms for a POSIX script that needs a new connection every time).

## Commands

| command | what it does |
|---|---|
| `remote add NAME --host H --user U` | remember a target (`--port --identity --jump --shell --login --runner --transport --no-config`) |
| `remote deploy NAME` | install the fast path on the peer and write its marker |
| `remote ls` / `rm NAME` | list / forget targets |
| `remote doctor NAME` | prove the link works, and how fast |
| `remote run NAME 'script'` | run a script (`--file`, `-` or stdin, `--cwd`, `--env`, `--timeout`, `--max-output`, `--direct`, `--detach`) |
| `remote put` / `get` | copy a file, verify the size on read-back |
| `remote session start\|stop\|status` | manage the persistent session |

`--transport auto` (the default) follows the peer: a deployed peer gets the fast path,
everything else gets ssh. `--transport ssh` forces the byte-exact transport even on a
deployed peer, and `--transport psrp` refuses a peer that cannot serve it rather than
answering with different fidelity than you asked for.

## The fast path, and the convention behind it

The fast path is PowerShell Remoting over SSH. It needs two things on the peer:

  * **PowerShell 7**, because the client and the `powershell` subsystem both need it;
  * an **sshd `powershell` subsystem** pointing at that executable, because PSRP over
    SSH is carried by a subsystem request and not by a login shell.

`remote deploy NAME` installs both. It pushes the peer-side script over sftp, runs it
there (it needs an elevated session), registers the subsystem line idempotently,
restarts sshd if the line changed, and writes a marker.

**The marker is the convention.** A peer advertises the fast path if and only if the
marker exists, and the marker names the executable that was installed so the other side
never has to guess where PowerShell lives:

```
%ProgramData%\remote\fast-path.json
~\.remote\fast-path.json
```

That is the whole rule. One file, one place, one meaning:

  * `remote deploy NAME` installs it and writes the marker;
  * `remote doctor NAME` reports the marker and the transport in use;
  * `remote run` reads it once, caches the answer in the target file, and picks the
    transport from then on;
  * the script and CLI for any deployed peer are always the ones shipped **in this
    skill** — never a per-machine copy — so every link looks the same.

The marker is written *before* sshd is restarted, because the restart may close the
connection that asked for it. A peer with a marker but a stale sshd is a state you can
retry; a peer with no marker at all is a state you have to repair by hand.

Re-running the deployment on an already-deployed peer is cheap and safe: the subsystem
line is already correct, so nothing is restarted and the connection does not drop.

## The wire protocol

A request is one line: `R <id> <base64(script)>`. The reply is frames, one per line:

```
O <id> <base64 chunk>    standard output
E <id> <base64 chunk>    standard error
X <id> <exit code>       the request is finished
```

The per-request id is the point. Without it, a request that times out leaves unread
frames in the pipe and every later reply is off by one — which presents as "the remote
returned another command's output", a bug that is nearly impossible to see from the
outside.

Chunks are 3000 base64 characters, a multiple of four, so every chunk decodes on its
own and concatenating them reproduces the original bytes.

## Two axes that are easy to confuse

| axis | question | values | decided by |
|---|---|---|---|
| **shell** | which language the script is written in | `posix` / `powershell` | you, or a probe |
| **login** | which shell sshd runs your command in | `posix` / `powershell` / `cmd` | a probe |

**login decides exit codes and quoting; shell decides script syntax.** On a Windows
machine sshd may run PowerShell while you want to run Git Bash scripts — that is
`shell=posix --runner 'C:\software\Git\Git\bin\bash.exe'`.

`login` is probed, because it cannot be asked: make the remote exit 7 and see which
combination reports 7. The answer is written to the target file, so a later failure is
*the remote changed*, not another guess.

## Three places that silently lie

### 1. A child process under ssh believes stdin has ended

A PowerShell started as a child sees an empty stdin, decides EOF, and exits before the
loop can read anything. A request written two seconds later then reaches nothing, and
the channel looks open while answering nothing.

**This is PowerShell-specific. Do not generalise it to `sh`** — a measured `sh script`
reads a 2 s-delayed line normally. The same reasoning wrongly applied to the POSIX
branch was harmless, but it taught the wrong lesson.

Consequence: for a PowerShell login, the session's loop must run in the login shell
itself. A target with a runner has no session for the same reason, and uses one-shot
calls instead.

### 2. Exit codes are not free

The remote's own failures collapse to 1 unless the command line says otherwise:

```
powershell -EncodedCommand 'exit 7'                    -> 1
powershell -EncodedCommand 'exit 7'; exit $LASTEXITCODE -> 7
bash.exe -c "exit 3"                                   -> 1
bash.exe -c "exit 3"; exit $LASTEXITCODE               -> 3
```

So "non-zero means failure" is trustworthy; "which non-zero" is not. The fast path has
its own version of this: there, `exit` ends the peer's runspace rather than returning a
code, so the wrapper carries no exit trailer and the bridge reads `$LASTEXITCODE`
itself. A script that needs to report a specific code should use a native command, or
the ssh transport.

### 3. sftp reports success and the file is not there

`sftp` prints `put ...`, exits 0, and the target file does not exist. So transfers are
**read back and the size compared**. `--no-verify` turns that off, and then you have
nothing but sftp's word.

sftp also has its own parser, not a shell's: double quotes group, and a backslash
escapes inside them. Windows paths do not behave that way, so a file lands somewhere
else **without an error**.

## Writing a script for the remote

Scripts run on the **remote**, in the remote's shell:

```bash
# POSIX
remote run box 'df -h / | tail -1'
# PowerShell
remote run box 'Get-ChildItem C:\ | Measure-Object | Select-Object -Expand Count'
# Multi-line: use --file or stdin, never a quoted newline
remote put box ./probe.sh /tmp/probe.sh && remote run box 'sh /tmp/probe.sh'
```

`--file` and stdin are the recommended shape: the script arrives byte for byte and
passes through no quoting layer at all.

Two things to know about the PowerShell payload:

  * the console encoding is set inside a `try`, because a runspace with no console
    attached throws on the assignment and a throw under `Stop` would abort the script
    before it ran anything;
  * there is **no exit trailer** on the fast path, because there `exit` would end the
    runspace the request is running in.

## Transferring files

```bash
remote put box ./local.tar.gz /tmp/remote.tar.gz     # verifies the size
remote get box /var/log/app.log ./app.log
```

Do **not** use `cat file | ssh host 'cat > file'`: that path truncates near 108 KB and
the exit code is still 0.

**`/tmp` is the shell's, not sftp's.** Paths are parsed by whoever receives them, and
ssh's shell and the sftp server are two different receivers: a Windows target's Git Bash
has a `/tmp` that the sftp server has never heard of, so `remote put box ./x /tmp/x`
fails with `dest open "/tmp/x": No such file or directory`. Write a name with no
directory — it lands in the remote home, which both sides agree on — or an absolute
`C:/Users/...`. The path is not silently rewritten: guessing paths is a different kind
of lie, and the failure tells you the two views are separate.

Large transfers should run as a background job: `scp` and `sftp` have no progress
output to keep a foreground call alive.

## Background tasks

```bash
remote run box 'make -j8' --detach
# started           pid 4821
# log               /tmp/remote-job-20261024-161500-a1b2.log
remote run box 'tail -n 20 /tmp/remote-job-….log'
```

`--detach` starts the process and returns immediately. It does not wait for it to
finish — that is what "background" means.

A detached job outlives the call that started it, so a later cleanup of the same
directory can fail because a handle is still open. Check for the process before
removing anything it is using.

## Targets and independence

```bash
remote add box --host 10.0.0.5 --user ops --identity ~/.ssh/id_ed25519 --port 2222
remote add box --host 10.0.0.5 --user ops --jump bastion   # ProxyJump
remote add box --host 10.0.0.5 --user ops --no-config       # -F /dev/null, hermetic
remote ls
```

Stored in `~/.remote/targets.json`; `remote rm box` deletes one.

**Deliberately not read:** `~/.ssh/config` (an alias is a local convention that exists
nowhere else), any roster, any third-party CLI. `--no-config` is the sharpest way to
answer "is my local ssh config the culprit", because it closes the connection off from
every local convention at once.

The other way round: by default the local config **is** still read, so existing aliases
keep working. That is a deliberate trade-off, and `--no-config` is the exception.

## Facts worth not re-deriving

  * **Probe TCP, not ICMP.** A peer can refuse ping and answer SSH perfectly. Test the
    port you are about to use.
  * **A scheduled task with a logon trigger never returns after an unattended
    restart.** Anything that must survive a reboot wants a boot trigger instead, and
    `LogonType=Interactive` additionally requires a real interactive session — in
    SessionId 0 it does not start at all.
  * **Event IDs tell you how a host died**: `1074` a planned restart, `6005`/`6006` the
    event log service starting and stopping (a clean shutdown), `6008` and `41` an
    unexpected shutdown or power loss, `1000`/`1001`/`1026` an application crash.
  * **`Register-ScheduledTask` has no `-AllowStartOnDemand` parameter.** Demand start
    is the default; the only switch is `-DisallowDemandStart`, and the positive
    spelling fails the whole call with `NamedParameterNotFound`. A parameter spelled
    from memory aborts the call and leaves the paragraph that would have worked
    useless.
  * **`-ExecutionTimeLimit ([TimeSpan]::Zero)` is not optional** for a task that should
    keep running. The default is `PT72H`, and Task Scheduler kills the process tree
    when the limit expires — the job then disappears three days after every start,
    with nothing on record but the service going away.
  * **Never gate on a cmdlet's return value.** `Start-ScheduledTask` and
    `Register-ScheduledTask` emit nothing to the pipeline, so `$r = Start-ScheduledTask
    …; if ($r) { 'ok' } else { 'failed' }` reports failure for a task that started
    correctly. Decide from the exit code and from observable state — the process, the
    listening port.
  * **Fail loudly inside the script** with `$ErrorActionPreference = 'Stop'`.
    `Continue` is how a failed copy becomes a "successful" one: the script keeps
    going, a later gate passes on the *previous* state, and it reports success over a
    half-written target.
  * **A GUI-subsystem binary never records `$LASTEXITCODE`.** `& app.exe` returns
    immediately with `$LASTEXITCODE` still `$null`, and since `$null -ne 0`, the
    idiomatic `if ($LASTEXITCODE -ne 0) { fail }` reports a successful install as a
    failure. Only `Start-Process -PassThru -Wait` waits and reports a real code.
  * **A non-interactive SSH session has a different PATH** from an interactive one.
    `Get-Command node` can fail while the runtime exists. Address tools by full path.
  * **Windows OpenSSH needs a space-free subsystem path.** A subsystem command line
    containing a space is mis-parsed; use the 8.3 short name.
  * **PowerShell 5.1 reads a script file as the console's code page** unless it has a
    UTF-8 BOM. A comment with non-ASCII characters is mangled without one.
  * **A timeout must never look like empty output.** A call that gives up quietly is
    indistinguishable from a script that printed nothing, and that is how a long script
    goes unnoticed for hours.

## Limits

  * **SSH only.** No WinRM, no cloud APIs, no serial console.
  * **The fast path is Windows and PowerShell only.** A POSIX peer uses the ssh
    transport, and a POSIX target without a named interpreter cannot use the fast path
    at all.
  * **The fast path's output is text.** Exact bytes, and binary, belong to the ssh
    transport or to a file transfer.
  * **A PowerShell `exit N` ends the peer's runspace** over the fast path. The bridge
    rebuilds it on the next request, and the exit code is reported as unknown.
  * **The fast path's session is not durable.** Idle exit, a crash or a restart
    discards the runspace's cwd, variables and loaded modules. A changed session id in
    the result is the signal that it happened, so never build a long procedure on state
    a restart could erase.
  * **One runspace serialises commands inside one session.** State continuity *is* the
    feature, so a long command blocks later callers of that session.
  * **Sessions are serial.** One session, one request. Use `--direct` or several
    sessions for concurrency.
  * **A one-shot is limited by the command line length** (32767 characters on Windows,
    about 4/3 of that once base64-encoded). Copy the script with `put` and run it as a
    file instead.
  * **`--timeout` may leave the remote still running.** The client says plainly that
    the outcome is unknown and **never replays** it, because a replay could run the
    command twice.
  * **File transfers need sftp on PATH.** The doctor checks it.
  * **The fast path needs `pwsh` on both sides** — the peer runs the subsystem, and
    the caller runs the client that carries the session.

## The scripts in this skill

| file | runs | role |
|---|---|---|
| `remote.py` | here | the client, the daemon and the transports |
| `remote-bridge.ps1` | here | holds one PSRP session to the peer and speaks the frame protocol |
| `remote-deploy.ps1` | **on the peer** | installs the fast path and writes the marker |
| `remote-call.ps1` | here | the PowerShell-native entry point for the fast path |

`remote-call.ps1` is for a PowerShell session — including one running inside an agent
— that wants the fast path without a Python toolchain:

```powershell
.\remote-call.ps1 -Script 'Get-Date' -HostName 192.0.2.10 -UserName ops `
    -KeyFilePath $env:USERPROFILE\.ssh\id_ed25519
```

It starts the bridge for the duration of the call, so it pays a session open every
time. `remote run` keeps the session warm between calls and is the one to use for
repeated work.

## How to verify it

```bash
python3 test_remote.py      # the skill's own suite
```

Two layers: pure functions (defaults, ssh arguments, quoting, wrapping), and a real
session against a fake login shell. The fake shell covers what a real machine cannot
reach on demand — the POSIX login path, and a request that arrives late.

Tests without teeth are worse than no tests, so the mutations that must turn the suite
red are checked too: dropping the per-request id, not escaping backslashes for sftp,
the one-shot path inheriting stdin, dropping the login shell's exit-code trailer,
truncating output past the cap, `execute` ignoring the session gate, and handing the
PowerShell loop back to a child process.

One mutation can never turn the suite red, because it is equivalent: changing
`. $D` back to `sh $D`. A test that stays green is not always a test that cannot fail
— it may be a change that had no effect. The reason is in "A child process under ssh
believes stdin has ended".

And `remote doctor NAME` is the other half: it measures the real link, one ring at a
time, so a claim about a remote machine rests on that machine's own answers.
