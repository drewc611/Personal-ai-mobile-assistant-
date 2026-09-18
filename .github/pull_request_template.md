## What this changes

<!-- One or two sentences. What is different afterwards. -->

## Why

<!-- The problem, not the patch. -->

## Checks

- [ ] `make check` passes (lint, terraform fmt and validate, all tests)
- [ ] `make build` is under the 50MB Lambda limit, if the artifact changed

## Rules touched

`errand/CLAUDE.md` lists eleven hard rules and where each is enforced. Tick
any this change goes near, and say in one line why it still holds.

- [ ] 1 — approval enforced in the tool layer
- [ ] 2 — the planner never sees raw bodies
- [ ] 3 — the reader has no tools and a fixed schema
- [ ] 4 — one allowlisted number
- [ ] 5 — webhook signature validated
- [ ] 6 — audit row before and after every tool call
- [ ] 7 — no secrets in code or env files
- [ ] 8 — budget cap
- [ ] 9 — nothing sensitive over SMS
- [ ] 10 — disconnect deletes and issues a receipt
- [ ] None of them

<!-- If a rule is ticked, say here why it still holds. -->
