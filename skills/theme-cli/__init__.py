"""Switch the Windows theme between light and dark, by hand or on a daily schedule.

    light() / dark() / toggle()
    status(as_json=False)
    schedule(light=None, dark=None, show=False, remove=False)

Every function runs `theme-cli.ps1` beside it and answers with that script's exit
code. The change is per-user: it writes HKCU and then broadcasts
WM_SETTINGCHANGE, which makes the shell and running applications repaint without
restarting Explorer or signing out.
"""

import os
import shutil
import subprocess

#: Where the script sits: this package's own directory.
HERE = os.path.dirname(os.path.abspath(__file__))


def _powershell():
    """The host to run the script with: pwsh when it is here, else Windows PowerShell."""
    for name in ("pwsh", "powershell"):
        if shutil.which(name):
            return name
    raise RuntimeError("no pwsh or powershell on PATH")


def _run(arguments):
    """Run the script with `arguments`, whose stdout and stderr are this call's.

    The words reach the script as a command line, which is the only door a
    PowerShell script has; the list is built here and a caller spells none of it.
    """
    argv = [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "theme-cli.ps1")]
    return subprocess.run(argv + list(arguments)).returncode


def light():
    """Both the shell and applications to light."""
    return _run(["light"])


def dark():
    """Both the shell and applications to dark."""
    return _run(["dark"])


def toggle():
    """Whatever it is now, the other one."""
    return _run(["toggle"])


def status(as_json=False):
    """The current theme and the two scheduled tasks, if any are installed.

    `as_json=True` answers the same state as one JSON object, for a caller that
    reads fields instead of lines.
    """
    return _run(["status"] + (["--json"] if as_json else []))


def schedule(light=None, dark=None, show=False, remove=False):
    """Install, show or remove the two daily tasks that switch the theme.

    `light` and `dark` are `HH:mm` times of day; a side left out keeps the time
    already installed, so a schedule can be changed one half at a time. `show`
    lists what is installed and `remove` deletes both tasks.
    """
    arguments = ["schedule"]
    if remove:
        arguments.append("--remove")
    if show:
        arguments.append("--show")
    if light:
        arguments.extend(["--light", str(light)])
    if dark:
        arguments.extend(["--dark", str(dark)])
    return _run(arguments)
