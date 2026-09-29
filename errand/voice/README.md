# v2 only

Empty on purpose. PLAN.md puts voice in v2 and CLAUDE.md says not to start a
later phase without Andrew saying so.

When it does get built it arrives through the `Channel` interface in
`channels/`, not around it. Twilio voice becomes another implementation; the
gate, the approvals, the audit log and the conversation handler should need no
change. That is the test of whether the interface was drawn in the right place.

Step 1 is calls with Andrew only: Twilio voice streams call audio over a
websocket to Nova Sonic through Bedrock's bidirectional API, with tool calls
routed to the same gate. AWS publishes a sample of this exact integration.

Step 2 — outbound calls where the agent speaks to a business — is blocked until
Andrew signs off on a legal check covering call recording consent and AI
disclosure for both his state and the callee's.

"Hold for me", where the agent waits on hold and bridges Andrew in when a human
answers, is the least legally complicated of these because Andrew is the one on
the call. It is still a recorded outbound call, so it still waits.
