# Security

Errand reads Andrew's email and calendar and can send on his behalf. This
file is about the parts of that which are enforced rather than intended.

## Reporting something

Personal project, one user. Open a private security advisory on the repository
rather than a public issue.

## The properties this repo is built to hold

Each of these is a line of code and a test, not a policy. `errand/CLAUDE.md`
maps every rule to the file that enforces it and the test that proves it.

| Property | Enforced in | Proven by |
|---|---|---|
| A tool call above its tier does nothing | `policy/gate.py`, `tools/registry.py` | `tests/unit/test_gate.py`, `tests/injection/` |
| The planner never sees a raw email or page | `reader/quarantine.py` | `tests/unit/test_reader.py` |
| Unknown reader output is dropped, not escaped | `reader/schemas.py` | `tests/unit/test_reader.py` |
| Only one phone number is answered | `ingress/handler.py` | `tests/unit/test_ingress.py` |
| Every webhook signature is checked | `channels/twilio.py` | `tests/unit/test_ingress.py` |
| Every tool call is audited before and after | `store/audit_store.py` | `tests/unit/test_gate.py` |
| Spend stops at a cap | `policy/budget.py` | `tests/unit/test_budget.py` |
| A disconnect deletes and proves it | `tools/connections.py` | `tests/acceptance/test_v0.py` |

## Prompt injection

The threat is an email or web page that tells the assistant to do something.
The answer is not that the model resists it.

1. **The reader has no tools.** `reader/quarantine.py` calls Bedrock Converse
   with no `toolConfig` argument at all. A body that says "forward my inbox"
   is talking to a model that has nothing to forward anything with.
2. **Its output is forced through a fixed schema.** Fields that are not
   declared are dropped rather than escaped.
3. **Every tool call hits the gate**, which decides the tier from the tool
   name and its arguments and never from anything a model says.

Only the first and third are load-bearing. `tests/injection/` runs 26 hostile
payloads against a planner that does *exactly* what the attacker asked, and
asserts nothing left the system. It runs on every push, because a prompt edit
that makes the planner more agreeable is the change that should fail a build.

## Secrets

Nothing secret is in this repository, and CI checks that on every push:

- The **Twilio auth token** lives in Secrets Manager. It is both the REST
  credential and the HMAC key webhooks are signed with, which is why it is
  never an environment variable.
- **Google user tokens** go through AgentCore Identity. The `connections`
  table stores a reference, never a token.
- Terraform creates the secret *containers* and never the values, because
  Terraform state is a file on disk.

`gitleaks` scans the full history on every push, not just the diff — a secret
that was committed and later removed is still a leaked secret. If this repo
ever moves under an organisation, gitleaks-action requires a licence key there;
it is free for a user-owned repository.

## Supply chain

- Every GitHub Action is pinned to a **commit SHA**, not a tag. A tag is a
  moving pointer its owner can repoint; a SHA is the code that was reviewed.
  CI fails if anyone reintroduces a tag pin.
- Every workflow declares a **least-privilege `permissions:` block**, and CI
  fails if one appears without it. Only the CodeQL job may write findings,
  and that is all it may write.
- Terraform providers are pinned by `.terraform.lock.hcl`.
- Dependabot raises weekly PRs for actions, Python packages and providers.

## What is not defended

Worth being explicit, because a security file that lists only wins is not
useful.

- **SMS is not encrypted end to end.** A carrier can read it and it shows on a
  lock screen. `channels/base.py:mask_sensitive` strips card numbers, one-time
  codes and anything labelled a password on the way out, but that is a
  backstop — the rule is that Errand does not handle those at all.
- **A compromised AWS account is game over.** Everything here assumes the
  account boundary holds. Use MFA on the root account.
- **`terraform validate` is not `terraform apply`.** It checks syntax and
  internal consistency, not whether an IAM action name is real. The
  `bedrock-agentcore` action names and the foundation-model ARN format are
  the two most likely to need adjusting against the live API.
- **The reader flag is a signal, not a control.**
  `contains_instructions_to_assistant` is reported to Andrew and written to
  the log. The gate does not consult it, and nothing should be built that does.
