# CLAUDE.md for Errand

Errand is Andrew's personal assistant agent. He messages it on Telegram; it completes tasks using his connected accounts. Single user. Not a product. Personal accounts only, never work data.

Read PLAN.md before any work. Build only the current phase. Do not start a later phase without Andrew saying so.

## Stack (fixed)

Python 3.12, Strands Agents, Amazon Bedrock, Terraform. Andrew's personal AWS account, us-east-1.
Hosting: Bedrock AgentCore Runtime (agent), AgentCore Memory, AgentCore Browser (v1), AgentCore Observability.
Ingress: API Gateway + Lambda for the Telegram webhook, SQS FIFO queue to the agent.
Storage: DynamoDB (on demand) and S3, both encrypted with a customer managed KMS key.
Voice notes: Amazon Transcribe. Search: a `search` tool interface, AgentCore web search as the first implementation.
Set CloudWatch log retention and trace sampling on day one.
Default model: Claude Haiku 4.5. Escalation model: Claude Sonnet 5. Look up current Bedrock model IDs in the Bedrock console or docs. Never hardcode a model ID from memory. Both IDs live in config.

## Hard rules

1. Approval is enforced in the tool layer. A tool call above its allowed tier returns PENDING_APPROVAL and does nothing. No prompt wording can bypass it. Unit test this.
2. The planner model never sees raw email bodies, raw web pages, or raw documents. A reader model with zero tools extracts them into a fixed JSON schema. Fields outside the schema are dropped.
3. Only Andrew's Telegram user ID is allowed. Every other sender gets no reply and one audit row.
4. Validate the Telegram webhook secret token header on every request. Reject on mismatch. Confirm the header name in the current Telegram Bot API docs.
5. Every tool call writes an audit row before and after it runs.
6. No secrets in code, images, or committed files. Telegram token and Google OAuth client secret live in Secrets Manager; Google user tokens go through AgentCore Identity. Every component uses its own least privilege IAM role. Never long lived access keys.
7. Disconnecting an account deletes every stored copy of its data and sends Andrew a deletion receipt listing what was deleted.
8. Monthly token budget cap in config. At 80 percent, message Andrew. At 100 percent, stop all model calls except replying that the cap was hit.
9. Telegram bot chats are not end to end encrypted. Never send passwords, full card numbers, or 2FA codes through the chat. Mask to last four.
10. Keep changes scoped. Fix the broken part, not the whole file.

## Action tiers

| Tier | Examples | Rule |
|---|---|---|
| 0 read | Read calendar, search inbox, web search | Runs |
| 1 draft | Draft email, draft invite | Runs, shows draft |
| 2 send | Send email, accept invite | Needs approval |
| 3 spend | Anything with a charge | Needs approval, amount echoed, per task cap |
| 4 irreversible | Cancel account, delete data, nonrefundable booking | Needs approval plus second confirm |

Standing rules (see PLAN.md) can pre approve tier 2 and 3 actions that match them. They never pre approve tier 4.

## Definition of done for any task

Tests pass, acceptance script for the phase passes, audit rows present, no secret in git history, README updated with how to run it.

---

## Where each hard rule is enforced

Added by the build, not part of the rules above. If you change one of these
files, the rule it carries is what you are changing.

| Rule | Code | Test |
|---|---|---|
| 1 | `policy/gate.py`, `tools/registry.py` | `tests/unit/test_gate.py`, `tests/injection/` |
| 2 | `reader/quarantine.py`, `tools/gmail.py:gmail_read` | `tests/unit/test_reader.py` |
| 3 | `ingress/handler.py:is_allowlisted` | `tests/unit/test_ingress.py` |
| 4 | `ingress/handler.py:_secret_ok` | `tests/unit/test_ingress.py` |
| 5 | `store/audit_store.py`, `tools/registry.py` | `tests/unit/test_gate.py` |
| 6 | `common/secrets.py`, `infra/secrets.tf`, `infra/iam.tf` | reviewed, not tested |
| 7 | `tools/connections.py`, `store/content_store.py` | `tests/acceptance/test_v0.py` |
| 8 | `policy/budget.py`, `agent/planner.py` | `tests/unit/test_budget.py` |
| 9 | `channels/base.py:mask_sensitive` | `tests/unit/test_channels.py` |
| 10 | — | — |

## Things that are easy to break and hard to notice

- **Do not give the reader model a tool config.** The `converse` call in
  `reader/quarantine.py` deliberately has no `toolConfig` argument. That
  absence is the isolation, not the prompt above it.
- **Do not call a tool implementation directly.** Everything goes through
  `tools.registry.call`. A new import of `gmail_send` from somewhere else
  bypasses the gate, whatever it is for.
- **Do not re-plan an approved action.** Approve executes the stored
  arguments, checked against a digest taken when the approval was created.
  Andrew approved an action, not an intention.
- **`callback_data` is capped at 64 bytes** by Telegram. `channels/telegram.py`
  encodes a short token and resolves it server side. Do not put a summary in
  there; it will fail with BUTTON_DATA_INVALID only for the long ones, which
  is the worst way to find out.
- **A standing rule can never pre-approve tier 4**, and never a tier 3 with no
  amount cap. `policy/rules.py:validate` refuses both.
- **Audit rows must not carry message bodies.** `policy/gate.py:_redact`
  replaces them with a length. Audit rows outlive the task.
- **The budget check runs before the model call, not after.** Checking
  afterwards means the cap is always exceeded by one call.
