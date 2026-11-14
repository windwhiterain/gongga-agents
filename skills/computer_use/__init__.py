"""Drive this machine's desktop: screenshots, the control tree, clicks, keys.

    doctor()                                       # can this skill work here
    windows()                                      # what is open
    shot(window=None, scale=1.0, out_path=None)     # `@image(<path>)`
    tree(window=None, depth=5, limit=150)           # what can be clicked
    click(x, y, double=False, right=False, window=None)
    type_text(text, window=None)
    key(combo, window=None)
    focus(window)

`cu.py` beside this file is the implementation for whichever platform this is:
the interface is the same everywhere, so only the backend changes (cuwin.py,
cumac.py, culinux.py).
"""

import os
import sys

#: The implementation sits beside this file. The harness imports this package by
#: path, which does not put this directory on `sys.path` -- and the `import cu`
#: below, and cu's own `import cuwin`, both need it there.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cu  # noqa: E402  -- the implementation beside this file

doctor = cu.doctor
windows = cu.windows
shot = cu.shot


def tree(window=None, depth=5, limit=150):
    """The control tree of a window: what can be clicked, and what it is called.

    This is the one call that needs `comtypes` (the UIA client is built from the
    typelib it generates). The entry this skill used to have ran under `uv run`,
    which installed it on demand; a package imported in-process cannot, so a
    missing comtypes is said in one line instead of arriving as a traceback.
    """
    try:
        return cu.tree(window, depth=depth, limit=limit)
    except ImportError as missing:
        raise RuntimeError(
            f"the control tree needs comtypes ({missing}); install it with "
            "`uv pip install comtypes`, or use shot + click instead"
        ) from None
click = cu.click
type_text = cu.type_text
key = cu.key
focus = cu.focus
