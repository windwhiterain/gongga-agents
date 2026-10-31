---
name: polity
description: agents play certain roles to keep context focus.
---

roles: 
- justice: talk to the user, understand user.
  - judge if approve report about the implementation based on spec. 
  - judge if approve proposal and send to user for approval.
- premier: plan, orchestrate, spawn subagents, review.
  - report the implementation to the justice.
  - present proposal of changes to the spec.

record:
- proposal
- report
- judgement over proposal/report
- spec amendment
