# Errand

Andrew's personal text agent. Runs in his own AWS account on Bedrock. One
user, one phone number, no work data.

v0 is built: SMS in and out, Gmail and Google Calendar read and draft with
gated send, web search, task threads, approvals, standing rules, batched
approvals, a 60-second undo window, voice memos in, an audit log, and a
disconnect that deletes and issues a receipt.

## What this is for

Instinct is the reference point. Every design decision below answers a
reported failure of it rather than adding a feature it lacks.

| Instinct failure | What Errand does instead | Where |
|---|---|---|
| Email sent without approval | The tier is decided in `policy/gate.py` and enforced in `tools/registry.py`. The model never holds a reference to a tool implementation. | `policy/`, `tools/registry.py` |
| Data kept after disconnect | `disconnect gmail` revokes the token, deletes every cached row tagged with that connection, and texts back the counts. | `tools/connections.py` |
| Email prompt injection succeeded | Untrusted bytes only ever reach a model invoked with no tool configuration, whose output is forced through a fixed schema. | `reader/` |
| One crowded thread | Every job gets a task id. `T7 yes`, `T7 status`. | `store/tasks_store.py` |
| No model choice | A routing table keyed by task type, model ids from the environment. | `agent/router.py` |

## How a text moves through it

```
Twilio SMS
  -> API Gateway (POST /sms only, throttled)
  -> ingress Lambda        signature check, allowlist, nothing else
  -> SQS FIFO              one message group, so order is guaranteed
  -> dispatcher Lambda     parses commands; free text goes to the agent
     -> AgentCore Runtime  Strands agent + tools
        -> policy gate     tier 0/1 run, tier 2+ return PENDING_APPROVAL
        -> reader model    untrusted content, no tools, fixed schema
  -> Twilio SMS reply
```

An approved action does not go out immediately. It lands in an outbox with a
release time; the releaser Lambda runs once a minute and sends whatever is
past its window. That is the undo.

## What Andrew can text

| Text | What happens |
|---|---|
| anything else | starts a task, replies with a task id |
| a voice memo | transcribed, then treated exactly like a typed message |
| `T7 yes` | approves T7; it goes out in 60 seconds |
| `T7 yes $42.50` | approves a charge, amount echoed back and checked against the cap |
| `T7 no` | drops it |
| `T7 status` | one task's state |
| `T7 undo` / `undo` | pulls it back if the window is still open |
| `tasks` | open tasks |
| `pending` | the numbered list that `yes 1,3` indexes into |
| `yes all` / `yes 1,3` | batch approval |
| `rules` | standing rules |
| `tighten dining` | drops the allow rules for a domain; deny rules stay |
| `STOP ALL` | halts everything and says what it stopped |
| `disconnect gmail` | revokes, deletes, and texts a receipt with counts |

Anything the parser does not recognise becomes a new task rather than an
action. `T7 yes but change the subject` is not an approval, because the
arguments Andrew approved are not the arguments he just described.

## Running the tests

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest errand/tests
```

159 tests, no AWS credentials needed. The interesting ones:

- `errand/tests/injection/` runs 26 hostile payloads against a planner that
  does exactly what the attacker asked, and asserts nothing left the system.
  It runs on every push, not nightly: a prompt edit that makes the planner
  more compliant should fail the build.
- `errand/tests/acceptance/test_v0.py` is the v0 acceptance criteria, one test
  each, webhook to reply.

## Deploying

```bash
cd errand && make build          # -> build/errand.zip
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill it in
terraform init && terraform apply
```

Then, in this order:

1. Put the Twilio credentials in Secrets Manager. Terraform creates the secret
   container but never the value, because Terraform state is a file on disk.
   ```bash
   aws secretsmanager put-secret-value --secret-id errand/twilio \
     --secret-string '{"account_sid":"AC...","auth_token":"..."}'
   ```
2. Set the `webhook_url` output as the messaging webhook on the Twilio number.
3. Connect Google through AgentCore Identity and set
   `ERRAND_IDENTITY_PROVIDER` on the dispatcher and runtime.
4. Look up the current Claude model ids in the Bedrock console for this
   account and region, and set `planner_model_id` and `reader_model_id`.
   There are no defaults and the code raises without them - a model id copied
   from documentation goes stale quietly, which is the worst way for it to go
   wrong.

Real texting is blocked until US A2P 10DLC registration completes. See the
open decisions below.

## Open decisions

These are Andrew's, and three of them shape code that is already written:

1. **SMS registration type.** Sole Proprietor applies only with no EIN; with
   an EIN (an LLC, for instance) it has to be Low Volume Standard or Standard.
   Sole Proprietor costs $4 once for the brand, $15 once for campaign vetting,
   and $2 a month, and the brand must be verified from a real mobile carrier
   number, not a VoIP line (Twilio, n.d.; Twilio, 2026).
2. **Tier 3 spend cap.** `tier3_cap_cents` defaults to zero, and zero means
   every tier 3 approval is refused with a message saying so. That is
   deliberate - the alternative is a default cap nobody chose.
3. **Payment method for v1.** Stored card per site, or a virtual card with
   merchant limits. Nothing in v0 depends on this, but the receipt format in
   v1 does.
4. **WhatsApp as a second v0 channel.** Not built. The command parser and the
   conversation handler are channel-agnostic; adding it is an ingress adapter
   and a sender, not a rewrite.

## Layout

```
errand/
  common/      config, ids, clock, money, secrets
  store/       DynamoDB access, tasks, audit, cached content, counters
  policy/      tiers, the gate, approvals, standing rules, the undo outbox
  reader/      the quarantined reader and its schemas
  tools/       gmail, calendar, search, tasks, rules, disconnect, the registry
  agent/       planner prompt, model router, Strands agent, AgentCore entrypoint
  ingress/     Twilio webhook Lambda and signature validation
  dispatcher/  command parsing, conversation handling, SMS, voice memos
  voice/       v2 only
  infra/       Terraform
  tests/       unit, injection, acceptance
  CLAUDE.md    the hard rules, and what is easy to break
```

## References

Amazon Web Services. (2025). *Announcing Amazon Nova Sonic, a new speech to speech model that brings real time voice conversations to Amazon Bedrock.* https://aws.amazon.com/about-aws/whats-new/2025/04/amazon-nova-sonic-speech-to-speech-conversations-bedrock/

Amazon Web Services. (n.d.). *Amazon Bedrock AgentCore documentation.* https://docs.aws.amazon.com/bedrock-agentcore/

Amazon Web Services. (n.d.). *Bedrock AgentCore starter toolkit* [Computer software]. GitHub. https://github.com/aws/bedrock-agentcore-starter-toolkit

Amazon Web Services. (n.d.). *Sample Amazon Nova Sonic Twilio integration* [Computer software]. GitHub. https://github.com/aws-samples/sample-amazon-nova-sonic-twilio-integration

AWS Labs. (n.d.). *AWS Bedrock AgentCore MCP server.* https://awslabs.github.io/mcp/servers/amazon-bedrock-agentcore-mcp-server

Carly. (2026). *What is Instinct AI? What early users actually found.* https://www.usecarly.com/blog/what-is-instinct-ai/

MLQ News. (2026). *Instinct is still invite only as its AI assistant takes broad access to users' data.* https://mlq.ai/news/instinct-is-still-invite-only-as-its-ai-assistant-takes-broad-access-to-users-data/

Strands Agents. (n.d.). *Python quickstart.* https://strandsagents.com/docs/user-guide/quickstart/python/

Strands Agents. (n.d.). *Python deployment to Amazon Bedrock AgentCore Runtime.* https://strandsagents.com/docs/user-guide/deploy/deploy_to_bedrock_agentcore/python/

Twilio. (2026). *A2P 10DLC: Gather the required business information.* https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/collect-business-info

Twilio. (n.d.). *A2P 10DLC Sole Proprietor brands FAQ.* https://support.twilio.com/hc/en-us/articles/9550596959643-A2P-10DLC-Sole-Proprietor-Brands-FAQ

Vellum. (2026). *Official Instinct breakdown.* https://www.vellum.ai/blog/official-instinct-breakdown
