# Errand: rules for anyone (or anything) changing this repo

Owner: Andrew Clark. Single user. Runs in Andrew's personal AWS account on
Bedrock. Not a product, not multi tenant, no work data ever.

## Hard rules

1. Approval enforcement lives in the tool layer. A tool call classified above
   the allowed tier returns PENDING_APPROVAL and does nothing. The model
   cannot bypass it by wording.
2. The planner model never sees raw email bodies or raw web pages. It sees
   only JSON extracted by the reader model.
3. The reader model has zero tools and a fixed output schema. Anything outside
   the schema is dropped.
4. Only Andrew's mobile number is allowlisted. Every other inbound number gets
   no reply and an audit entry.
5. Validate the Twilio request signature on every webhook. Reject on failure.
6. Every tool call writes an audit row before and after execution.
7. No secrets in code or env files. Twilio auth token in Secrets Manager;
   Google OAuth through AgentCore Identity.

## Action tiers

| Tier | Examples | Rule |
|---|---|---|
| 0 read | Read calendar, search inbox, web search | Runs freely |
| 1 draft | Draft email, draft calendar invite | Runs freely, shows you the draft |
| 2 send | Send email, accept invite, text a third party | Needs "T# yes" |
| 3 spend | Any purchase or booking with a charge | Needs "T# yes" plus amount echoed back; per task cap |
| 4 irreversible | Cancel account, delete data, nonrefundable booking | Needs "T# yes" and a second confirm |

Kill switch: texting "STOP ALL" halts every running task, revokes pending
approvals, and replies with what was stopped.

## Where each rule is enforced

| Rule | Code | Test |
|---|---|---|
| 1 | `policy/gate.py`, `tools/registry.py` | `tests/unit/test_gate.py`, `tests/injection/` |
| 2 | `tools/gmail.py:gmail_read`, `tools/search.py:web_read` | `tests/unit/test_reader.py` |
| 3 | `reader/quarantine.py`, `reader/schemas.py` | `tests/unit/test_reader.py` |
| 4 | `ingress/handler.py:is_allowlisted` | `tests/unit/test_ingress.py` |
| 5 | `ingress/twilio_signature.py` | `tests/unit/test_ingress.py` |
| 6 | `store/audit_store.py`, `tools/registry.py` | `tests/unit/test_gate.py` |
| 7 | `common/secrets.py`, `infra/secrets.tf` | reviewed, not tested |

## Things that are easy to break and hard to notice

- **Do not give the reader model a tool config.** The `converse` call in
  `reader/quarantine.py` deliberately has no `toolConfig` argument. That
  absence is the isolation. A future "just let it look one thing up" undoes
  rule 3 completely.
- **Do not let a tool implementation be called directly.** Everything goes
  through `tools.registry.call`. A direct import of `gmail_send` from
  somewhere new is a bypass of the gate, whatever it is for.
- **Do not widen the allowlist to a list.** Rule 4 says one number. A list is
  one merge away from a list with a mistake in it.
- **Do not re-plan an approved action.** When Andrew says "T7 yes", the stored
  arguments execute, checked against the digest recorded at approval time. Do
  not re-run the model to regenerate them - he approved an action, not an
  intention.
- **Do not hardcode a Bedrock model id.** Look the current id up in the
  Bedrock console and set `ERRAND_PLANNER_MODEL_ID` and
  `ERRAND_READER_MODEL_ID`. `common/config.py` raises if they are missing,
  and that is correct behaviour.
- **A standing rule can never pre-approve tier 4**, and never a tier 3 without
  an amount cap. `policy/rules.py:validate` refuses both.
- **Audit rows must not carry message bodies.** `policy/gate.py:_redact`
  replaces them with a length. Audit rows outlive the task.

## Phases

v0 (built): SMS in and out, Calendar and Gmail read/draft/gated send, web
search, task threads, approvals, standing rules, batched approvals, the undo
window, voice memos in, audit, deletion.

v1: AgentCore Browser, recorded recipes, API-before-browser fallback order,
preview-before-submit screenshots, receipts, watchers, money leak radar.

v2: voice. Nova Sonic over a Twilio websocket. Outbound calls to businesses
and negotiation playbooks need a legal check on recording consent and AI
disclosure first, and Andrew's sign-off, before any of it is built.

v3: model router per task type, replay harness, earned autonomy (needs the
per-task-type outcome history v0 is already collecting in `tasks_store`).
