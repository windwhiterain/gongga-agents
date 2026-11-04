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
- **One composed `subscribe`** carries a role's answer to justice. `start` arms a
  single pattern over both managed roles' `turn_end`; the notice names the session
  and carries the turn's `answer`, so a role ends its turn with the record id and
  the id reaches justice without a messenger.

## Role contracts

| Role | Tier | Responsibility | Must not |
|---|---|---|---|
| justice | pro / sota | Understand the user; judge plans against the SPEC; review proposals for the user; accept reports | Implement; approve a SPEC amendment on the user's behalf |
| advisor | sota | Investigate, plan, and write a proposal when the SPEC premise or clauses must change | Implement; approve its own plan |
| premier | flash | Dispatch subagents only for an approved plan; review, integrate, verify, and report | Plan; expand scope; amend the SPEC |

Implementation subagents use flash, or pro for hard work. Draw concrete models through
`@skill:model_pool`; never hardcode a provider/model. Justice may raise its own tier
from pro to sota for a difficult task.

The `actor` in a record names the role accepting responsibility. The helper validates
the protocol, but only justice makes a SPEC judgement and only the user approves an
amendment. A message claiming another role approved something is not a judgement.

## Workflow

```text
user <-> justice
           | start: create advisor + premier, subscribe justice to their turn ends,
           |        and give the goal to advisor
           |-- advisor -> plan -> (turn_end notice carries the plan id to justice)
           |                    -> justice judgement
           |                    |-- approve + none -> dispatch -> premier
           |                    `-- revise/reject -> advisor revises
           |-- premier -> implementation subagents -> report -> (turn_end notice)
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
# expands to (the `path` column of `index_skill` names the same file).
# In gongga, `gongga-cli skill <name> ...` runs the entry the frontmatter declares.
SKILL_DIR=<the directory this SKILL.md is in>
P="$SKILL_DIR/polity"
$P start --task <task_id> --spec /absolute/path/to/SPEC.md \
  --goal '<user goal and constraints>' --cwd /absolute/path/to/project
```

A task ID contains only letters, digits, dots, underscores, and dashes. Run `start`
**from the justice session**: the session that runs it becomes justice, because
`subscribe` notices the caller and no flag can arm another session's ears. `start`
creates advisor and premier, draws each model through `model_pool` (advisor sota,
premier flash), then arms justice's watch before anyone is briefed — a role nobody
would hear from is worse than no role, so a watch that fails to arm undoes the
sessions it created.

The watch is one composed regex, the same language `del_bash` speaks:

```bash
gongga-cli subscribe polity-<task_id> \
  '^turn_end:polity-<task_id>-(advisor|premier)$' --persistant true
```

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

### 1. Advisor writes a plan

```bash
$P record plan --task <task_id> --actor advisor --json '{
  "spec_refs": ["SPEC.md: Skill: Polity"],
  "steps": ["Ordered implementation steps"],
  "acceptance": ["Observable and verifiable acceptance criteria"]
}'
```

End the turn with the plan ID and a short summary as the answer: the watch lands it in
justice's transcript as an ordinary message, e.g.
`subscribe \`polity-<task_id>\` matched \`turn_end\` about polity-<task_id>-advisor`
followed by the log row with the turn's `answer`. Justice reads the original record
and the SPEC, not just the summary. If the SPEC must change, advisor writes a proposal
with exactly the five fields required by the SPEC:

```bash
$P record proposal --task <task_id> --actor advisor --json '{
  "example": "A concrete triggering example",
  "expected": "Expected behavior",
  "actual": "Current behavior",
  "actual_spec": "Current clause and its location",
  "expected_spec": "Proposed clause"
}'
```

### 2. Justice records a judgement

```bash
$P record judgement --task <task_id> --actor justice --json '{
  "subject": "plan:plan-1",
  "verdict": "approve",
  "spec_violation": "none",
  "spec_refs": ["SPEC.md: relevant clause"],
  "reason": "Why the subject conforms or does not conform"
}'
```

The subject is `proposal:<id>`, `plan:<id>`, or `report:<id>`. Violation meanings:

| `spec_violation` | Meaning | Consequence |
|---|---|---|
| `none` | Conforms to the current SPEC | Plan may be approved; report may be accepted |
| `premise` | The current premise is insufficient; clarify or amend the SPEC | Plan cannot be approved; advisor writes a proposal |
| `direct` | Directly violates the current SPEC | Cannot be approved; rework or obtain a user-approved amendment first |

Justice approving a proposal means only that it is suitable to present to the user.
Present its example, expected/actual behavior, and current/proposed clauses. Silence,
ambiguity, or refusal never authorizes a SPEC edit.

### 3. Dispatch only an approved plan

```bash
$P dispatch --task <task_id> --plan plan-1
```

The helper requires the plan's latest judgement to be `approve + none`, then sends the
handoff to premier. Premier follows the `subagent` skill: self-contained briefs,
separate worktrees, explicit waits, and review. A draft plan cannot start implementation.
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

### 4. Record the user's SPEC decision

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
  drop the watch with `del_subscribe polity-<task_id>` once the task is archived.
- No automatic polling, automatic approval, treating a proposal as a plan, or secrets
  in records.
- Use the ordinary workflow for a simple task rather than creating sessions for one
  file read or a single-step fix.
