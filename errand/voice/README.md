# v2 only

Nothing here yet, and nothing should be built here until two things are true.

**Step 1, calls with Andrew only.** Twilio voice streams call audio over a
websocket to Nova Sonic through Bedrock's bidirectional API, with tool calls
routed to the same gate as everything else. AWS publishes a sample of this
exact integration (Amazon Web Services, n.d.).

**Step 2, outbound calls to businesses.** Bill negotiation, appointments,
"hold for me". This needs a legal check on call recording consent and AI
disclosure for both the caller's and the callee's states, and Andrew's
sign-off on that check, before any code is written. Do not build step 2
without it.

"Hold for me" is the least legally complicated of the voice ideas: Errand
waits on hold and connects Andrew when a human picks up, so Andrew is the one
on the call. It is still a recorded outbound call, so it still waits for the
check.
