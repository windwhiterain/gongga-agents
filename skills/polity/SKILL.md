---
name: polity
description: Separate understanding, planning, implementation, approval, and harness operations across justice, advisor, premier, and HR sessions. Use for multi-stage work that needs SPEC-based approval or a SPEC amendment; do not start the full workflow for a simple direct task.
---

# Polity: separate judgement from execution

Every role is an ordinary Gongga session, not a new server tool. The session talking
to the user is **justice**: it already holds the user's context, so do not create a
second justice that receives a paraphrase. Other roles read only the records they need
instead of copies of the whole conversation. The SPEC (`@file:SPEC.md`, in the repository
the work is in) is the authority; a role's opinion is not an amendment.

## Role contracts

| Role | Tier | Responsibility | Must not |
|---|---|---|---|
| justice | pro / sota | Understand the user; judge plans against the SPEC; review proposals for the user; accept reports | Implement; approve a SPEC amendment on the user's behalf |
| advisor | sota | Investigate, plan, and write a proposal when the SPEC premise or clauses must change | Implement; approve its own plan |
| premier | flash | Dispatch subagents only for an approved plan; review, integrate, verify, and report | Plan; expand scope; amend the SPEC |
| HR | flash | Create and recover role sessions; monitor health; repair quota and harness failures | Make technical or SPEC judgements |

Implementation subagents use flash, or pro for hard work. Draw concrete models through
`@skill:model_pool`; never hardcode a provider/model. Justice may raise its own tier from pro
to sota for a difficult task.

The `actor` in a record names the role accepting responsibility. The helper validates
the protocol, but only justice makes a SPEC judgement and only the user approves an
amendment. A message claiming another role approved something is not a judgement.

## Workflow

```text
user <-> justice
           | start: create HR / advisor / premier; give the goal to advisor
           |-- advisor -> plan -> justice judgement
           |                    |-- approve + none -> dispatch -> premier
           |                    `-- revise/reject -> advisor revises
           |-- premier -> implementation subagents -> report -> justice judgement
           |                                                   |-- approve -> deliver
           |                                                   `-- revise -> rework
           `-- advisor -> proposal -> justice judgement -> user
                                                         `-- explicit approval -> amendment
```

HR checks sessions on demand. It does not run a timer or relay substantive work. On a
`Context notice`, every role immediately follows the `heir` skill; its session name,
task ID, and records stay the same.

## Start

```bash
P=~/.agents/skills/polity/polity
$P start --task <task_id> --spec /absolute/path/to/SPEC.md \
  --goal '<user goal and constraints>' --cwd /absolute/path/to/project
```

A task ID contains only letters, digits, dots, underscores, and dashes. The current
`$GONGGA_SESSION` becomes justice; pass `--justice <session>` when starting elsewhere.
`start` gives the three managed roles self-contained briefs. Advisor starts planning;
premier waits for an approved plan.

Each task has one append-only JSONL file: `$GONGGA_HOME/polity/<task_id>.jsonl`.
It records the workflow; it does not replace the SPEC, git, or transcripts as their
source of truth. `--home` overrides the home for an isolated environment.
`status --task <task_id>` lists roles, record IDs, and the latest record. After a restart,
resume from these records rather than repeating approvals.

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

Send the plan ID and a short summary to justice. Justice reads the original record and
the SPEC, not just the summary. If the SPEC must change, advisor writes a proposal with
exactly the five fields required by the SPEC:

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

## HR failure handling

HR uses `get_messages` and read-only session state to inspect stalls, 403/429 responses,
quota failures, and harness errors. For a quota failure, preserve the transcript and
replace only the model:

```bash
$P remodel --task <task_id> --role premier \
  --old-model '<provider/model>' --reason 'quota exhausted'
```

This marks the old route through `model_pool mark`, draws the same tier, and applies
`set_session`. A difficult implementation subagent uses pro; premier stays flash.
For other harness failures, follow `self-control`, `heir`, or `evolve`. Do not delete a
transcript to hide an error or let HR make a role's technical decision.

## Completion and limits

- Justice reports the outcome, checks actually run, and unresolved issues to the user;
  premier saying "done" is not acceptance.
- Keep task JSONL. Delete implementation sessions according to `subagent`. Delete role
  sessions only when recovery is no longer needed and no work is running.
- No automatic polling, automatic approval, treating a proposal as a plan, or secrets
  in records.
- Use the ordinary workflow for a simple task rather than creating sessions for one
  file read or a single-step fix.
