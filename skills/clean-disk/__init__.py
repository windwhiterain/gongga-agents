"""Windows disk cleanup: the two scripts this skill ships, as functions.

    clean(dry_run=False, no_elevate=False, project_root=None, remove_upgrade_leftovers=False)
    find_forgotten(min_mb=300, top=25, stale_days=120)

Each function runs the script beside it and answers with the script's own exit
code: `0` every item was cleaned or was never there, `1` something failed, was
left behind or was in use. Both scripts also print a `SUMMARY_JSON` line, which
is the authoritative account of what was freed -- the prose above it is not.
"""

import os
import shutil
import subprocess

#: Where the scripts sit: this package's own directory.
HERE = os.path.dirname(os.path.abspath(__file__))


def _powershell():
    """The host the scripts are written for: Windows PowerShell 5.1, then pwsh."""
    for name in ("powershell", "pwsh"):
        if shutil.which(name):
            return name
    raise RuntimeError("no powershell or pwsh on PATH")


def _script(name, arguments):
    """Run one shipped script with `arguments` and answer its exit code.

    The script's own stdout and stderr are this call's: a caller reads the same
    summary a human would. Arguments reach the script as a command line because
    that is the only door a PowerShell script has; the list is built here, and
    nothing in it is spelled by a caller.
    """
    argv = [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, name)]
    return subprocess.run(argv + list(arguments)).returncode


def _roots(project_root):
    """The code roots to sweep inside, normalised: the script takes them comma separated."""
    if not project_root:
        return []
    if isinstance(project_root, str):
        return [part.strip() for part in project_root.split(",") if part.strip()]
    return list(project_root)


def clean(dry_run=False, no_elevate=False, project_root=None, remove_upgrade_leftovers=False):
    """Clean everything on this machine that is rebuilt by itself.

    `dry_run` measures and plans and deletes nothing, and asks for no elevation.
    `no_elevate` runs the user-level half only, for a session nobody will answer
    a UAC prompt in; the system-level items are then reported as skipped.
    `project_root` is the only scope argument: a path or a list of paths to sweep
    for build output (Rust target, node_modules, ...), off unless named.
    `remove_upgrade_leftovers` also deletes the previous Windows install, which is
    the only way back to it, so it is off unless named.
    """
    arguments = []
    if dry_run:
        arguments.append("-DryRun")
    if no_elevate:
        arguments.append("-NoElevate")
    if remove_upgrade_leftovers:
        arguments.append("-RemoveUpgradeLeftovers")
    roots = _roots(project_root)
    if roots:
        arguments.extend(["-ProjectRoot", ",".join(roots)])
    return _script("clean-disk.ps1", arguments)


def find_forgotten(min_mb=300, top=25, stale_days=120):
    """Look for what holds space and is nobody's cache: read-only, deletes nothing.

    Every line ends with a verdict -- delete / ask first / do not delete / needs
    admin -- so the caller can act without measuring again.
    """
    return _script(
        "find-forgotten.ps1",
        ["-MinMB", str(int(min_mb)), "-Top", str(int(top)), "-StaleDays", str(int(stale_days))],
    )
