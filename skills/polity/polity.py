#!/usr/bin/env python3
"""Run the polity workflow without putting decisions in the harness.

The current session is justice. Roles are ordinary Gongga sessions; records are
append-only JSON documents. Only a justice approval can release a plan, and
only an explicit user approval can amend a spec.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROLES = {"justice": "pro", "advisor": "sota", "premier": "flash", "hr": "flash"}
ROLE_TITLES = {"hr": "HR"}
RECORDS = ("proposal", "plan", "judgement", "report", "spec_amendment")
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
    elif kind == "report":
        string_list(payload, "changes")
        string_list(payload, "verification")
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
    return f"polity-{task}-{ROLE_TITLES.get(role, role)}"


def role_brief(task, role, spec):
    common = (
        f"You are {ROLE_TITLES.get(role, role)} for polity task {task}. "
        f"The authoritative spec is {spec}. Read it and the polity skill. "
        f"Records live in $GONGGA_HOME/polity/{task}.jsonl. "
        "Use the polity script for records; never invent an approval. "
        "Do not edit the spec, another role's records, or implementation files."
    )
    duties = {
        "advisor": (
            "Investigate and write one plan for the user's goal. Present its id to "
            "justice for review. If the spec itself is the obstacle, write a proposal "
            "instead. Do not implement or approve your own work."
        ),
        "premier": (
            "Wait for an approved plan id. Only then dispatch implementation to "
            "subagents (flash, pro for hard work), in separate worktrees, review the "
            "result, and write a report for justice. Never change the plan or spec."
        ),
        "hr": (
            "Create missing role sessions, inspect session health, repair quota or "
            "harness failures, and preserve records. You make no technical or spec "
            "decisions. The current session is justice; never create a replacement."
        ),
    }
    return common + " " + duties[role]


def create_role(task, role, cwd):
    session = session_name(task, role)
    model = draw_model(ROLES[role])
    cli("set_session", "--session", session, "--cwd", cwd, "--model", model)
    return {"session": session, "model": model}


def cmd_start(args):
    task = checked_name(args.task, "task")
    cwd = str(Path(args.cwd).expanduser().resolve())
    spec = str(Path(args.spec).expanduser().resolve())
    if not Path(spec).is_file():
        fail(f"spec does not exist: {spec}")
    if not args.goal.strip():
        fail("goal must not be empty")
    path = task_file(task, args.home)
    if path.exists():
        fail(f"task already exists: {path}; use status to resume it")
    justice = args.justice or os.environ.get("GONGGA_SESSION")
    if not justice:
        fail("pass --justice or run start from the justice session")
    roles = {}
    for role in ("hr", "advisor", "premier"):
        roles[role] = create_role(task, role, cwd)
    record = {
        "version": 1,
        "kind": "task",
        "id": task,
        "task": task,
        "created_at": now(),
        "goal": args.goal,
        "spec": spec,
        "cwd": cwd,
        "roles": {"justice": {"session": justice}, **roles},
        "state": "planning",
        "note": "state is descriptive; the records and their references are authoritative",
    }
    try:
        append_record(path, record)
        for role in roles:
            brief = role_brief(task, role, record["spec"])
            if role == "advisor":
                brief += f" The user's goal is: {args.goal}"
            cli("send_message", "--session", roles[role]["session"], "--message", brief)
    except Exception:
        # The failed start remains inspectable rather than pretending to be ready.
        if not path.exists():
            append_record(path, {**record, "state": "start_failed"})
        raise
    print(
        json.dumps(
            {"task": task, "state": "planning", "roles": record["roles"]}, indent=2
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
        "Do not expand its scope or amend the spec."
    )
    cli("send_message", "--session", session, "--message", message)
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
                "latest": documents[-1] if documents else None,
                "file": str(task_file(task, args.home)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_remodel(args):
    task = checked_name(args.task, "task")
    if args.role not in ROLES or args.role == "justice":
        fail("remodel one managed role: advisor, premier, or hr")
    initial, _ = read_task(task, args.home)
    session = initial["roles"][args.role]["session"]
    if args.old_model:
        done = subprocess.run(
            [*pool(), "mark", args.old_model, "--reason", args.reason],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            fail((done.stderr or done.stdout).strip() or "could not mark the old model")
    model = draw_model(ROLES[args.role])
    cli("set_session", "--session", session, "--model", model)
    print(json.dumps({"role": args.role, "session": session, "model": model}, indent=2))


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
    start.add_argument("--justice", help="current session; defaults to GONGGA_SESSION")
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

    remodel = commands.add_parser("remodel", help="HR: replace a role's failing model")
    remodel.add_argument("--task", required=True)
    remodel.add_argument("--role", required=True)
    remodel.add_argument(
        "--old-model", help="mark this quota-failing route before drawing"
    )
    remodel.add_argument("--reason", default="polity: quota failure")
    remodel.set_defaults(handler=cmd_remodel)
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
