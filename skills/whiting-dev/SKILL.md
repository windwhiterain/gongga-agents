---
name: whiting-dev
description: The user's standing development rules for work in their repositories — how to ask before deciding, how to use a worktree, when to commit or squash, what comments and tests may contain, which file writes are forbidden, and the code-taste and line-ending constraints. Read this before any coding, refactoring, testing, committing or design task in this user's code, and before answering how a piece of work should be done.
---

# Whiting dev rules

Standing rules the user wrote for their agents. They are not imported into every request
any more: read them when the task is development work in this machine's repositories.

## Superior

- Present a plan and ask for superior's approval unless the instruction is direct.
- When you can't tell which choice is better, or facing difficulty you can't handle, ask
  superior's idea with your analysis.
- Superior's instruction is not golden, there can be mistake or inconsistency, you can stop
  and point out.
- Change any Invariant/Safety is forbidden without permission.

## Worktree

- Each session works in its own worktree, each feature work in a feature branch in the
  session's worktree, parent can assign them to children.
- Default worktree path: `./.worktrees`.
- At each check point, merge the feature branch to main so other parallel work can keep
  sync.
- After the final merge, delete the feature branch.
- Parent deletes children's worktree.

## Temporal writes

- Writing to a temp location outside the project directory is forbidden.

## Network

- A CLI proxy tool `flm` is installed and has a whitelist for url.

## Comments

- Only write comments about invariant, contract or reference to docs.

## Context

- Never fear about context window, compaction will handle it.
- If the task is huge with multi-steps, dispatch subagent for atomic one and you review.
- If there are mechanical works, hand them to subagents.

## Tests

- Agents should not write tests without user's permission.
- Tests must be minimal for fast development.
- Tests (even unit tests) should live in separate files from sources.
- Tests inside a compile unit should be gathered in a single binary to save linking time.
- Never run full-scale test, only run tests that your edits has influenced.

## Commit

- If your change breaks more tests, find out why rather than revert it.
- Commit at each small step (even red) to save WIP, revert WIP is forbidden.
- Fix warning then format before commit. If you format other files beyond your edits, still
  commit them. No extra verification after format.
- Squash the small step commits into one then merge.

## Documentation

- Keep documentation synced; stale or rejected must be removed.
- Discussions and ideas should be recorded.
- User's verdicts, judgements, designs, visions should be recorded.

## Ask user

- Present each choice's pros and cons.

## Forward compatibility

- Do not consider forward compatibility unless superior ask for.

## Code taste

- Write docs and code in English.
- Do not use abbreviation or short form except well-known.
- Organize large file into multiple small one for sparse exploring.
- Prefer unification against variants, unconditional over special case, never duplicate code.
- Always choose O(1), at least O(log(n)), never O(n).

## Caution

- Line-ending might be mixed, can be tricky when you edit file via scripts.
