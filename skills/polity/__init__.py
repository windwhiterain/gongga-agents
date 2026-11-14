"""Separate judgement from execution: justice, advisor and premier as roles.

    start(task, spec, goal, cwd=".", remote=None, home=None)
    record(kind, task, body, actor, id=None, home=None)
    dispatch(task, plan, home=None)
    status(task, home=None)

Every role is an ordinary session; what ties them together is one append-only
JSONL file per task (`<home>/polity/<task>.jsonl`). `body` is a mapping or the
JSON text of one. The implementation is `polity.py` beside this file: each
function here calls the function of that name there, with the arguments the
caller named.
"""

import json
import os
import subprocess
import sys

#: The implementation sits beside this file; the harness imports this package by
#: path, which does not put this directory on `sys.path`.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import polity  # noqa: E402  -- the implementation beside this file


def _run(call):
    """Run one verb, answering its refusal as one line and exit code 2."""
    try:
        call()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("polity: %s" % error, file=sys.stderr)
        return 2
    return 0


def start(task, spec, goal, cwd=".", remote=None, home=None):
    """Create one task, its advisor and premier sessions, and justice's watch.

    Run it from the justice session: the watch's action runs in the session that
    armed it, so no argument can arm another session's interpreter. `cwd` and
    `spec` name files on the machine the roles run on; with `remote` they are the
    peer's paths and are passed through untouched, because this machine's
    filesystem says nothing about them.
    """
    return _run(lambda: polity.start(task=task, spec=spec, goal=goal, cwd=cwd,
                                     remote=remote, home=home))


def record(kind, task, body, actor, id=None, home=None):
    """Validate one record and append it: `kind` is requirement, proposal, plan,
    report, judgement, spec_amendment or session; `actor` is the role that accepts
    responsibility, and only some roles may write some kinds.

    `id` names the record; leave it out and the next free one is used.
    """
    payload = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return _run(lambda: polity.record(kind=kind, task=task, body=payload, actor=actor,
                                      id=id, home=home))


def dispatch(task, plan, home=None):
    """Release an approved plan to premier, naming the judgement that approved it."""
    return _run(lambda: polity.dispatch(task=task, plan=plan, home=home))


def status(task, home=None):
    """Roles, record ids, the open requirements, and the latest record."""
    return _run(lambda: polity.status(task=task, home=home))
