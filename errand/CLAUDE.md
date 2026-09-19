# CLAUDE.md for Errand

Errand is Andrew's personal assistant agent. He texts it; it completes tasks using his connected accounts. Single user. Not a product. Personal accounts only, never work data.

Read PLAN.md before any work. Build only the current phase. Do not start a later phase without Andrew saying so.

## Stack (fixed)

Python 3.12, Strands Agents, Amazon Bedrock, Terraform. Andrew's personal AWS account, us-east-2.

This originally said us-east-1, because Nova Sonic launched there and v2 voice needs it. Andrew moved it to us-east-2 after his account was already set up there, accepting that tradeoff. v2 will either call Sonic cross-region or use whatever speech model is in us-east-2 by then; either way it is a v2 problem, not a reason to keep the rest of the stack in a region he is not using.
Hosting: Bedrock AgentCore Runtime (agent), AgentCore Memory, AgentCore Gateway (tools), AgentCore Identity (outbound OAuth), AgentCore Browser (v1), AgentCore Observability.
Ingress: API Gateway + Lambda for the Twilio webhook, SQS FIFO queue to the agent.
Messaging: Twilio SMS, behind the `Channel` interface in `channels/`. Voice in v2.
Storage: DynamoDB (on demand) and S3, both encrypted with a customer managed KMS key.
Voice memos: Amazon Transcribe. Search: a `search` tool interface, AgentCore web search as the first implementation.
Set CloudWatch log retention and trace sampling on day one.
Default model: Claude Haiku 4.5. Escalation model: Claude Sonnet 5. Look up current Bedrock model IDs in the Bedrock console or docs. Never hardcode a model ID from memory. Both IDs live in config.

## Hard rules

1. Approval enforcement lives in the tool layer. A tool call classified above the allowed tier returns PENDING_APPROVAL and does nothing. The model cannot bypass it by wording. Unit test this.
2. The planner model never sees raw email bodies or raw web pages. It sees only JSON extracted by the reader model.
3. The reader model has zero tools and a fixed output schema. Anything outside the schema is dropped.
4. Only Andrew's mobile number is allowlisted. Every other inbound number gets no reply and an audit entry.
5. Validate the Twilio request signature on every webhook. Reject on failure.
6. Every tool call writes an audit row before and after execution.
7. No secrets in code or env files. Twilio auth token in Secrets Manager; Google OAuth through AgentCore Identity. Every component uses its own least privilege IAM role. Never long lived access keys.
8. Monthly budget cap in config. At 80 percent, message Andrew. At 100 percent, stop all model calls except replying that the cap was hit.
9. SMS is not encrypted end to end and shows up on a lock screen. Never send passwords, full card numbers, or 2FA codes. Mask to last four.
10. Disconnecting an account deletes every stored copy of its data and sends Andrew a deletion receipt listing what was deleted.
11. Keep changes scoped. Fix the broken part, not the whole file.

## Action tiers

| Tier | Examples | Rule |
|---|---|---|
| 0 read | Read calendar, search inbox, web search | Runs freely |
| 1 draft | Draft email, draft calendar invite | Runs freely, shows you the draft |
| 2 send | Send email, accept invite, text a third party | Needs "T# yes" |
| 3 spend | Any purchase or booking with a charge | Needs "T# yes" plus amount echoed back; per task cap |
| 4 irreversible | Cancel account, delete data, nonrefundable booking | Needs "T# yes" and a second confirm |

Standing rules can pre approve tier 2 and 3 actions that match them. They never pre approve tier 4.

Kill switch: texting "STOP ALL" halts every running task, revokes pending approvals, and replies with what was stopped.

## Where each rule is enforced

| Rule | Code | Test |
|---|---|---|
| 1 | `policy/gate.py`, `tools/registry.py` | `tests/unit/test_gate.py`, `tests/injection/` |
| 2 | `reader/quarantine.py`, `tools/gmail.py:gmail_read` | `tests/unit/test_reader.py` |
| 3 | `reader/schemas.py` | `tests/unit/test_reader.py` |
| 4 | `ingress/handler.py:is_allowlisted` | `tests/unit/test_ingress.py` |
| 5 | `channels/twilio.py:signature_is_valid` | `tests/unit/test_ingress.py` |
| 6 | `store/audit_store.py`, `tools/registry.py` | `tests/unit/test_gate.py` |
| 7 | `common/secrets.py`, `infra/secrets.tf`, `infra/iam.tf` | reviewed, not tested |
| 8 | `policy/budget.py`, `agent/planner.py` | `tests/unit/test_budget.py` |
| 9 | `channels/base.py:mask_sensitive` | `tests/unit/test_channels.py` |
| 10 | `tools/connections.py`, `store/content_store.py` | `tests/acceptance/test_v0.py` |
| 11 | — | — |

## Things that are easy to break and hard to notice

- **Do not give the reader model a tool config.** The `converse` call in
  `reader/quarantine.py` deliberately has no `toolConfig` argument. That
  absence is the isolation, not the prompt above it.
- **Do not call a tool implementation directly.** Everything goes through
  `tools.registry.call`. A new import of `gmail_send` from somewhere else
  bypasses the gate, whatever it is for.
- **Do not re-plan an approved action.** "T7 yes" executes the stored
  arguments, checked against a digest taken when the approval was created.
  Andrew approved an action, not an intention.
- **Do not widen the allowlist to a list.** Rule 4 says one number. A list is
  one merge away from a list with a mistake in it.
- **Do not hardcode a Bedrock model id.** `common/config.py` raises when one
  is missing, and that is correct behaviour.
- **The budget check runs before the model call, not after.** Checking
  afterwards means the cap is always exceeded by one call, and on an
  escalation to Sonnet that one call is the expensive one.
- **A standing rule can never pre-approve tier 4**, and never a tier 3 with no
  amount cap. `policy/rules.py:validate` refuses both.
- **Audit rows must not carry message bodies.** `policy/gate.py:_redact`
  replaces them with a length. Audit rows outlive the task.
- **Anything user-facing goes through `channels/`.** Nothing outside that
  package should mention Twilio. That is what makes WhatsApp a constructor
  argument and v2 voice a new file.
- **A Button's `reply` has to be a message that actually works.** SMS renders
  it verbatim as "Reply "T7 yes $42.50"", so a spend approval whose hint omits
  the amount sends Andrew straight into the refusal for not echoing it.

## Definition of done for any task

Tests pass, acceptance script for the phase passes, audit rows present, no secret in git history, README updated with how to run it.
