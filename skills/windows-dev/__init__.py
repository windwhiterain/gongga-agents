"""Windows agentic development's four traps, as functions.

    doctor(paths=None, limit=20)                    # all four, read-only
    eol(paths=None, action="scan", limit=20)        # line endings
    encoding(paths=None, action="scan", limit=20)   # GBK / UTF-16 / BOM
    path_scan(paths=None, limit=20)                 # 260 chars, reserved names
    env()                                          # execution policy, long paths, git

Each function runs `windows-dev.ps1` beside it and answers with that script's
exit code; its stdout and stderr are this call's. `action` is `scan` (read-only),
`fix` (rewrite in place, no backup) or `guard` (`eol` only: write .gitattributes
and .editorconfig).
"""

import os
import shutil
import subprocess

#: Where the script sits: this package's own directory.
HERE = os.path.dirname(os.path.abspath(__file__))


def _powershell():
    """The host the script is written for: Windows PowerShell 5.1, then pwsh."""
    for name in ("powershell", "pwsh"):
        if shutil.which(name):
            return name
    raise RuntimeError("no powershell or pwsh on PATH")


def _targets(paths):
    """The roots to scan, spelled one per word; none means the script's own `.`."""
    if not paths:
        return []
    if isinstance(paths, str):
        return [paths]
    return [str(path) for path in paths]


def _run(command, paths=None, action=None, limit=None):
    """Run the script's `command` over `paths` and answer its exit code.

    The words are a command line because that is the only door a PowerShell
    script has; the list is built here, and a caller spells none of it.
    """
    argv = [
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        os.path.join(HERE, "windows-dev.ps1"), command,
    ] + _targets(paths)
    if action:
        if action not in ("scan", "fix", "guard"):
            raise ValueError(f"action is scan, fix or guard; not {action!r}")
        argv.extend(["-Action", action])
    if limit:
        argv.extend(["-Limit", str(int(limit))])
    return subprocess.run(argv).returncode


def doctor(paths=None, limit=20):
    """All four checks in one read-only report. Changes nothing."""
    return _run("doctor", paths, limit=limit)


def eol(paths=None, action="scan", limit=20):
    """Line endings: `scan` reports, `fix` rewrites in place, `guard` writes the
    two config files that make git and the editors agree."""
    return _run("eol", paths, action=action, limit=limit)


def encoding(paths=None, action="scan", limit=20):
    """Encoding: `scan` reports, `fix` drops a BOM and converts GBK/UTF-16 to UTF-8."""
    return _run("encoding", paths, action=action, limit=limit)


def path_scan(paths=None, limit=20):
    """Paths: too long, reserved device names, case collisions, trailing space or dot.

    Read-only by design: there is no safe automatic repair for a path.
    """
    return _run("path", paths, limit=limit)


def env():
    """This shell's environment: PowerShell version, execution policy, long-path
    switch, git autocrlf, console encoding -- with the command that would fix each."""
    return _run("env")
