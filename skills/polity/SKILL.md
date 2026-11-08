---
name: polity
description: Separate understanding, planning, implementation, and approval across justice, advisor, and premier sessions. Use for multi-stage work that needs SPEC-based approval or a SPEC amendment; do not start the full workflow for a simple direct task.
metadata:
  entry: polity
---

# Polity: separate judgement from execution

Every role is an ordinary Gongga session, not a new server tool. The session talking
to the user is **justice**: it already holds the user's context, so do not create a
second justice that receives a paraphrase. Other roles read only the records they need
instead of copies of the whole conversation. The SPEC (`@file:SPEC.md`, in the repository
the work is in) is the authority; a role's opinion is not an amendment.

Two harness mechanisms carry what an extra role used to:

- **The server's monitor** repairs a session that stopped with an error: every
  unreported failure is handed to `@monitor`, which finds the harness- or quota-side
  cause, fixes it, and can switch the session's model. Polity keeps no watcher and
  no repair command of its own.
- **One `subscribe` per role** carries a role's answer to justice: `start` arms a
  pattern over that role's `turn_end` and a `send_message` command, so the news
  names the role that finished. The server does not inject a notice itself. The
  message names the session and carries the turn's `answer`, so a role ends its
  turn with the record id and the id reaches justice without a messenger.

## Role contracts

| Role | Tier | Responsibility | Must not |
|---|---|---|---|
| justice | pro / sota | Talk to the user and record their requirements; judge plans against them and the SPEC; review proposals; accept reports | Implement; approve a SPEC amendment on the user's behalf |
| advisor | sota | Investigate, plan, and write a proposal when the SPEC premise or clauses must change | Implement; approve its own plan |
| premier | flash | Dispatch subagents only for an approved plan; review, integrate, verify, and report | Plan; expand scope; amend the SPEC |

Implementation subagents use flash, or pro for hard work. Draw concrete models through
`@skill:model_pool`; never hardcode a provider/model. Justice may raise its own tier
from pro to sota for a difficult task.

That table is the default, not a hardcode: `POLITY_TIER=<tier>` draws every role at one
tier for the run, and `POLITY_TIER_<ROLE>` (`POLITY_TIER_ADVISOR`) moves a single one.
`start` prints each role's session and the model it drew, so the run records what it
actually spent. A run that names no variable gets the table above.

The `actor` in a record names the role accepting responsibility. The helper validates
the protocol, but only justice makes a SPEC judgement and only the user approves an
amendment. A message claiming another role approved something is not a judgement.

## Workflow

```text
user <-> justice
           | record: the user's requirements, so later judgements answer them
           | start: create advisor + premier, subscribe justice to their turn ends,
           |        and give the goal to advisor
           |-- advisor -> plan -> (turn_end callback carries the plan id to justice)
           |                    -> justice judgement
           |                    |-- approve + none -> dispatch -> premier
           |                    `-- revise/reject -> advisor revises
           |-- premier -> implementation subagents -> report -> (turn_end callback)
           |                                                   -> justice judgement
           |                                                   |-- approve -> deliver
           |                                                   `-- revise -> rework
           `-- advisor -> proposal -> justice judgement -> user
                                                         `-- explicit approval -> amendment
```

A role's turn that ends `failed` belongs to the monitor, not to the workflow: it is
handed the failure and its report says what it changed. Justice judges records; it
does not watch sessions. On a `Context notice`, every role immediately follows the
`heir` skill; its session name, task ID, and records stay the same.

## Start

```bash
# This skill's own directory: the parent of the `SKILL.md: <path>` that `@skill:`
# expands to (the `path` column of the `skill` tool's table names the same file).
# In gongga, `gongga-cli skill <name> ...` runs the entry the frontmatter declares.
SKILL_DIR=<the directory this SKILL.md is in>
P="$SKILL_DIR/polity"
$P start --task <task_id> --spec /absolute/path/to/SPEC.md \
  --goal '<user goal and constraints>' --cwd /absolute/path/to/project
```

`--cwd` and `--spec` name files on the machine the roles run on, and `--remote
<name>` makes that a Gongga remote's machine (`set_session --remote`), not this one.
With `--remote`, both paths are the peer's and are passed through untouched: this
machine's filesystem says nothing about them, so `start` neither resolves them nor
tests that the spec exists -- a local test would approve a different checkout. Omit
`--remote` and the roles work on this machine's files, and `start` resolves both and
refuses a spec that is not there.

A task ID contains only letters, digits, dots, underscores, and dashes. Run `start`
**from the justice session**: the subscription's action runs in the session that
armed it, and no flag can arm another session's interpreter. `start` creates
advisor and premier, draws each model through `model_pool` (advisor sota, premier
flash), then arms justice's watch before anyone is briefed — a role nobody would
hear from is worse than no role, so a watch that fails to arm undoes the sessions
it created.

The watch is one regex and one command per role:

```bash
gongga-cli subscribe '^turn_end:polity-<task_id>-advisor$' \
  '{"name": "send_message", "args": {"name": "<justice>", "message": "polity-<task_id>: advisor finished its turn"}}' \
  --subscription polity-<task_id>-advisor --session <justice> --persistant true
```

A match only runs that command, and the command is a server cmd: it carries no
event fields, so the news says which role spoke by which watch fired. The command
runs in the session that armed it, which is justice's -- no `notice` shell and no
`GONGGA_EVENT_*` variables are involved.

`turn_end` fires for failed turns too — its payload's `outcome` says which — and the
monitor is already repairing those, so the workflow waits on this one event kind.
Adding a role to a task means extending the group, not adding a watcher.

Each task has one append-only JSONL file: `$GONGGA_HOME/polity/<task_id>.jsonl`.
It records the workflow; it does not replace the SPEC, git, or transcripts as their
source of truth. `--home` overrides the home for an isolated environment.
`status --task <task_id>` lists roles, the watch, record IDs, and the latest record.
After a restart, resume from these records rather than repeating approvals.

## Records and handoffs

Pass JSON as one single-quoted shell argument; do not interpolate shell variables into
it. References must name existing records. Never rewrite a record: append a revision
with a new ID.

### 1. Justice records what the user asked for

Before a plan exists, the user's own requirements are written down. A requirement is
one sentence the user would recognise as theirs, plus where they said it:

```bash
$P record requirement --task <task_id> --actor justice --json '{
  "text": "All runs use flash",
  "source": "user, in the session that started this task"
}'
```

Only justice writes one, because only justice talks to the user. The record is what a
later judgement answers: `judgement-6` refusing work for breaking a clause is weaker
than refusing it for breaking the sentence the user actually wrote, and a requirement
that lives only in a transcript is re-argued every round.

A requirement stays open until a later requirement record names it in `closes`:

```bash
$P record requirement --task <task_id> --actor justice --json '{
  "text": "Superseded: the user replaced this with the SPEC amendment",
  "closes": "requirement-2"
}'
```

That record is the closure event and asks for nothing; `status` shows which
requirements are still open. **Every judgement then carries `requirements`, one entry
per open requirement, and the command refuses a judgement that misses one:**

```bash
$P record judgement --task <task_id> --actor justice --json '{
  "subject": "report:report-3",
  "verdict": "revise",
  "spec_violation": "none",
  "reason": "the benchmark ran on a synthetic script, not the real task",
  "verification": ["read the report's benchmark section"],
  "requirements": {"requirement-1": "met", "requirement-2": "unmet"}
}'
```

Each value is `met`, `unmet`, or `not-applicable`. `unmet` is not by itself grounds
for a verdict -- a report may be revised for one unmet requirement and approved once it
is met -- but it is the answer justice owes the user, and it survives the transcript.

### 2. Advisor writes a plan

```bash
$P record plan --task <task_id> --actor advisor --json '{
  "spec_refs": ["SPEC.md: Skill: Polity"],
  "steps": ["Ordered implementation steps"],
  "acceptance": ["Observable and verifiable acceptance criteria"]
}'
```

End the turn with the plan ID and a short summary as the answer: the callback lands
it in justice's transcript as an ordinary message beginning
`turn_end polity-<task_id>-advisor:`, followed by the event payload containing the
turn's `answer`. Justice reads the original record and the SPEC, not just the
summary. If the SPEC must change, advisor writes a proposal with exactly the five
fields required by the SPEC:

```bash
$P record proposal --task <task_id> --actor advisor --json '{
  "example": "A concrete triggering example",
  "expected": "Expected behavior",
  "actual": "Current behavior",
  "actual_spec": "Current clause and its location",
  "expected_spec": "Proposed clause"
}'
```

### 3. Justice records a judgement

```bash
$P record judgement --task <task_id> --actor justice --json '{
  "subject": "plan:plan-1",
  "verdict": "approve",
  "spec_violation": "none",
  "spec_refs": ["SPEC.md: relevant clause"],
  "reason": "Why the subject conforms or does not conform",
  "verification": ["the command justice ran", "what it printed"]
}'
```

The subject is `proposal:<id>`, `plan:<id>`, or `report:<id>`. A judgement on a
**plan** or a **report** must carry `verification`: the checks justice actually ran
and what they answered. `reason` says what justice thinks; `verification` is why
anyone should believe it, and a judgement without it is refused by the command, not
merely discouraged. A proposal is read rather than executed, so its five fields are
the evidence and no `verification` is asked for.

Violation meanings:

| `spec_violation` | Meaning | Consequence |
|---|---|---|
| `none` | Conforms to the current SPEC | Plan may be approved; report may be accepted |
| `premise` | The current premise is insufficient; clarify or amend the SPEC | Plan cannot be approved; advisor writes a proposal |
| `direct` | Directly violates the current SPEC | Cannot be approved; rework or obtain a user-approved amendment first |

A role's session is a fact that changes: a provider refuses a model, a session is
moved, a tier is raised. Record it when it changes, rather than leaving the task
record to say what `start` drew:

```bash
$P record session --task <task_id> --actor justice --json '{
  "role": "advisor",
  "session": "polity-<task_id>-advisor",
  "model": "provider/model",
  "remote": "build",
  "cwd": "/home/build/project",
  "note": "why it moved"
}'
```

`role` is `justice`, `advisor`, or `premier`; `remote` and `cwd` may be `null`, which
says this machine. `start` writes one such record per role it creates; the append-only
file then answers "what is running", and `status` stops being the only witness.

Justice approving a proposal means only that it is suitable to present to the user.
Present its example, expected/actual behavior, and current/proposed clauses. Silence,
ambiguity, or refusal never authorizes a SPEC edit.

### 4. Dispatch only an approved plan

```bash
$P dispatch --task <task_id> --plan plan-1
```

The helper requires the plan's latest judgement to be `approve + none`, then sends the
handoff to premier. Premier follows the `subagent` skill: self-contained briefs,
separate worktrees, explicit waits, and review. A draft plan cannot start implementation.

The handoff carries two landing rules, because a checkout's branch is not yours to
assume — one polity run landed two commits on another session's branch, because the
main checkout had been switched while justice was reading it: read `git -C <checkout>
rev-parse --abbrev-ref HEAD` before landing and land only on the branch the plan names;
if the checkout is on another branch, land from a temporary worktree rather than moving
a live one. And stage by path, so another session's uncommitted work is never carried
by a commit that is not its own.

Premier's first message at `start` only says to stand by: a role that is told to wait
needs one line, not a turn spent reading a plan that does not exist yet.
After integration, premier writes:

```bash
$P record report --task <task_id> --actor premier --json '{
  "plan": "plan-1",
  "changes": ["Actual changes, files, and commits"],
  "verification": ["Checks actually run and their results"],
  "deviations": [],
  "remaining": []
}'
```

Justice must approve the report before delivery. A `direct` violation or scope deviation
returns to advisor for replanning; incomplete implementation returns to premier for a
new report against the same approved plan.

### 5. Record the user's SPEC decision

```bash
$P record spec_amendment --task <task_id> --actor justice --json '{
  "ref_proposal": "proposal-1",
  "ref_judgement": "judgement-1",
  "user_approved": true,
  "user_decision": "The user decision, quoted or accurately summarized",
  "change": "The clause actually written to the SPEC"
}'
```

Only `user_approved: true` authorizes implementing the amendment. The referenced
judgement must be justice's approval of that proposal. Record refusal as `false` and
do not change the SPEC. After an approved amendment, advisor writes a new plan against
the revised SPEC; old plan approvals do not carry over.

## Completion and limits

- Justice reports the outcome, checks actually run, and unresolved issues to the user;
  premier saying "done" is not acceptance.
- Keep task JSONL. Delete implementation sessions according to `subagent`. Delete role
  sessions when recovery is no longer needed and no work is running, and let justice
  drop the watches with `del_subscription polity-<task_id>-advisor --session <justice>` once the task is archived.
- No automatic polling, automatic approval, treating a proposal as a plan, or secrets
  in records.
- Use the ordinary workflow for a simple task rather than creating sessions for one
  file read or a single-step fix.
