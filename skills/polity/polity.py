#!/usr/bin/env python3
"""Run the polity workflow without putting decisions in the harness.

The current session is justice. Roles are ordinary Gongga sessions; records are
append-only JSON documents. Only a justice approval can release a plan, and
only an explicit user approval can amend a spec.
"""

import argparse
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROLES = {"justice": "pro", "advisor": "sota", "premier": "flash"}
RECORDS = (
    "requirement",
    "proposal",
    "plan",
    "judgement",
    "report",
    "spec_amendment",
    "session",
)
REQUIREMENT_STATES = ("met", "unmet", "not-applicable")
VERDICTS = ("approve", "revise", "reject")
VIOLATIONS = ("none", "premise", "direct")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def fail(message):
    raise ValueError(message)


def checked_name(value, label):
    if not NAME.fullmatch(value):
        fail(f"{label} must contain only letters, digits, dot, underscore, or dash")
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def home():
    value = os.environ.get("GONGGA_HOME")
    if not value:
        fail("GONGGA_HOME is not set; use a shell started by Gongga or pass --home")
    return Path(value).expanduser()


def task_file(task, root=None):
    return (Path(root).expanduser() if root else home()) / "polity" / f"{task}.jsonl"


def read_task(task, root=None):
    path = task_file(task, root)
    records = []
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line, text in enumerate(stream, 1):
                if not text.strip():
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    fail(f"{path}:{line}: invalid JSON: {error.msg}")
                if not isinstance(record, dict):
                    fail(f"{path}:{line}: each record must be a JSON object")
                records.append(record)
    if not records:
        fail(f"no polity task {task!r}; run start first")
    initial = records[0]
    if initial.get("kind") != "task" or initial.get("version") != 1:
        fail(f"{path} does not begin with a version 1 task")
    if any(record.get("task") != task for record in records):
        fail(f"{path} contains a record for another task")
    return initial, records


def find_record(records, kind, identifier):
    found = [
        record
        for record in records
        if record.get("kind") == kind and record.get("id") == identifier
    ]
    if len(found) != 1:
        fail(f"expected one {kind} {identifier!r}, found {len(found)}")
    return found[0]


def open_requirements(records):
    """Every requirement still unsolved.

    A solved record closes the one its `ref` names; without a `ref` it is a
    requirement of its own that arrives already solved, which is a way of recording
    something the user asked for after it was done.
    """
    closed = {
        record["ref"]
        for record in records
        if record.get("kind") == "requirement"
        and record.get("solved")
        and record.get("ref")
    }
    return [
        record
        for record in records
        if record.get("kind") == "requirement"
        and not record.get("solved")
        and record["id"] not in closed
    ]


def record_id(records, kind, number=None):
    prefix = {"spec_amendment": "amendment"}.get(kind, kind)
    if number is not None:
        return f"{prefix}-{number}"
    used = {record.get("id") for record in records if record.get("kind") == kind}
    number = 1
    while f"{prefix}-{number}" in used:
        number += 1
    return f"{prefix}-{number}"


@contextmanager
def task_lock(path):
    """Serialize writers on both POSIX and Windows; the lock file is task data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_UN)


def write_record(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def append_record(path, record):
    with task_lock(path):
        write_record(path, record)


def load_json(text):
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        fail(f"invalid JSON: {error.msg}")
    if not isinstance(value, dict):
        fail("a record must be a JSON object")
    return value


def required_strings(value, fields):
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            fail(f"{field} must be a non-empty string")


def string_list(value, field):
    if not isinstance(value.get(field), list) or not value[field]:
        fail(f"{field} must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value[field]):
        fail(f"{field} must contain only non-empty strings")


def normalize(kind, payload):
    if not isinstance(payload, dict):
        fail("a record must be a JSON object")
    if kind == "proposal":
        required_strings(
            payload,
            (
                "example",
                "expected",
                "actual",
                "actual_spec",
                "expected_spec",
            ),
        )
    elif kind == "plan":
        string_list(payload, "spec_refs")
        string_list(payload, "steps")
        string_list(payload, "acceptance")
    elif kind == "judgement":
        required_strings(payload, ("subject", "reason"))
        if payload.get("spec_violation") not in VIOLATIONS:
            fail("spec_violation must be none, premise, or direct")
        if payload.get("verdict") not in VERDICTS:
            fail("verdict must be approve, revise, or reject")
        if "spec_refs" in payload:
            string_list(payload, "spec_refs")
        if payload["spec_violation"] != "none" and not payload.get("spec_refs"):
            fail("a premise or direct violation needs spec_refs")
        if "verification" in payload:
            string_list(payload, "verification")
        if "requirements" in payload:
            answers = payload["requirements"]
            if not isinstance(answers, dict):
                fail("judgement.requirements must map a requirement id to its state")
            for identifier, state in answers.items():
                if state not in REQUIREMENT_STATES:
                    fail(
                        "judgement.requirements values must be met, unmet, or "
                        "not-applicable"
                    )
        # A judgement on a plan or a report is an answer to someone's work: the
        # reason says what justice thinks, `verification` says what justice ran.
        if payload["subject"].startswith(("plan:", "report:")) and not payload.get(
            "verification"
        ):
            fail(
                "a judgement on a plan or a report carries `verification`: the "
                "commands justice ran and what they printed"
            )
    elif kind == "report":
        string_list(payload, "changes")
        string_list(payload, "verification")
    elif kind == "requirement":
        # SPEC: what the user asked for, and the records that answer it.
        required_strings(payload, ("text",))
        for field in ("proposals", "plans", "reports"):
            if field in payload:
                string_list(payload, field)
        if "solved" in payload and not isinstance(payload["solved"], bool):
            fail("requirement.solved must be true or false")
        if "ref" in payload and not isinstance(payload["ref"], str):
            fail("requirement.ref must be the id of the requirement it restates")
    elif kind == "session":
        required_strings(payload, ("role", "session"))
        if payload["role"] not in ("justice", "advisor", "premier"):
            fail("session.role must be justice, advisor, or premier")
        # A field given as None says "not set" -- no remote means this machine --
        # so only a non-string, non-null value is a mistake.
        for field in ("model", "remote", "cwd", "note"):
            if payload.get(field) is not None and not isinstance(payload[field], str):
                fail(f"session.{field} must be a string when it is given")
    elif kind == "spec_amendment":
        required_strings(
            payload, ("ref_proposal", "ref_judgement", "user_decision", "change")
        )
        if not isinstance(payload.get("user_approved"), bool):
            fail("user_approved must be true or false, quoting the user's decision")
    else:
        fail(f"unknown record kind {kind!r}")
    return payload


def cli(*arguments):
    done = subprocess.run(
        ["gongga-cli", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        fail((done.stderr or done.stdout).strip() or "gongga-cli failed")
    events = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    result = next((event for event in events if event.get("t") == "cmd_result"), None)
    if result is None or result.get("ok") is False:
        fail(done.stdout.strip() or f"gongga-cli {arguments[0]} returned no result")
    return result.get("data") or {}


def pool():
    configured = os.environ.get("POLITY_MODEL_POOL")
    path = (
        Path(configured).expanduser()
        if configured
        else (Path(__file__).resolve().parent.parent / "model_pool" / "model_pool")
    )
    if not path.is_file():
        fail(f"model_pool is not available at {path}; set POLITY_MODEL_POOL")
    # Windows cannot execute a `#!/usr/bin/env bash` script, so on that platform
    # the caller is bash itself. `shutil.which` first -- the shell this already
    # runs in has one -- then the two places Git for Windows installs it.
    if os.name == "nt":
        shell = shutil.which("bash") or next(
            (
                found
                for found in (
                    r"C:\Program Files\Git\bin\bash.exe",
                    r"C:\Program Files (x86)\Git\bin\bash.exe",
                )
                if Path(found).is_file()
            ),
            None,
        )
        if shell:
            return [shell, str(path)]
    return [str(path)]


def draw_model(tier):
    done = subprocess.run(
        [*pool(), "pick", "--tier", tier],
        capture_output=True,
        text=True,
        check=False,
    )
    model = done.stdout.strip()
    if done.returncode != 0 or not model or "\n" in model:
        fail((done.stderr or done.stdout).strip() or f"could not draw a {tier} model")
    return model


def session_name(task, role):
    return f"polity-{task}-{role}"


def watch_name(task, role):
    """What one role's watch is armed as, and dropped by.

    # Invariant

    One watch per role, because the news must say which role spoke: a match runs a
    command with no event fields to interpolate, so the identity comes from which
    pattern fired.
    """
    return f"polity-{task}-{role}"


def watch_pattern(task, role):
    """The pattern over one role's turn end.

    # Invariant

    The task id is escaped before it is a pattern: it may spell a regex
    metacharacter, and a watch that matches a stranger's session is worse than no
    watch. `turn_end` fires for failures too -- its payload's `outcome` says which
    -- and the server's monitor repairs a failed role session, so one kind is the
    whole vocabulary the workflow waits on.
    """
    return f"^turn_end:{re.escape(f'polity-{task}-')}{role}$"


def watch_action(task, role):
    """What a match does: tell justice, in a message that names the role."""
    return json.dumps(
        {
            "name": "send_message",
            "args": {
                "name": os.environ.get("GONGGA_SESSION", ""),
                "message": f"polity-{task}: {role} finished its turn",
            },
        }
    )


def role_brief(task, role, spec):
    common = (
        f"You are {role} for polity task {task}. "
        f"The authoritative spec is {spec}. Read it and the polity skill. "
        f"Records live in $GONGGA_HOME/polity/{task}.jsonl. "
        "Use the polity script for records; never invent an approval. "
        "Do not edit the spec, another role's records, or implementation files. "
        "End every turn with the record id and a one-line summary: justice is "
        "subscribed to your turn end, and that answer is how the id reaches them."
    )
    duties = {
        "advisor": (
            "Investigate and write one plan for the user's goal, then stop and let "
            "the subscription's callback carry its id to justice. If the spec itself "
            "is the obstacle, write a proposal instead. Do not implement or approve "
            "your own work."
        ),
        "premier": (
            "Stand by. No plan is approved yet, so there is nothing to do this turn: "
            "answer in one line and stop. Dispatching implementation (subagents in "
            "separate worktrees), reviewing the result and writing the report all "
            "begin when justice sends an approved plan id. Never change the plan or "
            "the spec."
        ),
    }
    return common + " " + duties[role]


def role_tier(role):
    """The tier this run draws a role's model at.

    # Invariant

    `POLITY_TIER` moves every role and `POLITY_TIER_<ROLE>` moves one, so a run
    that names no variable spends exactly the table above. Justice is this
    session: its entry only fixes the tier the user may switch this session to,
    never a session `start` creates.
    """
    return (
        os.environ.get(f"POLITY_TIER_{role.upper()}")
        or os.environ.get("POLITY_TIER")
        or ROLES[role]
    )


def create_role(task, role, cwd, remote=None):
    session = session_name(task, role)
    model = draw_model(role_tier(role))
    place = ["--remote", remote] if remote else []
    cli("set_session", "--name", session, "--cwd", cwd, "--model", model, *place)
    return {"session": session, "model": model, "cwd": cwd, "remote": remote}


def cmd_start(args):
    task = checked_name(args.task, "task")
    # A spec and a cwd name files on the machine the roles run on. With
    # `--remote` they are the peer's paths, and this machine's filesystem says
    # nothing about them: resolving or testing them here would check a
    # different checkout, or none at all.
    if args.remote:
        cwd, spec = args.cwd, args.spec
        if not spec.strip():
            fail("spec must not be empty")
    else:
        cwd = str(Path(args.cwd).expanduser().resolve())
        spec = str(Path(args.spec).expanduser().resolve())
        if not Path(spec).is_file():
            fail(f"spec does not exist: {spec}")
    if not args.goal.strip():
        fail("goal must not be empty")
    path = task_file(task, args.home)
    if path.exists():
        fail(f"task already exists: {path}; use status to resume it")
    # The callback runs in the session that armed it: justice is whoever runs
    # start, and no flag can arm another session's interpreter.
    justice = os.environ.get("GONGGA_SESSION")
    if not justice:
        fail("run start from the justice session; GONGGA_SESSION is not set")
    roles = {}
    for role in ("advisor", "premier"):
        roles[role] = create_role(task, role, cwd, args.remote)
    watches = []
    try:
        for role in ("advisor", "premier"):
            cli(
                "subscribe",
                watch_pattern(task, role),
                watch_action(task, role),
                "--subscription",
                watch_name(task, role),
                "--session",
                justice,
                "--persistant",
                "true",
            )
            watches.append(watch_name(task, role))
    except Exception:
        for role in roles:
            with suppress(Exception):
                cli("del_session", "--name", roles[role]["session"])
        raise
    record = {
        "version": 1,
        "kind": "task",
        "id": task,
        "task": task,
        "created_at": now(),
        "goal": args.goal,
        "spec": spec,
        "cwd": cwd,
        "remote": args.remote,
        "roles": {"justice": {"session": justice}, **roles},
        "watch": watches,
        "state": "planning",
        "note": "state is descriptive; the records and their references are authoritative",
    }
    try:
        append_record(path, record)
        for role in roles:
            brief = role_brief(task, role, record["spec"])
            if role == "advisor":
                brief += f" The user's goal is: {args.goal}"
            cli("send_message", "--name", roles[role]["session"], "--message", brief)
            # What the role actually runs on, on the record: the task record holds
            # what `start` drew, and a later model swap otherwise leaves it lying.
            append_record(
                path,
                {
                    "version": 1,
                    "kind": "session",
                    "id": record_id(read_task(task, args.home)[1], "session"),
                    "task": task,
                    "created_at": now(),
                    "actor": "justice",
                    "role": role,
                    "session": roles[role]["session"],
                    "model": roles[role]["model"],
                    "remote": args.remote,
                    "cwd": cwd,
                    "note": "created by start",
                },
            )
    except Exception:
        # The failed start remains inspectable rather than pretending to be ready.
        if not path.exists():
            append_record(path, {**record, "state": "start_failed"})
        raise
    print(
        json.dumps(
            {
                "task": task,
                "state": "planning",
                "roles": record["roles"],
                "watch": watches,
            },
            indent=2,
        )
    )


def cmd_record(args):
    task = checked_name(args.task, "task")
    path = task_file(task, args.home)
    with task_lock(path):
        record_document(args, task, path)


def record_document(args, task, path):
    _initial, records = read_task(task, args.home)
    payload = load_json(args.json)
    reserved = {"version", "kind", "id", "task", "created_at", "actor"}
    if reserved.intersection(payload):
        fail("version, kind, id, task, created_at, and actor belong to the command")
    normalize(args.kind, payload)
    if args.kind == "plan" and args.actor != "advisor":
        fail("only advisor can write a plan")
    if args.kind == "proposal" and args.actor != "advisor":
        fail("only advisor can write a proposal")
    if args.kind == "report" and args.actor != "premier":
        fail("only premier can write a report")
    if args.kind in ("judgement", "spec_amendment") and args.actor != "justice":
        fail("only justice can record a judgement or the user's spec decision")
    if args.kind == "requirement" and args.actor != "justice":
        fail("only justice records the user's requirements")
    if args.kind == "requirement":
        # A reference to nothing is a claim nobody can check.
        for field, kind in (
            ("proposals", "proposal"),
            ("plans", "plan"),
            ("reports", "report"),
        ):
            for identifier in payload.get(field, []):
                find_record(records, kind, identifier)
        if payload.get("ref"):
            find_record(records, "requirement", payload["ref"])
    if args.kind == "session" and args.actor not in ("justice", "advisor", "premier"):
        fail("a session record names its actor: justice, advisor, or premier")
    identifier = record_id(records, args.kind, args.id)
    if args.id is not None:
        checked_name(args.id, "id")
        if any(record.get("id") == args.id for record in records):
            fail(f"record id already exists: {identifier}")
    if args.kind == "judgement":
        subject = payload["subject"]
        match = re.fullmatch(r"(proposal|plan|report):(.+)", subject)
        if not match:
            fail("judgement.subject must be proposal:<id>, plan:<id>, or report:<id>")
        find_record(records, match.group(1), match.group(2))
        if payload["verdict"] == "approve":
            if payload["spec_violation"] == "direct":
                fail("a direct spec violation cannot be approved")
            if match.group(1) == "plan" and payload["spec_violation"] != "none":
                fail("only a plan with spec_violation=none can be approved")
            if match.group(1) == "report":
                report = find_record(records, "report", match.group(2))
                plan = find_record(records, "plan", report.get("plan", ""))
                approved_plan(records, plan["id"])
                if payload["spec_violation"] == "premise":
                    fail(
                        "a premise violation must go back to advisor, not approve a report"
                    )
                string_list(payload, "spec_refs")
    elif args.kind == "report":
        required_strings(payload, ("plan",))
        plan = find_record(records, "plan", payload["plan"])
        approved_plan(records, plan["id"])
    elif args.kind == "spec_amendment":
        proposal = find_record(records, "proposal", payload["ref_proposal"])
        judgement = find_record(records, "judgement", payload["ref_judgement"])
        if judgement.get("subject") != f"proposal:{proposal['id']}":
            fail("ref_judgement must judge ref_proposal")
        latest = latest_judgement(records, f"proposal:{proposal['id']}")
        if (
            latest is None
            or latest["id"] != judgement["id"]
            or latest["verdict"] != "approve"
        ):
            fail("a spec amendment needs the proposal's latest approved judgement")
    # Reserve the id and validate references while holding the same writer lock
    # used by append. This keeps two role sessions from minting one id.
    record = {
        "version": 1,
        "kind": args.kind,
        "id": identifier,
        "task": task,
        "created_at": now(),
        "actor": args.actor,
        **payload,
    }
    write_record(path, record)
    print(json.dumps({"task": task, "kind": args.kind, "id": identifier}, indent=2))


def latest_judgement(records, subject):
    found = [
        record
        for record in records
        if record.get("kind") == "judgement" and record.get("subject") == subject
    ]
    return found[-1] if found else None


def approved_plan(records, identifier):
    judgement = latest_judgement(records, f"plan:{identifier}")
    if not judgement or judgement.get("verdict") != "approve":
        fail(f"plan {identifier!r} has no approved judgement")
    if judgement.get("spec_violation") != "none":
        fail(f"plan {identifier!r} was not cleared against the spec")
    amendments = [
        record
        for record in records
        if record.get("kind") == "spec_amendment" and record.get("user_approved")
    ]
    if amendments:
        amendment = amendments[-1]
        plan = find_record(records, "plan", identifier)
        if amendment["created_at"] > plan["created_at"]:
            fail("the spec changed after this plan; advisor must write a new plan")
        if amendment["created_at"] > judgement["created_at"]:
            fail(
                "the spec changed after this approval; justice must review the new plan"
            )
    return judgement


def cmd_dispatch(args):
    task = checked_name(args.task, "task")
    initial, records = read_task(task, args.home)
    judgement = approved_plan(records, args.plan)
    session = initial["roles"]["premier"]["session"]
    message = (
        f"Polity task {task}: implement plan {args.plan}. "
        f"Justice approved it in judgement {judgement['id']}. "
        "Read the plan from the task records before dispatching subagents. "
        "Review all work and submit a report referencing this plan. "
        "Do not expand its scope or amend the spec. "
        "Two rules for landing, because a checkout's branch is not yours to assume: "
        "read `git -C <checkout> rev-parse --abbrev-ref HEAD` first and land only on "
        "the branch the plan names -- if that checkout is on another branch, land "
        "from a temporary worktree instead of moving a live one; and stage by path, "
        "so another session's uncommitted work is never carried by your commit."
    )
    cli("send_message", "--name", session, "--message", message)
    print(json.dumps({"task": task, "plan": args.plan, "premier": session}, indent=2))


def cmd_status(args):
    task = checked_name(args.task, "task")
    initial, records = read_task(task, args.home)
    documents = [record for record in records if record["kind"] in RECORDS]
    summary = {
        kind: [record["id"] for record in documents if record["kind"] == kind]
        for kind in RECORDS
    }
    print(
        json.dumps(
            {
                "task": task,
                "goal": initial["goal"],
                "spec": initial["spec"],
                "roles": initial["roles"],
                "records": summary,
                "open_requirements": open_requirements(records),
                "latest": documents[-1] if documents else None,
                "file": str(task_file(task, args.home)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="Gongga home; defaults to GONGGA_HOME")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser(
        "start", help="create one task and its managed sessions"
    )
    start.add_argument("--task", required=True)
    start.add_argument("--spec", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--cwd", default=".")
    start.add_argument(
        "--remote",
        help="Gongga remote whose machine holds the checkout; omit for this machine",
    )
    start.set_defaults(handler=cmd_start)

    record = commands.add_parser("record", help="validate and append a record")
    record.add_argument("kind", choices=RECORDS)
    record.add_argument("--task", required=True)
    record.add_argument("--id")
    record.add_argument(
        "--json", required=True, help="the record body as one JSON object"
    )
    record.add_argument(
        "--actor",
        required=True,
        choices=tuple(ROLES),
        help="role accepting responsibility",
    )
    record.set_defaults(handler=cmd_record)

    dispatch = commands.add_parser(
        "dispatch", help="release an approved plan to premier"
    )
    dispatch.add_argument("--task", required=True)
    dispatch.add_argument("--plan", required=True)
    dispatch.set_defaults(handler=cmd_dispatch)

    status = commands.add_parser("status", help="show task ownership and record ids")
    status.add_argument("--task", required=True)
    status.set_defaults(handler=cmd_status)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"polity: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
